<?php declare(strict_types=1);

namespace EnrichItem\Job;

use EnrichItem\Service\AnthropicClient;
use EnrichItem\Service\EnrichmentCache;
use Omeka\Job\AbstractJob;

/**
 * Background job: submit/collect Anthropic Batch API requests for field-level enrichment.
 *
 * Modes:
 *   submit  — download images, build batch with per-field prompts, POST to Anthropic, save metadata
 *   collect — fetch completed results, cache, apply to items
 *
 * Args (submit):
 *   mode: 'submit'
 *   property_id: int
 *   term: string
 *   field_label: string
 *   instructions: string
 *   model: string
 *   vocab_terms: ?string[]
 *   item_ids: int[]
 *   force: bool
 *
 * Args (collect):
 *   mode: 'collect'
 *   batch_id: string
 */
class EnrichFieldBatchApi extends AbstractJob
{
    private const BATCH_API_URL = 'https://api.anthropic.com/v1/messages/batches';
    private const API_VERSION = '2023-06-01';
    private const MAX_TOKENS = 4096;
    private const BATCH_MAX_ITEMS = 500;

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

        $propertyId = (int) $this->getArg('property_id');
        $term = $this->getArg('term');
        $fieldLabel = $this->getArg('field_label');
        $instructions = $this->getArg('instructions');
        $model = $this->getArg('model', 'haiku');
        $vocabTerms = $this->getArg('vocab_terms');
        $itemIds = $this->getArg('item_ids', []);
        $force = (bool) $this->getArg('force', false);

        $modelId = AnthropicClient::MODEL_MAP[$model] ?? $model;

        $systemPrompt = AnthropicClient::buildSystemPrompt($fieldLabel, $instructions, $vocabTerms);
        $userPrompt = "Analyze this artwork and provide the value for: {$fieldLabel}";

        $logger->info(sprintf('EnrichFieldBatchApi: preparing %d items for field "%s"', count($itemIds), $fieldLabel));

        // Build batch requests
        $requests = [];
        foreach ($itemIds as $itemId) {
            if ($this->shouldStop()) {
                $logger->info('EnrichFieldBatchApi: stopped during image download');
                return;
            }

            try {
                $itemRepr = $api->read('items', (int) $itemId)->getContent();
                $itemJson = json_decode(json_encode($itemRepr), true);

                $mediaRefs = $itemJson['o:media'] ?? [];
                if (empty($mediaRefs)) {
                    continue;
                }
                $mediaId = $mediaRefs[0]['o:id'] ?? null;
                if (!$mediaId) {
                    continue;
                }
                $mediaRepr = $api->read('media', $mediaId)->getContent();
                $mediaJson = json_decode(json_encode($mediaRepr), true);
                $mediaUrl = $mediaJson['o:original_url'] ?? null;
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
                        'system' => $systemPrompt,
                        'messages' => [
                            [
                                'role' => 'user',
                                'content' => [
                                    ['type' => 'image', 'source' => ['type' => 'base64', 'media_type' => $mediaType, 'data' => $b64Image]],
                                    ['type' => 'text', 'text' => $userPrompt],
                                ],
                            ],
                        ],
                    ],
                ];

                $logger->info(sprintf('EnrichFieldBatchApi: prepared item %d (%d/%d)', $itemId, count($requests), count($itemIds)));
            } catch (\Throwable $e) {
                $logger->err(sprintf('EnrichFieldBatchApi: failed to prepare item %d: %s', $itemId, $e->getMessage()));
            }
        }

        if (empty($requests)) {
            $logger->info('EnrichFieldBatchApi: no requests to submit');
            return;
        }

        // Submit in chunks
        $apiKey = $anthropicClient->getApiKey();
        $httpClient = $services->get('Omeka\HttpClient');
        $conn = $services->get('Omeka\EntityManager')->getConnection();

        // Ensure batch meta table exists with property_id column
        $conn->executeStatement("
            CREATE TABLE IF NOT EXISTS enrich_batch_meta (
                batch_id VARCHAR(64) NOT NULL PRIMARY KEY,
                model VARCHAR(32) NOT NULL,
                item_count INT NOT NULL,
                item_ids JSON NOT NULL,
                property_id INT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'submitted',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                collected_at DATETIME NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ");

        // Add property_id column if missing (upgrade from old schema)
        try {
            $conn->executeStatement("ALTER TABLE enrich_batch_meta ADD COLUMN property_id INT NULL AFTER item_ids");
        } catch (\Throwable $e) {
            // Column already exists
        }

        $chunks = array_chunk($requests, self::BATCH_MAX_ITEMS);
        foreach ($chunks as $chunkIdx => $chunk) {
            $logger->info(sprintf('EnrichFieldBatchApi: submitting chunk %d/%d (%d requests)', $chunkIdx + 1, count($chunks), count($chunk)));

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
                $logger->err(sprintf('EnrichFieldBatchApi: submit failed HTTP %d: %s', $response->getStatusCode(), substr($response->getBody(), 0, 500)));
                continue;
            }

            $result = json_decode($response->getBody(), true);
            $batchId = $result['id'] ?? '';
            if (!$batchId) {
                $logger->err('EnrichFieldBatchApi: no batch ID in response');
                continue;
            }

            $chunkItemIds = array_map(fn($r) => (int) $r['custom_id'], $chunk);
            $conn->executeStatement(
                'INSERT INTO enrich_batch_meta (batch_id, model, item_count, item_ids, property_id, status, created_at) VALUES (?, ?, ?, ?, ?, ?, NOW())',
                [$batchId, $model, count($chunk), json_encode($chunkItemIds), $propertyId, $result['processing_status'] ?? 'submitted']
            );

            $logger->info(sprintf('EnrichFieldBatchApi: submitted batch %s (%d items, field "%s")', $batchId, count($chunk), $fieldLabel));
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

        $batchId = $this->getArg('batch_id', '');
        if (!$batchId) {
            $logger->err('EnrichFieldBatchApi: no batch_id provided for collect');
            return;
        }

        $apiKey = $anthropicClient->getApiKey();

        // Get batch metadata to know the property_id and term
        $conn = $services->get('Omeka\EntityManager')->getConnection();
        $batchMeta = $conn->fetchAssociative(
            'SELECT property_id, model FROM enrich_batch_meta WHERE batch_id = ?',
            [$batchId]
        );
        $propertyId = (int) ($batchMeta['property_id'] ?? 0);
        $model = $batchMeta['model'] ?? 'haiku';

        if (!$propertyId) {
            $logger->err('EnrichFieldBatchApi: no property_id in batch metadata');
            return;
        }

        // Look up the term for this property
        $propRow = $conn->fetchAssociative("
            SELECT CONCAT(v.prefix, ':', p.local_name) AS term
            FROM property p
            JOIN vocabulary v ON p.vocabulary_id = v.id
            WHERE p.id = ?
        ", [$propertyId]);
        $term = $propRow['term'] ?? '';

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
            $logger->err(sprintf('EnrichFieldBatchApi: status check failed for %s', $batchId));
            return;
        }

        $batchInfo = json_decode($response->getBody(), true);
        if (($batchInfo['processing_status'] ?? '') !== 'ended') {
            $logger->info(sprintf('EnrichFieldBatchApi: batch %s still processing', $batchId));
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
            $logger->err(sprintf('EnrichFieldBatchApi: results fetch failed for %s', $batchId));
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
                $logger->info('EnrichFieldBatchApi: stopped during collect');
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
                $rawText = trim($result['result']['message']['content'][0]['text'] ?? '');

                // Strip markdown fences
                if (str_starts_with($rawText, '```')) {
                    $textLines = explode("\n", $rawText);
                    $textLines = array_filter($textLines, fn($l) => !str_starts_with(trim($l), '```'));
                    $rawText = trim(implode("\n", $textLines));
                }

                // Treat NULL as empty
                if (strtoupper($rawText) === 'NULL') {
                    $rawText = '';
                }

                if ($rawText === '') {
                    continue;
                }

                // Cache
                $cache->put($itemId, $propertyId, $rawText, $model);

                // Apply to item
                try {
                    $this->applyValue($itemId, $propertyId, $term, $rawText, $api);
                    $succeeded++;
                } catch (\Throwable $e) {
                    $errors++;
                    $logger->err(sprintf('EnrichFieldBatchApi: apply failed for item %d: %s', $itemId, $e->getMessage()));
                }
            } else {
                $errors++;
                $logger->warn(sprintf('EnrichFieldBatchApi: item %d result type: %s', $itemId, $resultType));
            }
        }

        // Update batch meta
        try {
            $conn->executeStatement(
                'UPDATE enrich_batch_meta SET status = ?, collected_at = NOW() WHERE batch_id = ?',
                ['collected', $batchId]
            );
        } catch (\Throwable $e) {
            // Table might not exist
        }

        $logger->info(sprintf('EnrichFieldBatchApi: collected batch %s — %d succeeded, %d errors', $batchId, $succeeded, $errors));
    }

    private function applyValue(int $itemId, int $propertyId, string $term, string $value, $api): void
    {
        $itemRepr = $api->read('items', $itemId)->getContent();
        $itemJson = json_decode(json_encode($itemRepr), true);

        $payload = [];
        foreach ($itemJson as $key => $val) {
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

        foreach (['o:resource_class', 'o:resource_template', 'o:item_set', 'o:media', 'o:is_public', 'o:site'] as $sysKey) {
            if (isset($itemJson[$sysKey])) {
                $payload[$sysKey] = $itemJson[$sysKey];
            }
        }

        // Set the field value
        $decoded = json_decode($value, true);
        if (is_array($decoded)) {
            $payload[$term] = array_map(fn($v) => [
                'type' => 'literal',
                'property_id' => $propertyId,
                '@value' => (string) $v,
            ], $decoded);
        } else {
            $payload[$term] = [
                ['type' => 'literal', 'property_id' => $propertyId, '@value' => $value],
            ];
        }

        $api->update('items', $itemId, $payload);
    }
}
