<?php declare(strict_types=1);

namespace SimilarPieces\Controller;

use Doctrine\ORM\EntityManager;
use Laminas\Http\Client as HttpClient;
use Laminas\Mvc\Controller\AbstractActionController;
use Laminas\View\Model\ViewModel;
use Omeka\Api\Manager as ApiManager;
use RuntimeException;
use Throwable;

class SimilarController extends AbstractActionController
{
    private HttpClient $httpClient;
    private ApiManager $api;
    private object $logger;
    private array $config;
    private bool $debug;
    private EntityManager $entityManager;

    private const LEXICAL_CACHE_TTL = 3600; // 1 hour
    private const LEXICAL_MAX_WORDS = 25;
    private const LEXICAL_MIN_WORD_LENGTH = 2;
    private const BIBO_CONTENT_PROPERTY_ID = 91;

    private const STOPWORDS = [
        'a','about','above','after','again','against','all','am','an','and','any',
        'are','as','at','be','because','been','before','being','below','between',
        'both','but','by','can','could','did','do','does','doing','down','during',
        'each','few','for','from','further','get','got','had','has','have','having',
        'he','her','here','hers','herself','him','himself','his','how','i','if',
        'in','into','is','it','its','itself','just','let','may','me','might',
        'more','most','my','myself','no','nor','not','now','of','off','on','once',
        'only','or','other','our','ours','ourselves','out','over','own','same',
        'shall','she','should','so','some','such','than','that','the','their',
        'theirs','them','themselves','then','there','these','they','this','those',
        'through','to','too','under','until','up','us','very','was','we','were',
        'what','when','where','which','while','who','whom','why','will','with',
        'would','you','your','yours','yourself','yourselves',
    ];

    public function __construct(HttpClient $httpClient, ApiManager $api, object $logger, array $config, EntityManager $entityManager)
    {
        $this->httpClient = $httpClient;
        $this->api = $api;
        $this->logger = $logger;
        $this->config = $config;
        $this->debug = !empty($config['debug']);
        $this->entityManager = $entityManager;
    }

    public function indexAction()
    {
        $itemId = (int) $this->params()->fromRoute('item_id', 0);
        if ($itemId <= 0) {
            return $this->notFoundAction();
        }

        $siteSlug = trim((string) $this->params()->fromQuery('site', ''));

        $error = null;
        $results = [];
        $healthWarning = null;

        try {
            $healthStatus = $this->fetchHealthStatus();
            if ($healthStatus !== 'ok') {
                $healthWarning = $this->healthWarningMessage($healthStatus);
            }
        } catch (Throwable $e) {
            $healthWarning = 'Similarity service status unavailable. Results may be incomplete.';
            $this->logError(sprintf('SimilarPieces health check error for item %d: %s', $itemId, $e->getMessage()), $e);
        }

        try {
            $payload = $this->fetchSimilarPayload($itemId);
            $parsed = $this->normalizeSimilarPayload($payload);
            $rawResults = $parsed['results'];

            $sourceData = $parsed['source'] ?? null;
            $sourceId = $sourceData['id'] ?? $itemId;

            $ids = array_column($rawResults, 'id');
            $ids[] = $sourceId;
            $ids = array_values(array_unique(array_filter($ids)));
            $itemsById = $this->fetchItemsById($ids);

            $source = [
                'id' => $sourceId,
                'item' => $itemsById[$sourceId] ?? null,
                'title' => $sourceData['title'] ?? null,
                'url' => $sourceData['url'] ?? null,
                'thumb_url' => $sourceData['thumb_url'] ?? null,
            ];

            foreach ($rawResults as $entry) {
                $id = $entry['id'];
                $results[] = [
                    'id' => $id,
                    'item' => $itemsById[$id] ?? null,
                    'title' => $entry['title'] ?? null,
                    'url' => $entry['url'] ?? null,
                    'thumb_url' => $entry['thumb_url'] ?? null,
                ];
            }
        } catch (Throwable $e) {
            $error = 'Similarity service unavailable. Please try again later.';
            $this->logError(sprintf('SimilarPieces error for item %d: %s', $itemId, $e->getMessage()), $e);
        }

        $view = new ViewModel([
            'itemId' => $itemId,
            'results' => $results,
            'error' => $error,
            'source' => $source ?? null,
            'siteSlug' => $siteSlug,
            'healthWarning' => $healthWarning,
        ]);
        $view->setTemplate('similar-pieces/similar/index');

        return $view;
    }

    public function jsonAction()
    {
        $itemId = (int) $this->params()->fromRoute('item_id', 0);
        $response = $this->getResponse();
        $response->getHeaders()->addHeaderLine('Content-Type', 'application/json');

        if ($itemId <= 0) {
            $response->setStatusCode(404);
            $response->setContent(json_encode(['error' => 'Invalid item ID']));
            return $response;
        }

        $siteSlug = trim((string) $this->params()->fromQuery('site', ''));

        try {
            $payload = $this->fetchSimilarPayload($itemId, 12);
            $parsed = $this->normalizeSimilarPayload($payload);
            $rawResults = $parsed['results'];

            $ids = array_column($rawResults, 'id');
            $ids = array_values(array_unique(array_filter($ids)));
            $itemsById = $this->fetchItemsById($ids);

            $results = [];
            foreach ($rawResults as $entry) {
                $id = $entry['id'];
                $item = $itemsById[$id] ?? null;

                $url = null;
                $thumbnail = null;
                $original = null;
                if ($item) {
                    $url = $siteSlug ? $item->siteUrl($siteSlug) : $item->url();
                    $title = $item->displayTitle();
                    $pm = $item->primaryMedia();
                    if ($pm) {
                        $thumbnail = $pm->thumbnailUrl('medium');
                        $original = $pm->originalUrl();
                    }
                }

                $url = $url ?: $entry['url'];
                $thumbnail = $thumbnail ?: $entry['thumb_url'];

                if (!$thumbnail) {
                    continue;
                }

                $results[] = [
                    'id' => $id,
                    'title' => $title ?? sprintf('Item %d', $id),
                    'url' => $url,
                    'thumbnail' => $thumbnail,
                    'original' => $original,
                ];
            }

            $response->setContent(json_encode(['results' => $results]));
        } catch (Throwable $e) {
            $this->logError(sprintf('SimilarPieces JSON error for item %d: %s', $itemId, $e->getMessage()), $e);
            $response->setStatusCode(502);
            $response->setContent(json_encode(['error' => 'Similarity service unavailable']));
        }

        return $response;
    }

    public function iconographyAction()
    {
        $itemId = (int) $this->params()->fromRoute('item_id', 0);
        $response = $this->getResponse();
        $response->getHeaders()->addHeaderLine('Content-Type', 'application/json');

        if ($itemId <= 0) {
            $response->setStatusCode(404);
            $response->setContent(json_encode(['error' => 'Invalid item ID']));
            return $response;
        }

        try {
            $baseUrl = rtrim((string) ($this->config['base_url'] ?? 'http://clip-api:8000'), '/');
            $url = sprintf('%s/v1/omeka/items/%d/iconography', $baseUrl, $itemId);

            $client = clone $this->httpClient;
            $client->resetParameters(true);
            $client->setUri($url);
            $client->setMethod('GET');
            $client->setHeaders(['Accept' => 'application/json']);
            $client->setOptions(['timeout' => 3]);

            $apiResponse = $client->send();
            $response->setStatusCode($apiResponse->getStatusCode());
            $response->setContent($apiResponse->getBody());
        } catch (Throwable $e) {
            $this->logError(sprintf('Iconography error for item %d: %s', $itemId, $e->getMessage()), $e);
            $response->setStatusCode(502);
            $response->setContent(json_encode(['error' => 'Iconography service unavailable']));
        }

        return $response;
    }

    public function iconographyBatchAction()
    {
        $response = $this->getResponse();
        $response->getHeaders()->addHeaderLine('Content-Type', 'application/json');

        $ids = trim((string) $this->params()->fromQuery('ids', ''));
        if ($ids === '') {
            $response->setStatusCode(400);
            $response->setContent(json_encode(['error' => 'ids parameter is required']));
            return $response;
        }

        try {
            $baseUrl = rtrim((string) ($this->config['base_url'] ?? 'http://clip-api:8000'), '/');
            $url = $baseUrl . '/v1/omeka/items/iconography/batch?ids=' . urlencode($ids);

            $client = clone $this->httpClient;
            $client->resetParameters(true);
            $client->setUri($url);
            $client->setMethod('GET');
            $client->setHeaders(['Accept' => 'application/json']);
            $client->setOptions(['timeout' => 5]);

            $apiResponse = $client->send();
            $response->setStatusCode($apiResponse->getStatusCode());
            $response->setContent($apiResponse->getBody());
        } catch (Throwable $e) {
            $this->logError(sprintf('Iconography batch error: %s', $e->getMessage()), $e);
            $response->setStatusCode(502);
            $response->setContent(json_encode(['error' => 'Iconography service unavailable']));
        }

        return $response;
    }

    public function lexicalProfileAction()
    {
        $itemId = (int) $this->params()->fromRoute('item_id', 0);
        $response = $this->getResponse();
        $response->getHeaders()->addHeaderLine('Content-Type', 'application/json');

        if ($itemId <= 0) {
            $response->setStatusCode(404);
            $response->setContent(json_encode(['error' => 'Invalid item ID']));
            return $response;
        }

        try {
            // Get this item's transcription text
            $itemText = $this->fetchItemTranscription($itemId);
            if ($itemText === null) {
                $response->setStatusCode(404);
                $response->setContent(json_encode(['error' => 'No transcription for this item']));
                return $response;
            }

            $itemWords = $this->tokenizeText($itemText);
            if (empty($itemWords)) {
                $response->setStatusCode(404);
                $response->setContent(json_encode(['error' => 'No words found in transcription']));
                return $response;
            }

            // Get or build the corpus frequency map
            $corpus = $this->getLexicalCorpus();
            $corpusSize = $corpus['corpus_size'];
            $wordCounts = $corpus['word_counts'];

            // Build word details sorted by rarity (lowest corpus frequency first)
            $words = [];
            foreach ($itemWords as $word) {
                $freq = $wordCounts[$word] ?? 0;
                $pct = $corpusSize > 0 ? round(($freq / $corpusSize) * 100, 1) : 0;
                $words[] = [
                    'word' => $word,
                    'corpus_frequency' => $freq,
                    'corpus_percentage' => $pct,
                ];
            }

            usort($words, function ($a, $b) {
                return $a['corpus_frequency'] <=> $b['corpus_frequency'];
            });

            $words = array_slice($words, 0, self::LEXICAL_MAX_WORDS);

            $response->setContent(json_encode([
                'words' => $words,
                'corpus_size' => $corpusSize,
                'total_unique_words' => count($itemWords),
            ]));
        } catch (Throwable $e) {
            $this->logError(sprintf('Lexical profile error for item %d: %s', $itemId, $e->getMessage()), $e);
            $response->setStatusCode(500);
            $response->setContent(json_encode(['error' => 'Lexical profile unavailable']));
        }

        return $response;
    }

    private function fetchItemTranscription(int $itemId): ?string
    {
        $conn = $this->entityManager->getConnection();
        $sql = 'SELECT v.value FROM value v WHERE v.resource_id = ? AND v.property_id = ? AND v.value IS NOT NULL LIMIT 1';
        $result = $conn->fetchOne($sql, [$itemId, self::BIBO_CONTENT_PROPERTY_ID]);
        if ($result === false || trim($result) === '') {
            return null;
        }
        return $result;
    }

    /**
     * @return string[] unique lowercase words (stopwords and short words filtered)
     */
    private function tokenizeText(string $text): array
    {
        $stopwords = array_flip(self::STOPWORDS);
        preg_match_all('/[a-zA-Z]+/', strtolower($text), $matches);
        $words = [];
        foreach ($matches[0] as $word) {
            if (strlen($word) < self::LEXICAL_MIN_WORD_LENGTH) {
                continue;
            }
            if (isset($stopwords[$word])) {
                continue;
            }
            $words[$word] = true;
        }
        return array_keys($words);
    }

    /**
     * @return array{corpus_size: int, word_counts: array<string, int>}
     */
    private function getLexicalCorpus(): array
    {
        $cacheFile = OMEKA_PATH . '/files/lexical-corpus-cache.json';

        if (file_exists($cacheFile) && (time() - filemtime($cacheFile)) < self::LEXICAL_CACHE_TTL) {
            $cached = json_decode(file_get_contents($cacheFile), true);
            if (is_array($cached) && isset($cached['corpus_size'], $cached['word_counts'])) {
                return $cached;
            }
        }

        $corpus = $this->buildLexicalCorpus();

        file_put_contents($cacheFile, json_encode($corpus), LOCK_EX);

        return $corpus;
    }

    /**
     * @return array{corpus_size: int, word_counts: array<string, int>}
     */
    private function buildLexicalCorpus(): array
    {
        $conn = $this->entityManager->getConnection();
        $sql = 'SELECT v.resource_id, v.value
                FROM value v
                JOIN resource r ON r.id = v.resource_id
                JOIN item i ON i.id = r.id
                WHERE v.property_id = ?
                  AND v.value IS NOT NULL
                  AND r.resource_template_id = 2';
        $rows = $conn->fetchAllAssociative($sql, [self::BIBO_CONTENT_PROPERTY_ID]);

        $itemCount = 0;
        $wordCounts = [];
        $seenItems = [];

        foreach ($rows as $row) {
            $resourceId = (int) $row['resource_id'];
            if (isset($seenItems[$resourceId])) {
                continue;
            }
            $seenItems[$resourceId] = true;
            $itemCount++;

            $words = $this->tokenizeText($row['value']);
            foreach ($words as $word) {
                $wordCounts[$word] = ($wordCounts[$word] ?? 0) + 1;
            }
        }

        return [
            'corpus_size' => $itemCount,
            'word_counts' => $wordCounts,
        ];
    }

    private function fetchHealthStatus(): string
    {
        $baseUrl = (string) ($this->config['base_url'] ?? 'http://clip-api:8000');
        $baseUrl = rtrim($baseUrl, '/');
        $url = $baseUrl . '/healthz';

        $timeout = (int) ($this->config['health_timeout'] ?? $this->config['timeout'] ?? 3);
        $timeout = max(1, min(10, $timeout));

        $client = clone $this->httpClient;
        $client->resetParameters(true);
        $client->setUri($url);
        $client->setMethod('GET');
        $client->setHeaders(['Accept' => 'application/json']);
        $client->setOptions([
            'timeout' => $timeout,
        ]);

        $response = $client->send();
        if (!$response->isSuccess()) {
            throw new RuntimeException(sprintf('Similarity health check HTTP %d', $response->getStatusCode()));
        }

        $body = $response->getBody();
        $data = json_decode($body, true);
        if (json_last_error() !== JSON_ERROR_NONE) {
            throw new RuntimeException('Similarity health check returned invalid JSON');
        }

        if (!is_array($data)) {
            throw new RuntimeException('Similarity health check returned unexpected payload');
        }

        $status = $data['status'] ?? null;
        if (!is_string($status) || $status === '') {
            throw new RuntimeException('Similarity health check returned missing status');
        }

        return strtolower($status);
    }

    private function healthWarningMessage(string $status): string
    {
        switch ($status) {
            case 'disabled':
                return 'Similarity service is currently disabled. Similar items are unavailable right now.';
            case 'degraded':
                return 'Similarity service is currently degraded. Results may be incomplete.';
            default:
                return sprintf('Similarity service reported status "%s". Results may be incomplete.', $status);
        }
    }

    private function fetchSimilarPayload(int $itemId, ?int $limit = null): array
    {
        $baseUrl = (string) ($this->config['base_url'] ?? 'http://clip-api:8000');
        $baseUrl = rtrim($baseUrl, '/');
        $url = sprintf('%s/v1/omeka/items/%d/similar', $baseUrl, $itemId);
        if ($limit !== null) {
            $url .= '?limit=' . $limit;
        }

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

        $response = $client->send();
        if (!$response->isSuccess()) {
            throw new RuntimeException(sprintf('Similarity service HTTP %d', $response->getStatusCode()));
        }

        $body = $response->getBody();
        $data = json_decode($body, true);
        if (json_last_error() !== JSON_ERROR_NONE) {
            throw new RuntimeException('Similarity service returned invalid JSON');
        }

        if (!is_array($data)) {
            throw new RuntimeException('Similarity service returned unexpected payload');
        }

        return $data;
    }

    private function normalizeSimilarPayload(array $payload): array
    {
        $meta = [
            'total' => null,
            'has_more' => null,
            'page' => null,
            'per_page' => null,
        ];

        $list = [];
        $isList = array_keys($payload) === range(0, count($payload) - 1);
        if ($isList) {
            $list = $payload;
        } else {
            foreach (['matches', 'items', 'results', 'data', 'ids'] as $key) {
                if (isset($payload[$key]) && is_array($payload[$key])) {
                    $list = $payload[$key];
                    break;
                }
            }
            if (isset($payload['source'])) {
                $source = $this->normalizeEntry($payload['source']);
            }
            if (isset($payload['total'])) {
                $meta['total'] = (int) $payload['total'];
            }
            if (array_key_exists('has_more', $payload)) {
                $meta['has_more'] = (bool) $payload['has_more'];
            }
            if (isset($payload['page'])) {
                $meta['page'] = (int) $payload['page'];
            }
            if (isset($payload['per_page'])) {
                $meta['per_page'] = (int) $payload['per_page'];
            }
        }

        $results = [];
        $seen = [];

        foreach ($list as $entry) {
            $normalized = $this->normalizeEntry($entry);
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
            'source' => $source ?? null,
        ];
    }

    private function normalizeEntry($entry): ?array
    {
        $id = null;
        $title = null;
        $url = null;
        $thumbUrl = null;

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
            foreach (['thumb_url', 'thumbnail_url', 'thumbUrl', 'thumbnailUrl'] as $key) {
                if (isset($entry[$key]) && is_string($entry[$key])) {
                    $thumbUrl = $entry[$key];
                    break;
                }
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
            $this->logError(sprintf('SimilarPieces could not fetch items: %s', $e->getMessage()), $e);
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
