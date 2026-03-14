<?php declare(strict_types=1);

namespace EnrichItem\Controller;

use EnrichItem\Service\AnthropicClient;
use EnrichItem\Service\EnrichmentCache;
use Laminas\Http\Client as HttpClient;
use Laminas\Mvc\Controller\AbstractActionController;
use Laminas\View\Model\JsonModel;
use Laminas\View\Model\ViewModel;
use Omeka\Api\Manager as ApiManager;

class EnrichController extends AbstractActionController
{
    /** Map Claude field names → Omeka property terms + IDs */
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

    private HttpClient $httpClient;
    private ApiManager $api;
    private $logger;
    private array $config;
    private $entityManager;
    private $jobDispatcher;
    private AnthropicClient $anthropicClient;
    private EnrichmentCache $cache;

    public function __construct(
        HttpClient $httpClient,
        ApiManager $api,
        $logger,
        array $config,
        $entityManager,
        $jobDispatcher,
        AnthropicClient $anthropicClient,
        EnrichmentCache $cache
    ) {
        $this->httpClient = $httpClient;
        $this->api = $api;
        $this->logger = $logger;
        $this->config = $config;
        $this->entityManager = $entityManager;
        $this->jobDispatcher = $jobDispatcher;
        $this->anthropicClient = $anthropicClient;
        $this->cache = $cache;
    }

    /**
     * POST /admin/enrich/:item_id — analyze item with Claude.
     */
    public function analyzeAction(): JsonModel
    {
        $itemId = (int) $this->params('item_id');
        $body = json_decode($this->getRequest()->getContent(), true) ?? [];
        $model = $body['model'] ?? ($this->config['default_model'] ?? 'haiku');
        $force = !empty($body['force']);

        $item = $this->api->read('items', $itemId)->getContent();

        $mediaUrl = $this->getOriginalMediaUrl($item);
        if (!$mediaUrl) {
            return new JsonModel(['error' => 'Item has no media']);
        }

        // Check cache first (unless force)
        $promptVersion = AnthropicClient::PROMPT_VERSION;
        $usage = null;
        if (!$force) {
            $cached = $this->cache->get($itemId, $promptVersion);
            if ($cached) {
                $enrichment = $cached;
                $usage = ['model' => 'cache', 'input_tokens' => 0, 'output_tokens' => 0, 'cost_usd' => 0];
                $diff = $this->buildDiff($item, $enrichment);
                return new JsonModel([
                    'enrichment' => $enrichment,
                    'diff' => $diff,
                    'usage' => $usage,
                ]);
            }
        }

        // Convert to Docker-internal URL for image download
        $mediaUrl = $this->internalizeUrl($mediaUrl);

        try {
            $enrichment = $this->anthropicClient->analyze($mediaUrl, $model);
        } catch (\Throwable $e) {
            $this->logger->err(sprintf(
                'EnrichItem: Claude analysis failed for item %d: %s',
                $itemId, $e->getMessage()
            ));
            return new JsonModel([
                'error' => 'Enrichment failed: ' . $e->getMessage(),
            ]);
        }

        // Extract usage info before building diff and caching
        $usage = $enrichment['usage'] ?? null;
        unset($enrichment['usage']);

        // Cache the result
        $this->cache->put($itemId, $promptVersion, $enrichment);

        $diff = $this->buildDiff($item, $enrichment);

        return new JsonModel([
            'enrichment' => $enrichment,
            'diff' => $diff,
            'usage' => $usage,
        ]);
    }

    /**
     * POST /admin/enrich/:item_id/apply — apply selected enrichment fields.
     */
    public function applyAction(): JsonModel
    {
        $itemId = (int) $this->params('item_id');
        $body = json_decode($this->getRequest()->getContent(), true);
        $fields = $body['fields'] ?? [];

        if (empty($fields)) {
            return new JsonModel(['error' => 'No fields to apply']);
        }

        $itemRepr = $this->api->read('items', $itemId)->getContent();
        $itemJson = json_decode(json_encode($itemRepr), true);

        $payload = $this->buildPatchPayload($itemJson, $fields);

        try {
            $this->api->update('items', $itemId, $payload);
        } catch (\Exception $e) {
            $this->logger->err(sprintf('EnrichItem: PATCH failed for item %d: %s', $itemId, $e->getMessage()));
            return new JsonModel(['error' => 'Failed to update item: ' . $e->getMessage()]);
        }

        return new JsonModel(['status' => 'ok', 'item_id' => $itemId]);
    }

    /**
     * POST /admin/enrich/:item_id/ingest — re-index single item in search.
     * Still proxied through clip-api (CLIP vectors require Python).
     */
    public function ingestAction(): JsonModel
    {
        $itemId = (int) $this->params('item_id');
        $itemRepr = $this->api->read('items', $itemId)->getContent();

        $mediaUrl = $this->getOriginalMediaUrl($itemRepr);
        if (!$mediaUrl) {
            return new JsonModel(['error' => 'Item has no media']);
        }
        $mediaUrl = $this->internalizeUrl($mediaUrl);

        $itemJson = json_decode(json_encode($itemRepr), true);

        $baseUrl = rtrim($this->config['clip_api_base_url'] ?? 'http://clip-api:8000', '/');
        $timeout = (int) ($this->config['timeout'] ?? 120);

        $subjects = [];
        foreach (($itemJson['dcterms:subject'] ?? []) as $v) {
            $val = $v['@value'] ?? '';
            if ($val) {
                $subjects[] = $val;
            }
        }

        $ingestBody = [
            'image_url' => $mediaUrl,
            'title' => $itemJson['o:title'] ?? '',
            'description' => $this->extractValue($itemJson, 'dcterms:description'),
            'subjects' => $subjects,
            'omeka_url' => '/s/catalog/item/' . $itemId,
            'thumb_url' => $itemJson['o:thumbnail_urls']['square'] ?? '',
        ];

        $dateStr = $this->extractValue($itemJson, 'dcterms:date');
        if (preg_match('/\b((?:19|20)\d{2})\b/', $dateStr, $m)) {
            $ingestBody['year'] = (int) $m[1];
        }

        $client = clone $this->httpClient;
        $client->resetParameters(true);
        $client->setUri($baseUrl . '/v1/ingest/' . $itemId);
        $client->setMethod('POST');
        $client->setHeaders([
            'Content-Type' => 'application/json',
            'Accept' => 'application/json',
        ]);
        $client->setRawBody(json_encode($ingestBody));
        $client->setOptions(['timeout' => $timeout]);

        $response = $client->send();
        if (!$response->isSuccess()) {
            return new JsonModel([
                'error' => 'Ingest failed: HTTP ' . $response->getStatusCode(),
            ]);
        }

        return new JsonModel(json_decode($response->getBody(), true));
    }

    /**
     * GET /admin/enrich-queue — show unenriched items.
     */
    public function queueAction(): ViewModel
    {
        $templateId = (int) ($this->config['resource_template_id'] ?? 2);

        $conn = $this->entityManager->getConnection();
        $sql = "
            SELECT r.id, r.title,
                   (SELECT v.value FROM value v WHERE v.resource_id = r.id AND v.property_id = 91 LIMIT 1) AS transcription
            FROM resource r
            JOIN item i ON i.id = r.id
            WHERE r.resource_template_id = :template_id
            ORDER BY r.id DESC
        ";
        $rows = $conn->fetchAllAssociative($sql, ['template_id' => $templateId]);

        $unenriched = [];
        $enriched = [];
        foreach ($rows as $row) {
            if (empty(trim($row['transcription'] ?? ''))) {
                $unenriched[] = $row;
            } else {
                $enriched[] = $row;
            }
        }

        return new ViewModel([
            'unenriched' => $unenriched,
            'enrichedCount' => count($enriched),
            'totalCount' => count($rows),
        ]);
    }

    /**
     * POST /admin/enrich-queue/run — dispatch batch enrichment job.
     */
    public function runBatchAction(): JsonModel
    {
        $body = json_decode($this->getRequest()->getContent(), true);
        $itemIds = $body['item_ids'] ?? [];

        if (empty($itemIds)) {
            $templateId = (int) ($this->config['resource_template_id'] ?? 2);
            $conn = $this->entityManager->getConnection();
            $sql = "
                SELECT r.id
                FROM resource r
                JOIN item i ON i.id = r.id
                LEFT JOIN value v ON v.resource_id = r.id AND v.property_id = 91
                WHERE r.resource_template_id = :template_id
                  AND (v.value IS NULL OR TRIM(v.value) = '')
                ORDER BY r.id
            ";
            $rows = $conn->fetchAllAssociative($sql, ['template_id' => $templateId]);
            $itemIds = array_column($rows, 'id');
        }

        if (empty($itemIds)) {
            return new JsonModel(['status' => 'nothing_to_do', 'count' => 0]);
        }

        $this->jobDispatcher->dispatch(\EnrichItem\Job\EnrichBatch::class, [
            'item_ids' => $itemIds,
        ]);

        return new JsonModel(['status' => 'dispatched', 'count' => count($itemIds)]);
    }

    /**
     * POST /admin/enrich-queue/apply-cache — re-apply cached enrichments.
     * Dispatches a background job that applies cached results to unenriched items.
     */
    public function applyCacheAction(): JsonModel
    {
        $this->jobDispatcher->dispatch(\EnrichItem\Job\ApplyCache::class, [
            'prompt_version' => AnthropicClient::PROMPT_VERSION,
        ]);

        $count = $this->cache->countForVersion(AnthropicClient::PROMPT_VERSION);
        return new JsonModel(['status' => 'dispatched', 'cached_count' => $count]);
    }

    /**
     * POST /admin/enrich-queue/batch — submit items to Anthropic Batch API.
     */
    public function batchSubmitAction(): JsonModel
    {
        $body = json_decode($this->getRequest()->getContent(), true) ?? [];
        $model = $body['model'] ?? ($this->config['default_model'] ?? 'haiku');
        $itemIds = $body['item_ids'] ?? [];

        if (empty($itemIds)) {
            // Find all unenriched items
            $templateId = (int) ($this->config['resource_template_id'] ?? 2);
            $conn = $this->entityManager->getConnection();
            $sql = "
                SELECT r.id
                FROM resource r
                JOIN item i ON i.id = r.id
                LEFT JOIN value v ON v.resource_id = r.id AND v.property_id = 91
                WHERE r.resource_template_id = :template_id
                  AND (v.value IS NULL OR TRIM(v.value) = '')
                ORDER BY r.id
            ";
            $rows = $conn->fetchAllAssociative($sql, ['template_id' => $templateId]);
            $itemIds = array_column($rows, 'id');
        }

        if (empty($itemIds)) {
            return new JsonModel(['status' => 'nothing_to_do', 'count' => 0]);
        }

        $this->jobDispatcher->dispatch(\EnrichItem\Job\EnrichBatchApi::class, [
            'mode' => 'submit',
            'item_ids' => $itemIds,
            'model' => $model,
        ]);

        return new JsonModel(['status' => 'dispatched', 'count' => count($itemIds)]);
    }

    /**
     * GET /admin/enrich-queue/batch-status — list batch statuses.
     */
    public function batchStatusAction(): JsonModel
    {
        $conn = $this->entityManager->getConnection();

        // Check if table exists
        try {
            $rows = $conn->fetchAllAssociative(
                'SELECT batch_id, model, item_count, status, created_at, collected_at FROM enrich_batch_meta ORDER BY created_at DESC LIMIT 20'
            );
        } catch (\Throwable $e) {
            return new JsonModel(['batches' => [], 'error' => 'Batch table not ready']);
        }

        return new JsonModel(['batches' => $rows]);
    }

    /**
     * POST /admin/enrich-queue/batch-collect — collect results from a completed batch.
     */
    public function batchCollectAction(): JsonModel
    {
        $body = json_decode($this->getRequest()->getContent(), true) ?? [];
        $batchId = $body['batch_id'] ?? '';

        if (!$batchId) {
            return new JsonModel(['error' => 'batch_id required']);
        }

        $this->jobDispatcher->dispatch(\EnrichItem\Job\EnrichBatchApi::class, [
            'mode' => 'collect',
            'batch_id' => $batchId,
        ]);

        return new JsonModel(['status' => 'dispatched', 'batch_id' => $batchId]);
    }

    // ── Private helpers ─────────────────────────────────────────────

    private function getOriginalMediaUrl($item): ?string
    {
        $itemJson = json_decode(json_encode($item), true);
        $mediaRefs = $itemJson['o:media'] ?? [];
        if (empty($mediaRefs)) {
            return null;
        }
        $mediaId = $mediaRefs[0]['o:id'] ?? null;
        if (!$mediaId) {
            return null;
        }
        $mediaRepr = $this->api->read('media', $mediaId)->getContent();
        $mediaJson = json_decode(json_encode($mediaRepr), true);
        return $mediaJson['o:original_url'] ?? null;
    }

    private function internalizeUrl(string $url): string
    {
        return preg_replace(
            '#https?://(?:localhost:\d+|catalog\.jonsarkin\.com)#',
            'http://omeka:80',
            $url
        );
    }

    private function extractValue(array $item, string $term): string
    {
        $values = $item[$term] ?? [];
        if (empty($values)) {
            return '';
        }
        return $values[0]['@value'] ?? $values[0]['o:label'] ?? '';
    }

    private function buildDiff($item, array $enrichment): array
    {
        $itemJson = json_decode(json_encode($item), true);
        $diff = [];

        foreach (self::FIELD_MAP as $field => $props) {
            $term = $props['term'];
            $suggested = $enrichment[$field] ?? null;

            if ($field === 'motifs') {
                $current = [];
                foreach (($itemJson[$term] ?? []) as $v) {
                    $val = $v['@value'] ?? '';
                    if ($val) {
                        $current[] = $val;
                    }
                }
                $diff[$field] = [
                    'term' => $term,
                    'current' => $current,
                    'suggested' => is_array($suggested) ? $suggested : [],
                    'empty' => empty($current),
                ];
            } else {
                $current = $this->extractValue($itemJson, $term);
                $diff[$field] = [
                    'term' => $term,
                    'current' => $current,
                    'suggested' => $suggested,
                    'empty' => empty(trim($current)),
                ];
            }
        }

        return $diff;
    }

    private function buildPatchPayload(array $item, array $fields): array
    {
        $payload = [];

        foreach ($item as $key => $val) {
            if (strpos($key, ':') !== false && strpos($key, 'o:') !== 0 && is_array($val)) {
                $payload[$key] = array_map([$this, 'cleanValue'], $val);
            }
        }

        foreach (['o:resource_class', 'o:item_set', 'o:media', 'o:is_public', 'o:site'] as $sysKey) {
            if (isset($item[$sysKey])) {
                $payload[$sysKey] = $item[$sysKey];
            }
        }

        foreach ($fields as $field => $value) {
            if (!isset(self::FIELD_MAP[$field]) || $value === null || $value === '') {
                continue;
            }
            $props = self::FIELD_MAP[$field];
            $term = $props['term'];
            $propId = $props['property_id'];

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

        return $payload;
    }

    private function cleanValue(array $v): array
    {
        $writeKeys = ['type', 'property_id', '@value', '@id', '@language', 'o:label', 'value_resource_id', 'uri', 'o:is_public'];
        $clean = [];
        foreach ($writeKeys as $k) {
            if (array_key_exists($k, $v)) {
                $clean[$k] = $v[$k];
            }
        }
        return $clean;
    }
}
