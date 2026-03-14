<?php declare(strict_types=1);

namespace EnrichItem\Controller;

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

    private $httpClient;
    private $api;
    private $logger;
    private array $config;
    private $entityManager;
    private $jobDispatcher;

    public function __construct(
        HttpClient $httpClient,
        ApiManager $api,
        $logger,
        array $config,
        $entityManager,
        $jobDispatcher
    ) {
        $this->httpClient = $httpClient;
        $this->api = $api;
        $this->logger = $logger;
        $this->config = $config;
        $this->entityManager = $entityManager;
        $this->jobDispatcher = $jobDispatcher;
    }

    /**
     * POST /admin/enrich/:item_id — analyze item with Claude.
     * Returns JSON with enrichment suggestions.
     */
    public function analyzeAction(): JsonModel
    {
        $itemId = (int) $this->params('item_id');
        $body = json_decode($this->getRequest()->getContent(), true) ?? [];
        $model = $body['model'] ?? ($this->config['default_model'] ?? 'haiku');

        $item = $this->api->read('items', $itemId)->getContent();

        $mediaUrl = $this->getOriginalMediaUrl($item);
        if (!$mediaUrl) {
            return new JsonModel(['error' => 'Item has no media']);
        }

        // Convert localhost URL to Docker-internal URL
        $mediaUrl = $this->internalizeUrl($mediaUrl);

        $baseUrl = rtrim($this->config['clip_api_base_url'] ?? 'http://clip-api:8000', '/');
        $timeout = (int) ($this->config['timeout'] ?? 30);

        $client = clone $this->httpClient;
        $client->resetParameters(true);
        $client->setUri($baseUrl . '/v1/enrich');
        $client->setMethod('POST');
        $client->setHeaders([
            'Content-Type' => 'application/json',
            'Accept' => 'application/json',
        ]);
        $client->setRawBody(json_encode([
            'image_url' => $mediaUrl,
            'model' => $model,
        ]));
        $client->setOptions(['timeout' => $timeout]);

        $response = $client->send();
        if (!$response->isSuccess()) {
            $body = json_decode($response->getBody(), true);
            $detail = $body['detail'] ?? $response->getBody();
            $this->logger->err(sprintf(
                'EnrichItem: clip-api enrich failed for item %d: HTTP %d — %s',
                $itemId, $response->getStatusCode(), $detail
            ));
            return new JsonModel([
                'error' => sprintf('Enrichment failed (HTTP %d): %s', $response->getStatusCode(), $detail),
            ]);
        }

        $enrichment = json_decode($response->getBody(), true);
        if (!$enrichment || json_last_error() !== JSON_ERROR_NONE) {
            return new JsonModel(['error' => 'Invalid response from enrichment service']);
        }

        // Extract usage info before building diff
        $usage = $enrichment['usage'] ?? null;
        unset($enrichment['usage']);

        // Build diff: compare current values vs suggestions
        $diff = $this->buildDiff($item, $enrichment);

        return new JsonModel([
            'enrichment' => $enrichment,
            'diff' => $diff,
            'usage' => $usage,
        ]);
    }

    /**
     * POST /admin/enrich/:item_id/apply — apply selected enrichment fields.
     * Expects JSON body: { "fields": {"transcription": "...", "medium": "...", ...} }
     */
    public function applyAction(): JsonModel
    {
        $itemId = (int) $this->params('item_id');
        $body = json_decode($this->getRequest()->getContent(), true);
        $fields = $body['fields'] ?? [];

        if (empty($fields)) {
            return new JsonModel(['error' => 'No fields to apply']);
        }

        // Get current item
        $itemRepr = $this->api->read('items', $itemId)->getContent();
        $itemJson = json_decode(json_encode($itemRepr), true);

        // Build PATCH payload preserving all existing properties
        $payload = $this->buildPatchPayload($itemJson, $fields);

        // PATCH via API
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
        $timeout = (int) ($this->config['timeout'] ?? 30);

        // Build ingest request body
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

        // Extract year from date
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

        // Find items with Artwork template that are missing transcription
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

        $this->jobDispatcher->dispatch(\EnrichItem\Job\EnrichBatch::class, [
            'item_ids' => $itemIds,
        ]);

        return new JsonModel(['status' => 'dispatched', 'count' => count($itemIds)]);
    }

    // ── Private helpers ─────────────────────────────────────────────

    private function getOriginalMediaUrl($item): ?string
    {
        $itemJson = json_decode(json_encode($item), true);
        $mediaRefs = $itemJson['o:media'] ?? [];
        if (empty($mediaRefs)) {
            return null;
        }
        // o:media contains references — fetch the first media to get original_url
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
        // Convert external Omeka URLs to Docker-internal ones
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
                // Array field
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

        // Preserve all existing vocabulary properties
        foreach ($item as $key => $val) {
            if (strpos($key, ':') !== false && strpos($key, 'o:') !== 0 && is_array($val)) {
                $payload[$key] = array_map([$this, 'cleanValue'], $val);
            }
        }

        // Preserve system keys
        foreach (['o:resource_class', 'o:item_set', 'o:media', 'o:is_public', 'o:site'] as $sysKey) {
            if (isset($item[$sysKey])) {
                $payload[$sysKey] = $item[$sysKey];
            }
        }

        // Apply enrichment fields
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
