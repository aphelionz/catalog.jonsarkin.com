<?php declare(strict_types=1);

namespace SimilarPieces\Controller;

use Laminas\Http\Client as HttpClient;
use Laminas\Mvc\Controller\AbstractActionController;
use Laminas\View\Model\ViewModel;
use Omeka\Api\Manager as ApiManager;
use RuntimeException;
use Throwable;

class VisualSearchController extends AbstractActionController
{
    private HttpClient $httpClient;
    private ApiManager $api;
    private object $logger;
    private array $config;
    private bool $debug;

    public function __construct(HttpClient $httpClient, ApiManager $api, object $logger, array $config)
    {
        $this->httpClient = $httpClient;
        $this->api = $api;
        $this->logger = $logger;
        $this->config = $config;
        $this->debug = !empty($config['debug']);
    }

    public function indexAction()
    {
        $request = $this->getRequest();
        $site = $this->currentSite();
        $siteSlug = $site ? $site->slug() : 'catalog';

        $error = null;
        $results = [];
        $searched = false;

        if ($request->isPost()) {
            $searched = true;
            $files = $request->getFiles()->toArray();
            $file = $files['image'] ?? null;

            if (!$file || empty($file['tmp_name']) || $file['error'] !== UPLOAD_ERR_OK) {
                $error = 'Please select an image to upload.';
            } else {
                try {
                    $payload = $this->fetchVisualSearchPayload($file['tmp_name'], $file['type'] ?? 'image/jpeg');
                    $rawResults = $this->normalizePayload($payload);

                    $ids = array_column($rawResults, 'id');
                    $ids = array_values(array_unique(array_filter($ids)));
                    $itemsById = $this->fetchItemsById($ids);

                    foreach ($rawResults as $entry) {
                        $id = $entry['id'];
                        $item = $itemsById[$id] ?? null;
                        $results[] = [
                            'id' => $id,
                            'item' => $item,
                            'thumb_url' => $entry['thumb_url'] ?? null,
                            'score' => $entry['score'] ?? null,
                        ];
                    }
                } catch (Throwable $e) {
                    $error = 'Visual search unavailable. Please try again later.';
                    $this->logError(sprintf('Visual search error: %s', $e->getMessage()), $e);
                }
            }
        }

        $view = new ViewModel([
            'results' => $results,
            'error' => $error,
            'searched' => $searched,
            'siteSlug' => $siteSlug,
        ]);
        $view->setTemplate('similar-pieces/visual-search/index');

        return $view;
    }

    public function jsonAction()
    {
        $request = $this->getRequest();
        $response = $this->getResponse();
        $response->getHeaders()->addHeaderLine('Content-Type', 'application/json');

        if (!$request->isPost()) {
            $response->setStatusCode(405);
            $response->setContent(json_encode(['error' => 'POST required']));
            return $response;
        }

        $files = $request->getFiles()->toArray();
        $file = $files['image'] ?? null;
        $site = $this->currentSite();
        $siteSlug = $site ? $site->slug() : 'catalog';

        if (!$file || empty($file['tmp_name']) || $file['error'] !== UPLOAD_ERR_OK) {
            $response->setStatusCode(400);
            $response->setContent(json_encode(['error' => 'No image uploaded']));
            return $response;
        }

        try {
            $payload = $this->fetchVisualSearchPayload($file['tmp_name'], $file['type'] ?? 'image/jpeg');
            $rawResults = $this->normalizePayload($payload);

            $ids = array_column($rawResults, 'id');
            $ids = array_values(array_unique(array_filter($ids)));
            $itemsById = $this->fetchItemsById($ids);

            $results = [];
            foreach ($rawResults as $entry) {
                $id = $entry['id'];
                $item = $itemsById[$id] ?? null;

                $url = null;
                $thumbnail = null;
                $title = null;
                if ($item) {
                    $url = $siteSlug ? $item->siteUrl($siteSlug) : $item->url();
                    $title = $item->displayTitle();
                    $pm = $item->primaryMedia();
                    if ($pm) {
                        $thumbnail = $pm->thumbnailUrl('medium');
                    }
                }
                $thumbnail = $thumbnail ?: ($entry['thumb_url'] ?? null);
                if (!$thumbnail) {
                    continue;
                }

                $results[] = [
                    'id' => $id,
                    'title' => $title ?? sprintf('Item %d', $id),
                    'url' => $url,
                    'thumbnail' => $thumbnail,
                    'score' => $entry['score'] ?? null,
                ];
            }

            $response->setContent(json_encode(['results' => $results]));
        } catch (Throwable $e) {
            $this->logError(sprintf('Visual search JSON error: %s', $e->getMessage()), $e);
            $response->setStatusCode(502);
            $response->setContent(json_encode(['error' => 'Visual search unavailable']));
        }

        return $response;
    }

    private function fetchVisualSearchPayload(string $tmpPath, string $mimeType): array
    {
        $baseUrl = rtrim((string) ($this->config['base_url'] ?? 'http://clip-api:8000'), '/');
        $url = $baseUrl . '/v1/omeka/images/search';

        $timeout = (int) ($this->config['timeout'] ?? 3);
        $timeout = max(1, min(30, $timeout));

        $client = clone $this->httpClient;
        $client->resetParameters(true);
        $client->setUri($url);
        $client->setMethod('POST');
        $client->setHeaders(['Accept' => 'application/json']);
        $client->setOptions(['timeout' => $timeout]);
        $client->setFileUpload($tmpPath, 'image', null, $mimeType);

        $response = $client->send();
        if (!$response->isSuccess()) {
            throw new RuntimeException(sprintf('Visual search HTTP %d: %s', $response->getStatusCode(), $response->getBody()));
        }

        $body = $response->getBody();
        $data = json_decode($body, true);
        if (json_last_error() !== JSON_ERROR_NONE) {
            throw new RuntimeException('Visual search returned invalid JSON');
        }

        if (!is_array($data)) {
            throw new RuntimeException('Visual search returned unexpected payload');
        }

        return $data;
    }

    private function normalizePayload(array $payload): array
    {
        $list = [];
        foreach (['matches', 'results', 'items', 'data'] as $key) {
            if (isset($payload[$key]) && is_array($payload[$key])) {
                $list = $payload[$key];
                break;
            }
        }

        $results = [];
        $seen = [];
        foreach ($list as $entry) {
            if (!is_array($entry)) {
                continue;
            }
            $id = null;
            foreach (['omeka_item_id', 'id', 'item_id'] as $key) {
                if (isset($entry[$key])) {
                    $value = $entry[$key];
                    if (is_int($value)) {
                        $id = $value;
                        break;
                    }
                    if (is_string($value) && ctype_digit($value)) {
                        $id = (int) $value;
                        break;
                    }
                }
            }
            if ($id === null || $id <= 0 || isset($seen[$id])) {
                continue;
            }
            $seen[$id] = true;

            $thumbUrl = null;
            foreach (['thumb_url', 'thumbnail_url'] as $key) {
                if (isset($entry[$key]) && is_string($entry[$key])) {
                    $thumbUrl = $entry[$key];
                    break;
                }
            }

            $results[] = [
                'id' => $id,
                'thumb_url' => $thumbUrl,
                'score' => isset($entry['score']) ? (float) $entry['score'] : null,
            ];
        }

        return $results;
    }

    private function fetchItemsById(array $ids): array
    {
        $itemsById = [];
        if (!$ids) {
            return $itemsById;
        }

        try {
            $response = $this->api->search('items', [
                'id' => $ids,
                'limit' => count($ids),
            ]);
            $items = $response->getContent();
            foreach ($items as $item) {
                $itemsById[$item->id()] = $item;
            }
        } catch (Throwable $e) {
            $this->logError(sprintf('Visual search could not fetch items: %s', $e->getMessage()), $e);
        }

        return $itemsById;
    }

    private function logError(string $message, ?Throwable $exception = null): void
    {
        if ($exception && $this->debug) {
            $message = sprintf("%s\n%s", $message, $exception->getTraceAsString());
        }

        if (method_exists($this->logger, 'err')) {
            $this->logger->err($message);
            return;
        }
        if (method_exists($this->logger, 'error')) {
            $this->logger->error($message);
        }
    }
}
