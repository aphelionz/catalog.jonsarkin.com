<?php declare(strict_types=1);

namespace EnrichItem\Job;

use Omeka\Job\AbstractJob;

/**
 * Background job: enrich transcription + CLIP ingest for a newly created item.
 *
 * Reads item data via direct DBAL (not the Omeka API) so it works on private
 * items. New uploads land as `is_public = 0` and stay that way until a human
 * publishes them via RapidEditor — keeping unreviewed items out of the public
 * sitemap so Google never sees a public-then-private flap.
 */
class EnrichAndIngest extends AbstractJob
{
    public function perform(): void
    {
        $services = $this->getServiceLocator();
        $conn = $services->get('Omeka\Connection');
        $logger = $services->get('Omeka\Logger');

        $itemId = (int) $this->getArg('item_id');
        if (!$itemId) {
            return;
        }

        $logger->info(sprintf('EnrichItem\\EnrichAndIngest: starting item %d', $itemId));

        // --- Enrich transcription (property 91) ---
        $saved = $conn->fetchAssociative(
            'SELECT instructions, model FROM enrich_field_instructions WHERE property_id = 91'
        );
        if ($saved) {
            try {
                $this->enrichTranscription($itemId, $saved, $services, $logger);
            } catch (\Throwable $e) {
                $logger->err(sprintf('EnrichItem\\EnrichAndIngest: enrich failed for item %d: %s', $itemId, $e->getMessage()));
            }
        }

        // --- CLIP ingest ---
        try {
            $this->ingestClip($itemId, $services, $logger);
        } catch (\Throwable $e) {
            $logger->err(sprintf('EnrichItem\\EnrichAndIngest: CLIP ingest failed for item %d: %s', $itemId, $e->getMessage()));
        }
    }

    private function enrichTranscription(int $itemId, array $saved, $services, $logger): void
    {
        $conn = $services->get('Omeka\Connection');
        $anthropicClient = $services->get(\EnrichItem\Service\AnthropicClient::class);
        $cache = $services->get(\EnrichItem\Service\EnrichmentCache::class);

        $media = $conn->fetchAssociative(
            'SELECT storage_id, extension FROM media WHERE item_id = ? ORDER BY position ASC, id ASC LIMIT 1',
            [$itemId]
        );
        if (!$media || empty($media['storage_id'])) {
            return;
        }
        // Internal Docker URL — clip-api and Anthropic-via-omeka access files
        // through the omeka container by hostname.
        $mediaUrl = sprintf(
            'http://omeka:80/files/original/%s.%s',
            $media['storage_id'],
            $media['extension']
        );

        $systemPrompt = \EnrichItem\Service\AnthropicClient::buildSystemPrompt(
            'content', $saved['instructions'], null
        );
        $userPrompt = 'Analyze this artwork and provide the value for: content';

        $result = $anthropicClient->enrichField($mediaUrl, $systemPrompt, $userPrompt, $saved['model']);
        $value = $result['value'] ?? '';

        if ($value === '') {
            $logger->info(sprintf('EnrichItem\\EnrichAndIngest: item %d transcription empty, skipping', $itemId));
            return;
        }

        $cache->put($itemId, 91, $value, $saved['model']);

        // Apply value via direct SQL (background jobs lack API write permission)
        $conn->executeStatement(
            'INSERT INTO `value` (resource_id, property_id, type, `value`, is_public) VALUES (?, ?, ?, ?, ?)',
            [$itemId, 91, 'literal', $value, 1]
        );

        $logger->info(sprintf('EnrichItem\\EnrichAndIngest: item %d transcription applied (%d chars)', $itemId, strlen($value)));
    }

    private function ingestClip(int $itemId, $services, $logger): void
    {
        $conn = $services->get('Omeka\Connection');
        $config = $services->get('Config');
        $moduleConfig = $config['enrich_item'] ?? [];
        $baseUrl = rtrim($moduleConfig['clip_api_base_url'] ?? 'http://clip-api:8000', '/');

        $media = $conn->fetchAssociative(
            'SELECT storage_id, extension, has_thumbnails FROM media WHERE item_id = ? ORDER BY position ASC, id ASC LIMIT 1',
            [$itemId]
        );
        if (!$media || empty($media['storage_id'])) {
            return;
        }
        $imageUrl = sprintf(
            'http://omeka:80/files/original/%s.%s',
            $media['storage_id'],
            $media['extension']
        );
        $thumbUrl = !empty($media['has_thumbnails'])
            ? sprintf('http://omeka:80/files/large/%s.jpg', $media['storage_id'])
            : $imageUrl;

        // Property IDs: title=1, subject=3 (repeatable), description=4, date=7
        $valueRows = $conn->fetchAllAssociative(
            'SELECT property_id, `value` FROM `value` WHERE resource_id = ? AND property_id IN (1, 3, 4, 7)',
            [$itemId]
        );
        $title = '';
        $description = '';
        $subjects = [];
        $year = null;
        foreach ($valueRows as $row) {
            $v = (string) ($row['value'] ?? '');
            if ($v === '') {
                continue;
            }
            switch ((int) $row['property_id']) {
                case 1: $title = $v; break;
                case 4: $description = $v; break;
                case 3: $subjects[] = $v; break;
                case 7:
                    $y = (int) $v;
                    if ($y > 1900 && $y < 2100) {
                        $year = $y;
                    }
                    break;
            }
        }

        $body = json_encode([
            'image_url' => $imageUrl,
            'title' => $title,
            'description' => $description,
            'subjects' => $subjects,
            'year' => $year,
            'omeka_url' => "https://catalog.jonsarkin.com/s/catalog/item/{$itemId}",
            'thumb_url' => $thumbUrl,
        ]);

        $ch = curl_init("{$baseUrl}/v1/ingest/{$itemId}");
        curl_setopt_array($ch, [
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => $body,
            CURLOPT_HTTPHEADER => ['Content-Type: application/json'],
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => 120,
        ]);
        $response = curl_exec($ch);
        $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);

        if ($httpCode === 200) {
            $logger->info(sprintf('EnrichItem\\EnrichAndIngest: item %d CLIP ingested', $itemId));
        } else {
            $logger->err(sprintf('EnrichItem\\EnrichAndIngest: item %d CLIP ingest HTTP %d: %s', $itemId, $httpCode, $response));
        }
    }
}
