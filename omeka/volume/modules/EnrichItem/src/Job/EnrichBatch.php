<?php declare(strict_types=1);

namespace EnrichItem\Job;

use EnrichItem\Service\AnthropicClient;
use EnrichItem\Service\EnrichmentCache;
use Laminas\Http\Client as HttpClient;
use Omeka\Job\AbstractJob;

/**
 * Background job: enrich items via Claude + ingest into Qdrant.
 *
 * Args: ['item_ids' => [int, ...]]
 */
class EnrichBatch extends AbstractJob
{
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
        $services = $this->getServiceLocator();
        $api = $services->get('Omeka\ApiManager');
        $logger = $services->get('Omeka\Logger');
        $httpClient = $services->get('Omeka\HttpClient');
        $anthropicClient = $services->get(AnthropicClient::class);
        $cache = $services->get(EnrichmentCache::class);
        $config = $services->get('Config');
        $moduleConfig = $config['enrich_item'] ?? [];
        $baseUrl = rtrim($moduleConfig['clip_api_base_url'] ?? 'http://clip-api:8000', '/');
        $timeout = (int) ($moduleConfig['timeout'] ?? 120);

        $itemIds = $this->getArg('item_ids', []);
        $total = count($itemIds);
        $logger->info(sprintf('EnrichBatch: starting %d items', $total));

        foreach ($itemIds as $i => $itemId) {
            if ($this->shouldStop()) {
                $logger->info(sprintf('EnrichBatch: stopped at %d/%d', $i, $total));
                return;
            }

            try {
                $this->processItem($itemId, $api, $anthropicClient, $cache, $httpClient, $baseUrl, $timeout, $logger);
                $logger->info(sprintf('EnrichBatch: [%d/%d] item %d done', $i + 1, $total, $itemId));
            } catch (\Throwable $e) {
                $logger->err(sprintf('EnrichBatch: item %d failed: %s', $itemId, $e->getMessage()));
            }
        }

        $logger->info(sprintf('EnrichBatch: completed %d items', $total));
    }

    private function processItem(
        int $itemId,
        $api,
        AnthropicClient $anthropicClient,
        EnrichmentCache $cache,
        HttpClient $httpClient,
        string $baseUrl,
        int $timeout,
        $logger
    ): void {
        $itemRepr = $api->read('items', $itemId)->getContent();
        $itemJson = json_decode(json_encode($itemRepr), true);

        // Get media URL
        $media = $itemJson['o:media'] ?? [];
        if (empty($media)) {
            $logger->info(sprintf('EnrichBatch: item %d has no media, skipping', $itemId));
            return;
        }
        $mediaUrl = $media[0]['o:original_url'] ?? null;
        if (!$mediaUrl) {
            return;
        }
        $mediaUrl = preg_replace(
            '#https?://(?:localhost:\d+|catalog\.jonsarkin\.com)#',
            'http://omeka:80',
            $mediaUrl
        );

        // Skip if already enriched (has transcription)
        $hasTranscription = false;
        foreach (($itemJson['bibo:content'] ?? []) as $v) {
            if (!empty(trim($v['@value'] ?? ''))) {
                $hasTranscription = true;
                break;
            }
        }
        if ($hasTranscription) {
            $logger->info(sprintf('EnrichBatch: item %d already enriched, skipping', $itemId));
            return;
        }

        // Step 1: Enrich (check cache first, then Claude)
        $promptVersion = AnthropicClient::PROMPT_VERSION;
        $enrichment = $cache->get($itemId, $promptVersion);
        if (!$enrichment) {
            $enrichment = $anthropicClient->analyze($mediaUrl, 'haiku');
            unset($enrichment['usage']);
            $cache->put($itemId, $promptVersion, $enrichment);
        }

        // Step 2: Apply enrichment (only empty fields)
        $payload = $this->buildPatchPayload($itemJson, $enrichment);
        $api->update('items', $itemId, $payload);

        // Step 3: Ingest into search index (still via clip-api)
        $subjects = [];
        foreach (($itemJson['dcterms:subject'] ?? []) as $v) {
            $val = $v['@value'] ?? '';
            if ($val) {
                $subjects[] = $val;
            }
        }
        // Merge new motifs
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

        // Only set empty fields
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
