<?php declare(strict_types=1);

namespace EnrichItem\Job;

use EnrichItem\Service\AnthropicClient;
use EnrichItem\Service\EnrichmentCache;
use Omeka\Job\AbstractJob;

/**
 * Background job: enrich one field across many items (real-time, one by one).
 *
 * Args:
 *   property_id: int
 *   term: string (e.g. 'dcterms:medium')
 *   field_label: string (e.g. 'Medium')
 *   instructions: string
 *   model: string (haiku/sonnet/opus)
 *   vocab_terms: ?string[]
 *   item_ids: int[]
 *   force: bool
 */
class EnrichFieldBatch extends AbstractJob
{
    public function perform(): void
    {
        $services = $this->getServiceLocator();
        $api = $services->get('Omeka\ApiManager');
        $logger = $services->get('Omeka\Logger');
        $anthropicClient = $services->get(AnthropicClient::class);
        $cache = $services->get(EnrichmentCache::class);

        $propertyId = (int) $this->getArg('property_id');
        $term = $this->getArg('term');
        $fieldLabel = $this->getArg('field_label');
        $instructions = $this->getArg('instructions');
        $model = $this->getArg('model', 'haiku');
        $vocabTerms = $this->getArg('vocab_terms');
        $itemIds = $this->getArg('item_ids', []);
        $force = (bool) $this->getArg('force', false);

        $total = count($itemIds);
        $logger->info(sprintf('EnrichFieldBatch: starting %d items for field "%s" (property %d, force=%s)',
            $total, $fieldLabel, $propertyId, $force ? 'yes' : 'no'));

        $systemPrompt = AnthropicClient::buildSystemPrompt($fieldLabel, $instructions, $vocabTerms);
        $userPrompt = "Analyze this artwork and provide the value for: {$fieldLabel}";

        $succeeded = 0;
        $errors = 0;

        foreach ($itemIds as $i => $itemId) {
            if ($this->shouldStop()) {
                $logger->info(sprintf('EnrichFieldBatch: stopped at %d/%d', $i, $total));
                return;
            }

            try {
                $this->processItem(
                    (int) $itemId, $propertyId, $term, $systemPrompt, $userPrompt,
                    $model, $vocabTerms, $force, $api, $anthropicClient, $cache, $logger
                );
                $succeeded++;
                if ($succeeded % 10 === 0) {
                    $logger->info(sprintf('EnrichFieldBatch: [%d/%d] %d succeeded, %d errors',
                        $i + 1, $total, $succeeded, $errors));
                }
            } catch (\Throwable $e) {
                $errors++;
                $logger->err(sprintf('EnrichFieldBatch: item %d failed: %s', $itemId, $e->getMessage()));
            }
        }

        $logger->info(sprintf('EnrichFieldBatch: completed — %d succeeded, %d errors out of %d',
            $succeeded, $errors, $total));
    }

    private function processItem(
        int $itemId,
        int $propertyId,
        string $term,
        string $systemPrompt,
        string $userPrompt,
        string $model,
        ?array $vocabTerms,
        bool $force,
        $api,
        AnthropicClient $anthropicClient,
        EnrichmentCache $cache,
        $logger
    ): void {
        $itemRepr = $api->read('items', $itemId)->getContent();
        $itemJson = json_decode(json_encode($itemRepr), true);

        // Get media URL
        $mediaRefs = $itemJson['o:media'] ?? [];
        if (empty($mediaRefs)) {
            return;
        }
        $mediaId = $mediaRefs[0]['o:id'] ?? null;
        if (!$mediaId) {
            return;
        }
        $mediaRepr = $api->read('media', $mediaId)->getContent();
        $mediaJson = json_decode(json_encode($mediaRepr), true);
        $mediaUrl = $mediaJson['o:original_url'] ?? null;
        if (!$mediaUrl) {
            return;
        }
        $mediaUrl = preg_replace(
            '#https?://(?:localhost:\d+|catalog\.jonsarkin\.com)#',
            'http://omeka:80',
            $mediaUrl
        );

        // Check cache first (unless force)
        if (!$force) {
            $cached = $cache->get($itemId, $propertyId);
            if ($cached !== null) {
                $this->applyValue($itemId, $itemJson, $propertyId, $term, $cached, $force, $api);
                return;
            }
        }

        // Call Claude
        $result = $anthropicClient->enrichField($mediaUrl, $systemPrompt, $userPrompt, $model);
        $value = $result['value'];

        if ($value === '') {
            $logger->info(sprintf('EnrichFieldBatch: item %d returned empty value, skipping', $itemId));
            return;
        }

        // Validate against vocab
        if (!empty($vocabTerms)) {
            if (!in_array($value, $vocabTerms, true)) {
                // Try case-insensitive match
                $matched = false;
                foreach ($vocabTerms as $vt) {
                    if (strcasecmp($vt, $value) === 0) {
                        $value = $vt;
                        $matched = true;
                        break;
                    }
                }
                if (!$matched) {
                    // Try JSON array for multi-value fields
                    $decoded = json_decode($value, true);
                    if (is_array($decoded)) {
                        $value = json_encode(array_values(array_filter($decoded, fn($v) => in_array($v, $vocabTerms, true))));
                    } else {
                        $logger->warn(sprintf('EnrichFieldBatch: item %d value "%s" not in vocab, skipping', $itemId, $value));
                        return;
                    }
                }
            }
        }

        // Cache
        $cache->put($itemId, $propertyId, $value, $model);

        // Apply
        $this->applyValue($itemId, $itemJson, $propertyId, $term, $value, $force, $api);
    }

    private function applyValue(int $itemId, array $itemJson, int $propertyId, string $term, string $value, bool $force, $api): void
    {
        if ($value === '') {
            return;
        }

        // Build payload preserving existing properties
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

        foreach (['o:resource_class', 'o:item_set', 'o:media', 'o:is_public', 'o:site'] as $sysKey) {
            if (isset($itemJson[$sysKey])) {
                $payload[$sysKey] = $itemJson[$sysKey];
            }
        }

        // Check if field already has a value (skip if not forcing)
        if (!$force) {
            $currentValues = $payload[$term] ?? [];
            foreach ($currentValues as $v) {
                if (!empty(trim($v['@value'] ?? ''))) {
                    return; // Already has value, skip
                }
            }
        }

        // Set the field value — handle JSON arrays for multi-value fields
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
