<?php declare(strict_types=1);

namespace MotifTagger\Job;

use Laminas\Http\Client as HttpClient;
use Omeka\Job\AbstractJob;

/**
 * Background job: segment all items with MobileSAM + embed with DINOv2 CLS.
 *
 * Args: ['item_ids' => [int, ...]] (optional — defaults to all items)
 */
class IngestSegments extends AbstractJob
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
        $logger->info(sprintf('IngestSegments: starting %d items', $total));

        $success = 0;
        $failed = 0;

        foreach ($itemIds as $i => $itemId) {
            if ($this->shouldStop()) {
                $logger->info(sprintf('IngestSegments: stopped at %d/%d', $i, $total));
                return;
            }

            try {
                $this->processItem((int) $itemId, $api, $httpClient, $baseUrl, $logger);
                $success++;
            } catch (\Throwable $e) {
                $logger->err(sprintf('IngestSegments: item %d failed: %s', $itemId, $e->getMessage()));
                $failed++;
            }

            if (($i + 1) % 10 === 0 || $i === $total - 1) {
                $logger->info(sprintf('IngestSegments: [%d/%d] %d ok, %d failed', $i + 1, $total, $success, $failed));
            }
        }

        $logger->info(sprintf('IngestSegments: completed — %d ok, %d failed of %d', $success, $failed, $total));
    }

    private function processItem(int $itemId, $api, HttpClient $httpClient, string $baseUrl, $logger): void
    {
        $itemRepr = $api->read('items', $itemId)->getContent();
        $itemJson = json_decode(json_encode($itemRepr), true);

        $mediaRefs = $itemJson['o:media'] ?? [];
        if (empty($mediaRefs)) {
            $logger->info(sprintf('IngestSegments: item %d has no media, skipping', $itemId));
            return;
        }

        // Media list only contains references; fetch full media to get URLs
        $mediaId = $mediaRefs[0]['o:id'] ?? null;
        if (!$mediaId) {
            return;
        }
        $mediaRepr = $api->read('media', $mediaId)->getContent();
        $mediaJson = json_decode(json_encode($mediaRepr), true);

        // Use 'large' thumbnail instead of original to reduce memory usage
        $mediaUrl = $mediaJson['o:thumbnail_urls']['large']
            ?? $mediaJson['o:original_url']
            ?? null;
        if (!$mediaUrl) {
            return;
        }
        // Rewrite to container-internal URL. When dispatched from CLI (no HTTP
        // context), Omeka returns URLs like "http:///files/large/abc.jpg" where
        // the host is empty. parse_url fails on these, so extract the path with
        // a regex that handles both normal and empty-host cases.
        if (preg_match('#(/files/.+)$#', $mediaUrl, $m)) {
            $mediaUrl = 'http://omeka:80' . $m[1];
        }

        $thumbUrl = $mediaJson['o:thumbnail_urls']['square'] ?? '';
        if ($thumbUrl && preg_match('#(/files/.+)$#', $thumbUrl, $m)) {
            $thumbUrl = 'http://omeka:80' . $m[1];
        }

        $ingestBody = [
            'image_url' => $mediaUrl,
            'omeka_url' => '/s/catalog/item/' . $itemId,
            'thumb_url' => $thumbUrl,
        ];

        $client = clone $httpClient;
        $client->resetParameters(true);
        $client->setUri($baseUrl . '/v1/segment/ingest/' . $itemId);
        $client->setMethod('POST');
        $client->setHeaders(['Content-Type' => 'application/json', 'Accept' => 'application/json']);
        $client->setRawBody(json_encode($ingestBody));
        $client->setOptions(['timeout' => 300]);
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
