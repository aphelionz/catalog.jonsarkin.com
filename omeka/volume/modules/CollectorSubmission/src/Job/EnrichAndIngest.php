<?php declare(strict_types=1);

namespace CollectorSubmission\Job;

use Omeka\Job\AbstractJob;

/**
 * Background job: enrich transcription + CLIP ingest for a new submission item,
 * then set the item back to private.
 *
 * The item must be public for the Omeka API to read it in job context (no
 * authenticated user), so createItemAction makes it public, dispatches this
 * job, and this job flips it back to private when done.
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

        $logger->info(sprintf('CollectorSubmission\\EnrichAndIngest: starting item %d', $itemId));

        // --- Enrich transcription (property 91) ---
        $saved = $conn->fetchAssociative(
            'SELECT instructions, model FROM enrich_field_instructions WHERE property_id = 91'
        );
        if ($saved) {
            try {
                $enrichJob = new \EnrichItem\Job\EnrichFieldBatch($this->job, $services);
                // We can't easily run another job inline, so replicate the core logic:
                $this->enrichTranscription($itemId, $saved, $services, $logger);
            } catch (\Throwable $e) {
                $logger->err(sprintf('CollectorSubmission\\EnrichAndIngest: enrich failed for item %d: %s', $itemId, $e->getMessage()));
            }
        }

        // --- CLIP ingest ---
        try {
            $this->ingestClip($itemId, $services, $logger);
        } catch (\Throwable $e) {
            $logger->err(sprintf('CollectorSubmission\\EnrichAndIngest: CLIP ingest failed for item %d: %s', $itemId, $e->getMessage()));
        }

        // --- Set item back to private ---
        $conn->update('resource', ['is_public' => 0], ['id' => $itemId]);
        $logger->info(sprintf('CollectorSubmission\\EnrichAndIngest: item %d set back to private', $itemId));
    }

    private function enrichTranscription(int $itemId, array $saved, $services, $logger): void
    {
        $api = $services->get('Omeka\ApiManager');
        $anthropicClient = $services->get(\EnrichItem\Service\AnthropicClient::class);
        $cache = $services->get(\EnrichItem\Service\EnrichmentCache::class);

        $itemRepr = $api->read('items', $itemId)->getContent();
        $itemJson = json_decode(json_encode($itemRepr), true);

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
        // Rewrite URL for internal Docker access
        $mediaUrl = preg_replace(
            '#https?://(?:localhost:\d+|catalog\.jonsarkin\.com)#',
            'http://omeka:80',
            $mediaUrl
        );
        // Fix empty-host URLs (http:///files/...)
        $mediaUrl = preg_replace('#^http:///files/#', 'http://omeka:80/files/', $mediaUrl);

        $systemPrompt = \EnrichItem\Service\AnthropicClient::buildSystemPrompt(
            'content', $saved['instructions'], null
        );
        $userPrompt = 'Analyze this artwork and provide the value for: content';

        $result = $anthropicClient->enrichField($mediaUrl, $systemPrompt, $userPrompt, $saved['model']);
        $value = $result['value'] ?? '';

        if ($value === '') {
            $logger->info(sprintf('CollectorSubmission\\EnrichAndIngest: item %d transcription empty, skipping', $itemId));
            return;
        }

        $cache->put($itemId, 91, $value, $saved['model']);

        // Apply value to item
        $vals = $itemJson['bibo:content'] ?? [];
        $vals[] = ['type' => 'literal', 'property_id' => 91, '@value' => $value];
        $itemJson['bibo:content'] = $vals;
        $api->update('items', $itemId, $itemJson);

        $logger->info(sprintf('CollectorSubmission\\EnrichAndIngest: item %d transcription applied (%d chars)', $itemId, strlen($value)));
    }

    private function ingestClip(int $itemId, $services, $logger): void
    {
        $api = $services->get('Omeka\ApiManager');
        $config = $services->get('Config');
        $moduleConfig = $config['enrich_item'] ?? [];
        $baseUrl = rtrim($moduleConfig['clip_api_base_url'] ?? 'http://clip-api:8000', '/');

        $itemRepr = $api->read('items', $itemId)->getContent();
        $itemJson = json_decode(json_encode($itemRepr), true);

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

        $imageUrl = $mediaJson['o:original_url'] ?? '';
        $thumbUrl = $mediaJson['o:thumbnail_urls']['large'] ?? $imageUrl;

        // Rewrite URLs for Docker
        $rewrite = function ($url) {
            $url = preg_replace('#https?://(?:localhost:\d+|catalog\.jonsarkin\.com)#', 'http://omeka:80', $url);
            $url = preg_replace('#^http:///files/#', 'http://omeka:80/files/', $url);
            return $url;
        };
        $imageUrl = $rewrite($imageUrl);
        $thumbUrl = $rewrite($thumbUrl);

        $title = $itemJson['dcterms:title'][0]['@value'] ?? '';
        $description = $itemJson['dcterms:description'][0]['@value'] ?? '';
        $subjects = array_map(fn($s) => $s['@value'] ?? '', $itemJson['dcterms:subject'] ?? []);
        $year = null;
        if (!empty($itemJson['dcterms:date'][0]['@value'])) {
            $y = (int) $itemJson['dcterms:date'][0]['@value'];
            if ($y > 1900 && $y < 2100) {
                $year = $y;
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
            $logger->info(sprintf('CollectorSubmission\\EnrichAndIngest: item %d CLIP ingested', $itemId));
        } else {
            $logger->err(sprintf('CollectorSubmission\\EnrichAndIngest: item %d CLIP ingest HTTP %d: %s', $itemId, $httpCode, $response));
        }
    }
}
