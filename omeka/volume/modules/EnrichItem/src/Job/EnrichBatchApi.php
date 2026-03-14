<?php declare(strict_types=1);

namespace EnrichItem\Job;

use EnrichItem\Service\AnthropicClient;
use EnrichItem\Service\EnrichmentCache;
use Laminas\Http\Client as HttpClient;
use Omeka\Job\AbstractJob;

/**
 * Background job: submit/collect Anthropic Batch API requests.
 *
 * Modes:
 *   submit  — download images, build batch, POST to Anthropic, save metadata
 *   collect — fetch completed results, cache, apply to items, ingest to Qdrant
 *
 * Args:
 *   mode: 'submit' | 'collect'
 *   item_ids: int[] (submit only)
 *   model: string (submit only, default 'haiku')
 *   batch_id: string (collect only)
 */
class EnrichBatchApi extends AbstractJob
{
    private const BATCH_API_URL = 'https://api.anthropic.com/v1/messages/batches';
    private const API_VERSION = '2023-06-01';
    private const MAX_TOKENS = 4096;
    private const BATCH_MAX_ITEMS = 500;

    private const FIELD_MAP = [
        'transcription'   => ['term' => 'bibo:content',              'property_id' => 91],
        'signature'        => ['term' => 'schema:distinguishingSign', 'property_id' => 476],
        'date'             => ['term' => 'dcterms:date',              'property_id' => 7],
        'medium'           => ['term' => 'dcterms:medium',            'property_id' => 26],
        'support'          => ['term' => 'schema:artworkSurface',     'property_id' => 931],
        'work_type'        => ['term' => 'dcterms:type',              'property_id' => 8],
        'motifs'           => ['term' => 'dcterms:subject',           'property_id' => 3],
        'condition_notes'  => ['term' => 'schema:itemCondition',      'property_id' => 1579],
    ];

    public function perform(): void
    {
        $mode = $this->getArg('mode', 'submit');
        if ($mode === 'submit') {
            $this->doSubmit();
        } elseif ($mode === 'collect') {
            $this->doCollect();
        }
    }

    private function doSubmit(): void
    {
        $services = $this->getServiceLocator();
        $api = $services->get('Omeka\ApiManager');
        $logger = $services->get('Omeka\Logger');
        $anthropicClient = $services->get(AnthropicClient::class);
        $cache = $services->get(EnrichmentCache::class);

        $itemIds = $this->getArg('item_ids', []);
        $model = $this->getArg('model', 'haiku');
        $modelId = AnthropicClient::MODEL_MAP[$model] ?? $model;
        $promptVersion = AnthropicClient::PROMPT_VERSION;

        // Filter out already-cached items
        $uncached = $cache->getUncachedItemIds($itemIds, $promptVersion);
        $logger->info(sprintf('EnrichBatchApi: %d items, %d uncached', count($itemIds), count($uncached)));

        if (empty($uncached)) {
            $logger->info('EnrichBatchApi: all items cached, nothing to submit');
            return;
        }

        // Build batch requests — download and encode each image
        $requests = [];
        foreach ($uncached as $itemId) {
            if ($this->shouldStop()) {
                $logger->info('EnrichBatchApi: stopped during image download');
                return;
            }

            try {
                $itemRepr = $api->read('items', $itemId)->getContent();
                $itemJson = json_decode(json_encode($itemRepr), true);

                $mediaUrl = $itemJson['o:media'][0]['o:original_url'] ?? null;
                if (!$mediaUrl) {
                    continue;
                }
                $mediaUrl = preg_replace(
                    '#https?://(?:localhost:\d+|catalog\.jonsarkin\.com)#',
                    'http://omeka:80',
                    $mediaUrl
                );

                [$b64Image, $mediaType] = $anthropicClient->downloadAndEncodeImage($mediaUrl);

                $requests[] = [
                    'custom_id' => (string) $itemId,
                    'params' => [
                        'model' => $modelId,
                        'max_tokens' => self::MAX_TOKENS,
                        'messages' => [
                            [
                                'role' => 'user',
                                'content' => [
                                    ['type' => 'image', 'source' => ['type' => 'base64', 'media_type' => $mediaType, 'data' => $b64Image]],
                                    ['type' => 'text', 'text' => $this->getAnalysisPrompt()],
                                ],
                            ],
                        ],
                    ],
                ];

                $logger->info(sprintf('EnrichBatchApi: prepared item %d (%d/%d)', $itemId, count($requests), count($uncached)));
            } catch (\Throwable $e) {
                $logger->err(sprintf('EnrichBatchApi: failed to prepare item %d: %s', $itemId, $e->getMessage()));
            }
        }

        if (empty($requests)) {
            $logger->info('EnrichBatchApi: no requests to submit');
            return;
        }

        // Submit in chunks
        $apiKey = getenv('ANTHROPIC_API_KEY');
        $httpClient = $services->get('Omeka\HttpClient');
        $conn = $services->get('Omeka\EntityManager')->getConnection();

        // Ensure batch meta table exists
        $conn->executeStatement("
            CREATE TABLE IF NOT EXISTS enrich_batch_meta (
                batch_id VARCHAR(64) NOT NULL PRIMARY KEY,
                model VARCHAR(32) NOT NULL,
                item_count INT NOT NULL,
                item_ids JSON NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'submitted',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                collected_at DATETIME NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ");

        $chunks = array_chunk($requests, self::BATCH_MAX_ITEMS);
        foreach ($chunks as $chunkIdx => $chunk) {
            $logger->info(sprintf('EnrichBatchApi: submitting chunk %d/%d (%d requests)', $chunkIdx + 1, count($chunks), count($chunk)));

            $client = clone $httpClient;
            $client->resetParameters(true);
            $client->setUri(self::BATCH_API_URL);
            $client->setMethod('POST');
            $client->setHeaders([
                'Content-Type' => 'application/json',
                'x-api-key' => $apiKey,
                'anthropic-version' => self::API_VERSION,
            ]);
            $client->setRawBody(json_encode(['requests' => $chunk]));
            $client->setOptions(['timeout' => 300]);

            $response = $client->send();
            if (!$response->isSuccess()) {
                $logger->err(sprintf('EnrichBatchApi: submit failed HTTP %d: %s', $response->getStatusCode(), substr($response->getBody(), 0, 500)));
                continue;
            }

            $result = json_decode($response->getBody(), true);
            $batchId = $result['id'] ?? '';
            if (!$batchId) {
                $logger->err('EnrichBatchApi: no batch ID in response');
                continue;
            }

            $chunkItemIds = array_map(fn($r) => (int) $r['custom_id'], $chunk);
            $conn->executeStatement(
                'INSERT INTO enrich_batch_meta (batch_id, model, item_count, item_ids, status, created_at) VALUES (?, ?, ?, ?, ?, NOW())',
                [$batchId, $model, count($chunk), json_encode($chunkItemIds), $result['processing_status'] ?? 'submitted']
            );

            $logger->info(sprintf('EnrichBatchApi: submitted batch %s (%d items)', $batchId, count($chunk)));
        }
    }

    private function doCollect(): void
    {
        $services = $this->getServiceLocator();
        $api = $services->get('Omeka\ApiManager');
        $logger = $services->get('Omeka\Logger');
        $httpClient = $services->get('Omeka\HttpClient');
        $anthropicClient = $services->get(AnthropicClient::class);
        $cache = $services->get(EnrichmentCache::class);
        $config = $services->get('Config');
        $moduleConfig = $config['enrich_item'] ?? [];
        $clipBaseUrl = rtrim($moduleConfig['clip_api_base_url'] ?? 'http://clip-api:8000', '/');
        $timeout = (int) ($moduleConfig['timeout'] ?? 120);

        $batchId = $this->getArg('batch_id', '');
        if (!$batchId) {
            $logger->err('EnrichBatchApi: no batch_id provided for collect');
            return;
        }

        $apiKey = getenv('ANTHROPIC_API_KEY');
        $promptVersion = AnthropicClient::PROMPT_VERSION;

        // Check batch status
        $client = clone $httpClient;
        $client->resetParameters(true);
        $client->setUri(self::BATCH_API_URL . '/' . $batchId);
        $client->setMethod('GET');
        $client->setHeaders([
            'x-api-key' => $apiKey,
            'anthropic-version' => self::API_VERSION,
        ]);
        $client->setOptions(['timeout' => 30]);

        $response = $client->send();
        if (!$response->isSuccess()) {
            $logger->err(sprintf('EnrichBatchApi: status check failed for %s', $batchId));
            return;
        }

        $batchInfo = json_decode($response->getBody(), true);
        if (($batchInfo['processing_status'] ?? '') !== 'ended') {
            $logger->info(sprintf('EnrichBatchApi: batch %s still processing', $batchId));
            return;
        }

        // Fetch results
        $resultsUrl = $batchInfo['results_url'] ?? (self::BATCH_API_URL . '/' . $batchId . '/results');
        $client2 = clone $httpClient;
        $client2->resetParameters(true);
        $client2->setUri($resultsUrl);
        $client2->setMethod('GET');
        $client2->setHeaders([
            'x-api-key' => $apiKey,
            'anthropic-version' => self::API_VERSION,
        ]);
        $client2->setOptions(['timeout' => 300]);

        $response2 = $client2->send();
        if (!$response2->isSuccess()) {
            $logger->err(sprintf('EnrichBatchApi: results fetch failed for %s', $batchId));
            return;
        }

        // Parse JSONL results
        $lines = explode("\n", trim($response2->getBody()));
        $succeeded = 0;
        $errors = 0;

        foreach ($lines as $line) {
            $line = trim($line);
            if (!$line) {
                continue;
            }

            if ($this->shouldStop()) {
                $logger->info('EnrichBatchApi: stopped during collect');
                return;
            }

            $result = json_decode($line, true);
            if (!$result) {
                continue;
            }

            $itemId = (int) ($result['custom_id'] ?? 0);
            if (!$itemId) {
                continue;
            }

            $resultType = $result['result']['type'] ?? '';

            if ($resultType === 'succeeded') {
                $rawText = $result['result']['message']['content'][0]['text'] ?? '';
                $parsed = $anthropicClient->parseResponse($rawText);
                $enrichment = $anthropicClient->validateEnrichment($parsed);

                if (!empty($enrichment)) {
                    $cache->put($itemId, $promptVersion, $enrichment);
                    $succeeded++;

                    // Apply to item
                    try {
                        $itemRepr = $api->read('items', $itemId)->getContent();
                        $itemJson = json_decode(json_encode($itemRepr), true);
                        $payload = $this->buildPatchPayload($itemJson, $enrichment);
                        $api->update('items', $itemId, $payload);

                        // Ingest
                        $this->ingestItem($itemId, $itemJson, $enrichment, $httpClient, $clipBaseUrl, $timeout);
                    } catch (\Throwable $e) {
                        $logger->err(sprintf('EnrichBatchApi: apply/ingest failed for item %d: %s', $itemId, $e->getMessage()));
                    }
                } else {
                    $errors++;
                    $logger->warn(sprintf('EnrichBatchApi: empty parse result for item %d', $itemId));
                }
            } else {
                $errors++;
                $logger->warn(sprintf('EnrichBatchApi: item %d result type: %s', $itemId, $resultType));
            }
        }

        // Update batch meta
        $conn = $services->get('Omeka\EntityManager')->getConnection();
        try {
            $conn->executeStatement(
                'UPDATE enrich_batch_meta SET status = ?, collected_at = NOW() WHERE batch_id = ?',
                ['collected', $batchId]
            );
        } catch (\Throwable $e) {
            // Table might not exist if module was reinstalled
        }

        $logger->info(sprintf('EnrichBatchApi: collected batch %s — %d succeeded, %d errors', $batchId, $succeeded, $errors));
    }

    private function ingestItem(int $itemId, array $itemJson, array $enrichment, HttpClient $httpClient, string $baseUrl, int $timeout): void
    {
        $mediaUrl = $itemJson['o:media'][0]['o:original_url'] ?? null;
        if (!$mediaUrl) {
            return;
        }
        $mediaUrl = preg_replace(
            '#https?://(?:localhost:\d+|catalog\.jonsarkin\.com)#',
            'http://omeka:80',
            $mediaUrl
        );

        $subjects = [];
        foreach (($itemJson['dcterms:subject'] ?? []) as $v) {
            $val = $v['@value'] ?? '';
            if ($val) {
                $subjects[] = $val;
            }
        }
        foreach (($enrichment['motifs'] ?? []) as $motif) {
            if (!in_array($motif, $subjects, true)) {
                $subjects[] = $motif;
            }
        }

        $ingestBody = [
            'image_url' => $mediaUrl,
            'title' => $itemJson['o:title'] ?? '',
            'description' => ($itemJson['dcterms:description'][0]['@value'] ?? ''),
            'subjects' => $subjects,
            'omeka_url' => '/s/catalog/item/' . $itemId,
            'thumb_url' => $itemJson['o:thumbnail_urls']['square'] ?? '',
        ];

        $dateStr = $itemJson['dcterms:date'][0]['@value'] ?? ($enrichment['date'] ?? '');
        if (preg_match('/\b((?:19|20)\d{2})\b/', $dateStr, $m)) {
            $ingestBody['year'] = (int) $m[1];
        }

        $client = clone $httpClient;
        $client->resetParameters(true);
        $client->setUri($baseUrl . '/v1/ingest/' . $itemId);
        $client->setMethod('POST');
        $client->setHeaders(['Content-Type' => 'application/json', 'Accept' => 'application/json']);
        $client->setRawBody(json_encode($ingestBody));
        $client->setOptions(['timeout' => $timeout]);
        $client->send();
    }

    private function getAnalysisPrompt(): string
    {
        return AnthropicClient::ANALYSIS_PROMPT;
    }

    private function buildPatchPayload(array $item, array $enrichment): array
    {
        $payload = [];

        foreach ($item as $key => $val) {
            if (strpos($key, ':') !== false && strpos($key, 'o:') !== 0 && is_array($val)) {
                $payload[$key] = array_map(function ($v) {
                    $writeKeys = ['type', 'property_id', '@value', '@id', '@language', 'o:label', 'value_resource_id', 'uri', 'o:is_public'];
                    $clean = [];
                    foreach ($writeKeys as $k) {
                        if (array_key_exists($k, $v)) {
                            $clean[$k] = $v[$k];
                        }
                    }
                    return $clean;
                }, $val);
            }
        }

        foreach (['o:resource_class', 'o:item_set', 'o:media', 'o:is_public', 'o:site'] as $sysKey) {
            if (isset($item[$sysKey])) {
                $payload[$sysKey] = $item[$sysKey];
            }
        }

        foreach (self::FIELD_MAP as $field => $props) {
            $value = $enrichment[$field] ?? null;
            if ($value === null || $value === '' || (is_array($value) && empty($value))) {
                continue;
            }

            $term = $props['term'];
            $propId = $props['property_id'];

            $currentValues = $payload[$term] ?? [];
            $hasValue = false;
            foreach ($currentValues as $v) {
                if (!empty(trim($v['@value'] ?? ''))) {
                    $hasValue = true;
                    break;
                }
            }

            if (!$hasValue) {
                if ($field === 'motifs' && is_array($value)) {
                    $payload[$term] = array_map(function ($v) use ($propId) {
                        return ['type' => 'literal', 'property_id' => $propId, '@value' => $v];
                    }, $value);
                } else {
                    $payload[$term] = [
                        ['type' => 'literal', 'property_id' => $propId, '@value' => (string) $value],
                    ];
                }
            }
        }

        return $payload;
    }
}
