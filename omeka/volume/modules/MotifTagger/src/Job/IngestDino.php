<?php declare(strict_types=1);

namespace MotifTagger\Job;

use Laminas\Http\Client as HttpClient;
use Omeka\Job\AbstractJob;

/**
 * Background job: re-embed all items into the DINOv2 motif patches Qdrant collection.
 *
 * Args: ['item_ids' => [int, ...]] (optional — defaults to all items)
 */
class IngestDino extends AbstractJob
{
    public function perform(): void
    {
        $services = $this->getServiceLocator();
        $api = $services->get('Omeka\ApiManager');
        $logger = $services->get('Omeka\Logger');
        $httpClient = $services->get('Omeka\HttpClient');
        $config = $services->get('Config');
        $moduleConfig = $config['motif_tagger'] ?? [];
        $baseUrl = rtrim($moduleConfig['clip_api_url'] ?? 'http://clip-api:8000', '/');

        $itemIds = $this->getArg('item_ids', []);
        if (empty($itemIds)) {
            $itemIds = $this->getAllItemIds($api);
        }
        $total = count($itemIds);
        $logger->info(sprintf('IngestDino: starting %d items', $total));

        $success = 0;
        $failed = 0;

        foreach ($itemIds as $i => $itemId) {
            if ($this->shouldStop()) {
                $logger->info(sprintf('IngestDino: stopped at %d/%d', $i, $total));
                return;
            }

            try {
                $this->processItem((int) $itemId, $api, $httpClient, $baseUrl, $logger);
                $success++;
            } catch (\Throwable $e) {
                $logger->err(sprintf('IngestDino: item %d failed: %s', $itemId, $e->getMessage()));
                $failed++;
            }

            if (($i + 1) % 10 === 0 || $i === $total - 1) {
                $logger->info(sprintf('IngestDino: [%d/%d] %d ok, %d failed', $i + 1, $total, $success, $failed));
            }
        }

        $logger->info(sprintf('IngestDino: completed — %d ok, %d failed of %d', $success, $failed, $total));
    }

    private function processItem(int $itemId, $api, HttpClient $httpClient, string $baseUrl, $logger): void
    {
        $itemRepr = $api->read('items', $itemId)->getContent();
        $itemJson = json_decode(json_encode($itemRepr), true);

        $mediaRefs = $itemJson['o:media'] ?? [];
        if (empty($mediaRefs)) {
            $logger->info(sprintf('IngestDino: item %d has no media, skipping', $itemId));
            return;
        }

        // Media list only contains references; fetch full media to get URLs
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

        $thumbUrl = $mediaJson['o:thumbnail_urls']['square'] ?? '';
        $thumbUrl = preg_replace(
            '#https?://(?:localhost:\d+|catalog\.jonsarkin\.com)#',
            'http://omeka:80',
            $thumbUrl
        );

        $ingestBody = [
            'image_url' => $mediaUrl,
            'omeka_url' => '/s/catalog/item/' . $itemId,
            'thumb_url' => $thumbUrl,
        ];

        $client = clone $httpClient;
        $client->resetParameters(true);
        $client->setUri($baseUrl . '/v1/dino/ingest/' . $itemId);
        $client->setMethod('POST');
        $client->setHeaders(['Content-Type' => 'application/json', 'Accept' => 'application/json']);
        $client->setRawBody(json_encode($ingestBody));
        $client->setOptions(['timeout' => 120]);
        $response = $client->send();

        if (!$response->isSuccess()) {
            throw new \RuntimeException(sprintf('HTTP %d: %s', $response->getStatusCode(), $response->getBody()));
        }
    }

    private function getAllItemIds($api): array
    {
        $ids = [];
        $page = 1;
        do {
            $response = $api->search('items', ['page' => $page, 'per_page' => 100]);
            $items = $response->getContent();
            if (empty($items)) {
                break;
            }
            foreach ($items as $item) {
                $itemJson = json_decode(json_encode($item), true);
                if (!empty($itemJson['o:media'])) {
                    $ids[] = $itemJson['o:id'];
                }
            }
            $page++;
        } while (count($items) === 100);

        return $ids;
    }
}
