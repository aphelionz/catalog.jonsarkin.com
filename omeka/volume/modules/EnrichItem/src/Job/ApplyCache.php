<?php declare(strict_types=1);

namespace EnrichItem\Job;

use EnrichItem\Service\EnrichmentCache;
use Laminas\Http\Client as HttpClient;
use Omeka\Job\AbstractJob;

/**
 * Background job: re-apply cached enrichments to unenriched items.
 *
 * Used after `make pull` to restore enrichment data without API cost.
 * Also ingests each item into Qdrant.
 *
 * Args: ['prompt_version' => int]
 */
class ApplyCache extends AbstractJob
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
        $cache = $services->get(EnrichmentCache::class);
        $config = $services->get('Config');
        $moduleConfig = $config['enrich_item'] ?? [];
        $baseUrl = rtrim($moduleConfig['clip_api_base_url'] ?? 'http://clip-api:8000', '/');
        $timeout = (int) ($moduleConfig['timeout'] ?? 120);

        $promptVersion = $this->getArg('prompt_version', 1);
        $allCached = $cache->getAllForVersion($promptVersion);
        $total = count($allCached);
        $logger->info(sprintf('ApplyCache: %d cached entries for prompt version %d', $total, $promptVersion));

        $applied = 0;
        foreach ($allCached as $itemId => $enrichment) {
            if ($this->shouldStop()) {
                $logger->info(sprintf('ApplyCache: stopped at %d/%d', $applied, $total));
                return;
            }

            try {
                $itemRepr = $api->read('items', $itemId)->getContent();
            } catch (\Throwable $e) {
                // Item may have been deleted
                continue;
            }

            $itemJson = json_decode(json_encode($itemRepr), true);

            // Skip if already has transcription
            $hasTranscription = false;
            foreach (($itemJson['bibo:content'] ?? []) as $v) {
                if (!empty(trim($v['@value'] ?? ''))) {
                    $hasTranscription = true;
                    break;
                }
            }
            if ($hasTranscription) {
                continue;
            }

            // Apply enrichment
            $payload = $this->buildPatchPayload($itemJson, $enrichment);
            try {
                $api->update('items', $itemId, $payload);
                $applied++;
            } catch (\Throwable $e) {
                $logger->err(sprintf('ApplyCache: item %d patch failed: %s', $itemId, $e->getMessage()));
                continue;
            }

            // Ingest into search index
            $mediaUrl = $itemJson['o:media'][0]['o:original_url'] ?? null;
            if ($mediaUrl) {
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

                try {
                    $client = clone $httpClient;
                    $client->resetParameters(true);
                    $client->setUri($baseUrl . '/v1/ingest/' . $itemId);
                    $client->setMethod('POST');
                    $client->setHeaders(['Content-Type' => 'application/json', 'Accept' => 'application/json']);
                    $client->setRawBody(json_encode($ingestBody));
                    $client->setOptions(['timeout' => $timeout]);
                    $client->send();
                } catch (\Throwable $e) {
                    $logger->warn(sprintf('ApplyCache: ingest failed for item %d: %s', $itemId, $e->getMessage()));
                }
            }

            if ($applied % 50 === 0) {
                $logger->info(sprintf('ApplyCache: applied %d/%d', $applied, $total));
            }
        }

        $logger->info(sprintf('ApplyCache: completed, applied %d items', $applied));
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
