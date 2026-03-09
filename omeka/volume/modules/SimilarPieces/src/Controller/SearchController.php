<?php declare(strict_types=1);

namespace SimilarPieces\Controller;

use Laminas\Http\Client as HttpClient;
use Laminas\Mvc\Controller\AbstractActionController;
use Laminas\View\Model\ViewModel;
use Omeka\Api\Manager as ApiManager;
use RuntimeException;
use Throwable;

class SearchController extends AbstractActionController
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
        if (empty($this->config['enable_search_ui'])) {
            return $this->notFoundAction();
        }

        $query = trim((string) $this->params()->fromQuery('q', ''));
        $limit = (int) $this->params()->fromQuery('limit', $this->config['default_per_page'] ?? 24);
        $limit = max(1, min(100, $limit));
        $offset = (int) $this->params()->fromQuery('offset', 0);
        $offset = max(0, $offset);

        $siteSlug = trim((string) $this->params()->fromQuery('site', ''));
        if ($siteSlug === '') {
            $siteSlug = trim((string) $this->params()->fromRoute('site-slug', ''));
        }

        $error = null;
        $results = [];
        $meta = [
            'total' => null,
            'has_more' => null,
        ];

        if ($query !== '') {
            try {
                $payload = $this->fetchSearchPayload($query, $limit, $offset);
                $parsed = $this->normalizeSearchPayload($payload);
                $rawResults = $parsed['results'];
                $meta = array_merge($meta, $parsed['meta']);

                $ids = array_column($rawResults, 'id');
                $ids = array_values(array_unique(array_filter($ids)));
                $itemsById = $this->fetchItemsById($ids);

                foreach ($rawResults as $entry) {
                    $id = $entry['id'];
                    $item = $itemsById[$id] ?? null;
                    $snippet = $this->truncateSnippet($entry['snippet'] ?? null);
                    if ($snippet === null) {
                        $snippet = $this->fallbackSnippetFromItem($item);
                    }

                    $results[] = [
                        'id' => $id,
                        'item' => $item,
                        'title' => $entry['title'] ?? null,
                        'url' => $entry['url'] ?? null,
                        'thumb_url' => $entry['thumb_url'] ?? null,
                        'snippet' => $snippet,
                    ];
                }
            } catch (Throwable $e) {
                $error = 'Similarity search unavailable. Please try again later.';
                $this->logError(sprintf('SimilarPieces search error for query "%s": %s', $query, $e->getMessage()), $e);
            }
        }

        $pagination = $this->buildPagination($limit, $offset, $meta['total'], $meta['has_more']);

        $view = new ViewModel([
            'query' => $query,
            'results' => $results,
            'error' => $error,
            'limit' => $limit,
            'offset' => $offset,
            'siteSlug' => $siteSlug,
            'meta' => $meta,
            'pagination' => $pagination,
        ]);
        $view->setTemplate('similar-pieces/search/index');

        return $view;
    }

    private function fetchSearchPayload(string $query, int $limit, int $offset): array
    {
        $baseUrl = (string) ($this->config['base_url'] ?? 'http://clip-api:8000');
        $baseUrl = rtrim($baseUrl, '/');
        $url = $baseUrl . '/v1/omeka/search';

        $timeout = (int) ($this->config['timeout'] ?? 3);
        $timeout = max(1, min(10, $timeout));

        $client = clone $this->httpClient;
        $client->resetParameters(true);
        $client->setUri($url);
        $client->setMethod('GET');
        $client->setHeaders(['Accept' => 'application/json']);
        $client->setOptions([
            'timeout' => $timeout,
        ]);
        $client->setParameterGet([
            'q' => $query,
            'limit' => $limit,
            'offset' => $offset,
        ]);

        $response = $client->send();
        if (!$response->isSuccess()) {
            throw new RuntimeException(sprintf('Similarity search HTTP %d', $response->getStatusCode()));
        }

        $body = $response->getBody();
        $data = json_decode($body, true);
        if (json_last_error() !== JSON_ERROR_NONE) {
            throw new RuntimeException('Similarity search returned invalid JSON');
        }

        if (!is_array($data)) {
            throw new RuntimeException('Similarity search returned unexpected payload');
        }

        return $data;
    }

    private function normalizeSearchPayload(array $payload): array
    {
        $meta = [
            'total' => null,
            'has_more' => null,
            'limit' => null,
            'offset' => null,
        ];

        $list = [];
        $isList = array_keys($payload) === range(0, count($payload) - 1);
        if ($isList) {
            $list = $payload;
        } else {
            foreach (['results', 'items', 'data', 'matches'] as $key) {
                if (isset($payload[$key]) && is_array($payload[$key])) {
                    $list = $payload[$key];
                    break;
                }
            }
            if (isset($payload['total'])) {
                $meta['total'] = (int) $payload['total'];
            }
            if ($meta['total'] === null) {
                foreach (['count', 'total_count', 'total_results'] as $key) {
                    if (isset($payload[$key])) {
                        $meta['total'] = (int) $payload[$key];
                        break;
                    }
                }
            }
            if (array_key_exists('has_more', $payload)) {
                $meta['has_more'] = (bool) $payload['has_more'];
            }
            if ($meta['has_more'] === null && array_key_exists('hasMore', $payload)) {
                $meta['has_more'] = (bool) $payload['hasMore'];
            }
            if (isset($payload['limit'])) {
                $meta['limit'] = (int) $payload['limit'];
            }
            if (isset($payload['offset'])) {
                $meta['offset'] = (int) $payload['offset'];
            }
        }

        $results = [];
        $seen = [];

        foreach ($list as $entry) {
            $normalized = $this->normalizeSearchEntry($entry);
            if (!$normalized) {
                continue;
            }

            $id = $normalized['id'];
            if (isset($seen[$id])) {
                continue;
            }
            $seen[$id] = true;

            $results[] = $normalized;
        }

        return [
            'results' => $results,
            'meta' => $meta,
        ];
    }

    private function normalizeSearchEntry($entry): ?array
    {
        $id = null;
        $title = null;
        $url = null;
        $thumbUrl = null;
        $snippet = null;

        if (is_int($entry)) {
            $id = $entry;
        } elseif (is_string($entry) && ctype_digit($entry)) {
            $id = (int) $entry;
        } elseif (is_array($entry)) {
            foreach (['id', 'item_id', 'itemId', 'itemID', 'omeka_item_id'] as $key) {
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
            foreach (['title', 'label', 'name'] as $key) {
                if (isset($entry[$key]) && is_string($entry[$key])) {
                    $title = $entry[$key];
                    break;
                }
            }
            foreach (['url', 'omeka_url', 'site_url', 'public_url'] as $key) {
                if (isset($entry[$key]) && is_string($entry[$key])) {
                    $url = $entry[$key];
                    break;
                }
            }
            foreach (['thumb_url', 'thumbnail_url', 'thumbUrl', 'thumbnailUrl', 'thumbnail'] as $key) {
                if (isset($entry[$key]) && is_string($entry[$key])) {
                    $thumbUrl = $entry[$key];
                    break;
                }
            }
            foreach (['snippet', 'summary', 'description', 'abstract', 'excerpt', 'text'] as $key) {
                if (isset($entry[$key])) {
                    $snippet = $this->normalizeSnippet($entry[$key]);
                    if ($snippet !== null) {
                        break;
                    }
                }
            }
            if ($snippet === null && isset($entry['highlights'])) {
                $snippet = $this->normalizeSnippet($entry['highlights']);
            }
        }

        if ($id === null || $id <= 0) {
            return null;
        }

        return [
            'id' => $id,
            'title' => $title,
            'url' => $url,
            'thumb_url' => $thumbUrl,
            'snippet' => $snippet,
        ];
    }

    private function normalizeSnippet($value): ?string
    {
        if (is_string($value)) {
            $value = trim($value);
            return $value === '' ? null : $value;
        }

        if (is_array($value)) {
            foreach ($value as $entry) {
                if (is_string($entry)) {
                    $entry = trim($entry);
                    if ($entry !== '') {
                        return $entry;
                    }
                }
            }
        }

        return null;
    }

    private function truncateSnippet(?string $snippet, int $maxLength = 180): ?string
    {
        if ($snippet === null) {
            return null;
        }

        $snippet = trim(preg_replace('/\\s+/', ' ', strip_tags($snippet)));
        if ($snippet === '') {
            return null;
        }

        if (strlen($snippet) <= $maxLength) {
            return $snippet;
        }

        return rtrim(substr($snippet, 0, $maxLength - 3)) . '...';
    }

    private function fallbackSnippetFromItem($item): ?string
    {
        if (!$item || !method_exists($item, 'displayDescription')) {
            return null;
        }

        $description = (string) $item->displayDescription();
        if ($description === '') {
            return null;
        }

        return $this->truncateSnippet($description);
    }

    private function buildPagination(int $limit, int $offset, ?int $total, ?bool $hasMore): array
    {
        $page = (int) floor($offset / $limit) + 1;
        $totalPages = null;
        if ($total !== null && $total > 0) {
            $totalPages = (int) ceil($total / $limit);
        }

        $prevOffset = $offset - $limit;
        if ($prevOffset < 0) {
            $prevOffset = null;
        }

        $nextOffset = $offset + $limit;
        if ($total !== null && $nextOffset >= $total) {
            $nextOffset = null;
        }
        if ($hasMore === false) {
            $nextOffset = null;
        }

        return [
            'page' => $page,
            'total_pages' => $totalPages,
            'prev_offset' => $prevOffset,
            'next_offset' => $nextOffset,
        ];
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
            $this->logError(sprintf('SimilarPieces could not fetch search items: %s', $e->getMessage()), $e);
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
