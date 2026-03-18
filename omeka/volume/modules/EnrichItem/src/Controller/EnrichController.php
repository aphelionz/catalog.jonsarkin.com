<?php declare(strict_types=1);

namespace EnrichItem\Controller;

use EnrichItem\Service\AnthropicClient;
use EnrichItem\Service\EnrichmentCache;
use EnrichItem\Service\FieldInstructions;
use Laminas\Mvc\Controller\AbstractActionController;
use Laminas\View\Model\JsonModel;
use Laminas\View\Model\ViewModel;
use Omeka\Api\Manager as ApiManager;

class EnrichController extends AbstractActionController
{
    private ApiManager $api;
    private $logger;
    private array $config;
    private $entityManager;
    private $jobDispatcher;
    private AnthropicClient $anthropicClient;
    private EnrichmentCache $cache;
    private FieldInstructions $fieldInstructions;

    public function __construct(
        ApiManager $api,
        $logger,
        array $config,
        $entityManager,
        $jobDispatcher,
        AnthropicClient $anthropicClient,
        EnrichmentCache $cache,
        FieldInstructions $fieldInstructions
    ) {
        $this->api = $api;
        $this->logger = $logger;
        $this->config = $config;
        $this->entityManager = $entityManager;
        $this->jobDispatcher = $jobDispatcher;
        $this->anthropicClient = $anthropicClient;
        $this->cache = $cache;
        $this->fieldInstructions = $fieldInstructions;
    }

    /**
     * GET /admin/enrich-queue — render the field-level enrichment page.
     */
    public function queueAction(): ViewModel
    {
        return new ViewModel();
    }

    /**
     * GET /admin/enrich-queue/fields — JSON: template properties with metadata.
     */
    public function fieldsAction(): JsonModel
    {
        $templateId = (int) ($this->config['resource_template_id'] ?? 2);
        $conn = $this->entityManager->getConnection();

        // Get all properties for the template
        $properties = $conn->fetchAllAssociative("
            SELECT rtp.property_id, p.local_name, p.label,
                   CONCAT(v.prefix, ':', p.local_name) AS term,
                   rtp.data_type
            FROM resource_template_property rtp
            JOIN property p ON rtp.property_id = p.id
            JOIN vocabulary v ON p.vocabulary_id = v.id
            WHERE rtp.resource_template_id = ?
            ORDER BY rtp.position
        ", [$templateId]);

        // Load saved instructions
        $savedInstructions = $this->fieldInstructions->getAll();

        // Build result with vocab terms and missing counts
        $fields = [];
        foreach ($properties as $prop) {
            $propId = (int) $prop['property_id'];
            $field = [
                'property_id' => $propId,
                'label' => $prop['label'],
                'local_name' => $prop['local_name'],
                'term' => $prop['term'],
                'data_type' => $prop['data_type'],
                'vocab_terms' => null,
                'saved_instructions' => null,
                'saved_model' => null,
                'missing_count' => 0,
                'total_count' => 0,
            ];

            // Check for controlled vocabulary
            $dataTypes = $this->parseDataTypes($prop['data_type'] ?? '');
            foreach ($dataTypes as $dt) {
                $dt = trim($dt);
                if (str_starts_with($dt, 'customvocab:')) {
                    $vocabId = (int) substr($dt, strlen('customvocab:'));
                    $vocabRow = $conn->fetchAssociative(
                        'SELECT terms FROM custom_vocab WHERE id = ?',
                        [$vocabId]
                    );
                    if ($vocabRow && $vocabRow['terms']) {
                        $field['vocab_terms'] = json_decode($vocabRow['terms'], true);
                    }
                    break;
                }
            }

            // Saved instructions
            if (isset($savedInstructions[$propId])) {
                $field['saved_instructions'] = $savedInstructions[$propId]['instructions'];
                $field['saved_model'] = $savedInstructions[$propId]['model'];
            }

            // Count items missing this field
            $counts = $conn->fetchAssociative("
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN v.id IS NULL THEN 1
                             WHEN v.value IS NOT NULL AND TRIM(v.value) != '' THEN 0
                             WHEN v.value_resource_id IS NOT NULL THEN 0
                             WHEN v.uri IS NOT NULL AND TRIM(v.uri) != '' THEN 0
                             ELSE 1 END) AS missing
                FROM resource r
                JOIN item i ON i.id = r.id
                LEFT JOIN value v ON v.resource_id = r.id AND v.property_id = ?
                WHERE r.resource_template_id = ?
            ", [$propId, $templateId]);

            $field['total_count'] = (int) ($counts['total'] ?? 0);
            $field['missing_count'] = (int) ($counts['missing'] ?? 0);

            $fields[] = $field;
        }

        return new JsonModel(['fields' => $fields]);
    }

    /**
     * POST /admin/enrich-queue/save — save instructions for a field.
     */
    public function saveInstructionsAction(): JsonModel
    {
        $body = json_decode($this->getRequest()->getContent(), true) ?? [];
        $propertyId = (int) ($body['property_id'] ?? 0);
        $instructions = trim($body['instructions'] ?? '');
        $model = $body['model'] ?? 'haiku';

        if (!$propertyId || !$instructions) {
            return new JsonModel(['error' => 'property_id and instructions are required']);
        }

        $this->fieldInstructions->save($propertyId, $instructions, $model);
        return new JsonModel(['status' => 'saved']);
    }

    /**
     * POST /admin/enrich-queue/run — dispatch real-time batch enrichment job.
     */
    public function runBatchAction(): JsonModel
    {
        $body = json_decode($this->getRequest()->getContent(), true) ?? [];
        $propertyId = (int) ($body['property_id'] ?? 0);
        $force = !empty($body['force']);

        if (!$propertyId) {
            return new JsonModel(['error' => 'property_id is required']);
        }

        // Load instructions
        $saved = $this->fieldInstructions->get($propertyId);
        if (!$saved) {
            return new JsonModel(['error' => 'No saved instructions for this field']);
        }

        // Find items
        $itemIds = $this->getItemIds($propertyId, $force);
        if (empty($itemIds)) {
            return new JsonModel(['status' => 'nothing_to_do', 'count' => 0]);
        }

        // Get field metadata for prompt building
        $fieldMeta = $this->getFieldMeta($propertyId);

        $this->jobDispatcher->dispatch(\EnrichItem\Job\EnrichFieldBatch::class, [
            'property_id' => $propertyId,
            'term' => $fieldMeta['term'],
            'field_label' => $fieldMeta['label'],
            'instructions' => $saved['instructions'],
            'model' => $saved['model'],
            'vocab_terms' => $fieldMeta['vocab_terms'],
            'item_ids' => $itemIds,
            'force' => $force,
        ]);

        return new JsonModel(['status' => 'dispatched', 'count' => count($itemIds)]);
    }

    /**
     * POST /admin/enrich-queue/preview — enrich a single random item for preview.
     */
    public function previewAction(): JsonModel
    {
        $body = json_decode($this->getRequest()->getContent(), true) ?? [];
        $propertyId = (int) ($body['property_id'] ?? 0);

        if (!$propertyId) {
            return new JsonModel(['error' => 'property_id is required']);
        }

        $saved = $this->fieldInstructions->get($propertyId);
        if (!$saved) {
            return new JsonModel(['error' => 'No saved instructions for this field']);
        }

        // Pick a random item missing this field
        $itemIds = $this->getItemIds($propertyId, false);
        if (empty($itemIds)) {
            return new JsonModel(['error' => 'No items missing this field']);
        }
        $itemId = $itemIds[array_rand($itemIds)];

        try {
            $itemRepr = $this->api->read('items', $itemId)->getContent();
            $itemJson = json_decode(json_encode($itemRepr), true);
            $title = $itemJson['o:title'] ?? '(untitled)';

            $mediaUrl = $this->getOriginalMediaUrl($itemJson);
            if (!$mediaUrl) {
                return new JsonModel(['error' => 'Item has no media']);
            }
            $mediaUrl = $this->internalizeUrl($mediaUrl);

            $fieldMeta = $this->getFieldMeta($propertyId);
            $systemPrompt = AnthropicClient::buildSystemPrompt(
                $fieldMeta['label'],
                $saved['instructions'],
                $fieldMeta['vocab_terms']
            );
            $userPrompt = "Analyze this artwork and provide the value for: {$fieldMeta['label']}";

            $result = $this->anthropicClient->enrichField($mediaUrl, $systemPrompt, $userPrompt, $saved['model']);

            // Validate against vocab
            $value = $result['value'];
            $vocabWarning = null;
            if (!empty($fieldMeta['vocab_terms']) && $value !== '') {
                if (!in_array($value, $fieldMeta['vocab_terms'], true)) {
                    // Try case-insensitive match
                    $matched = false;
                    foreach ($fieldMeta['vocab_terms'] as $term) {
                        if (strcasecmp($term, $value) === 0) {
                            $value = $term;
                            $matched = true;
                            break;
                        }
                    }
                    if (!$matched) {
                        $vocabWarning = "Value \"{$value}\" is not in the controlled vocabulary";
                    }
                }
            }

            return new JsonModel([
                'item_id' => $itemId,
                'item_title' => $title,
                'suggested_value' => $value,
                'usage' => $result['usage'],
                'vocab_warning' => $vocabWarning,
            ]);
        } catch (\Throwable $e) {
            $this->logger->err(sprintf('EnrichItem preview failed for item %d: %s', $itemId, $e->getMessage()));
            return new JsonModel(['error' => 'Preview failed: ' . $e->getMessage()]);
        }
    }

    /**
     * POST /admin/enrich-queue/batch — submit to Anthropic Batch API.
     */
    public function batchSubmitAction(): JsonModel
    {
        $body = json_decode($this->getRequest()->getContent(), true) ?? [];
        $propertyId = (int) ($body['property_id'] ?? 0);
        $force = !empty($body['force']);

        if (!$propertyId) {
            return new JsonModel(['error' => 'property_id is required']);
        }

        $saved = $this->fieldInstructions->get($propertyId);
        if (!$saved) {
            return new JsonModel(['error' => 'No saved instructions for this field']);
        }

        $itemIds = $this->getItemIds($propertyId, $force);
        if (empty($itemIds)) {
            return new JsonModel(['status' => 'nothing_to_do', 'count' => 0]);
        }

        $fieldMeta = $this->getFieldMeta($propertyId);

        $this->jobDispatcher->dispatch(\EnrichItem\Job\EnrichFieldBatchApi::class, [
            'mode' => 'submit',
            'property_id' => $propertyId,
            'term' => $fieldMeta['term'],
            'field_label' => $fieldMeta['label'],
            'instructions' => $saved['instructions'],
            'model' => $saved['model'],
            'vocab_terms' => $fieldMeta['vocab_terms'],
            'item_ids' => $itemIds,
            'force' => $force,
        ]);

        return new JsonModel(['status' => 'dispatched', 'count' => count($itemIds)]);
    }

    /**
     * GET /admin/enrich-queue/batch-status — list batch statuses.
     */
    public function batchStatusAction(): JsonModel
    {
        $conn = $this->entityManager->getConnection();
        try {
            $rows = $conn->fetchAllAssociative(
                'SELECT batch_id, model, item_count, property_id, status, created_at, collected_at FROM enrich_batch_meta ORDER BY created_at DESC LIMIT 20'
            );
        } catch (\Throwable $e) {
            return new JsonModel(['batches' => []]);
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

        $this->jobDispatcher->dispatch(\EnrichItem\Job\EnrichFieldBatchApi::class, [
            'mode' => 'collect',
            'batch_id' => $batchId,
        ]);

        return new JsonModel(['status' => 'dispatched', 'batch_id' => $batchId]);
    }

    // ── Private helpers ─────────────────────────────────────────────

    /**
     * Get item IDs for enrichment. If force=false, only items missing the field.
     * If force=true, all items with the template.
     */
    private function getItemIds(int $propertyId, bool $force): array
    {
        $templateId = (int) ($this->config['resource_template_id'] ?? 2);
        $conn = $this->entityManager->getConnection();

        if ($force) {
            $sql = "
                SELECT r.id
                FROM resource r
                JOIN item i ON i.id = r.id
                WHERE r.resource_template_id = ?
                ORDER BY r.id
            ";
            $rows = $conn->fetchAllAssociative($sql, [$templateId]);
        } else {
            $sql = "
                SELECT r.id
                FROM resource r
                JOIN item i ON i.id = r.id
                LEFT JOIN value v ON v.resource_id = r.id AND v.property_id = ?
                WHERE r.resource_template_id = ?
                  AND (v.value IS NULL OR TRIM(v.value) = '')
                ORDER BY r.id
            ";
            $rows = $conn->fetchAllAssociative($sql, [$propertyId, $templateId]);
        }

        return array_map(fn($r) => (int) $r['id'], $rows);
    }

    /**
     * Get field metadata (label, term, vocab_terms) for a property ID.
     */
    private function getFieldMeta(int $propertyId): array
    {
        $conn = $this->entityManager->getConnection();

        $prop = $conn->fetchAssociative("
            SELECT p.label, p.local_name,
                   CONCAT(v.prefix, ':', p.local_name) AS term,
                   rtp.data_type
            FROM property p
            JOIN vocabulary v ON p.vocabulary_id = v.id
            LEFT JOIN resource_template_property rtp
                ON rtp.property_id = p.id AND rtp.resource_template_id = ?
            WHERE p.id = ?
        ", [(int) ($this->config['resource_template_id'] ?? 2), $propertyId]);

        $vocabTerms = null;
        if ($prop && $prop['data_type']) {
            $dataTypes = $this->parseDataTypes($prop['data_type']);
            foreach ($dataTypes as $dt) {
                $dt = trim($dt);
                if (str_starts_with($dt, 'customvocab:')) {
                    $vocabId = (int) substr($dt, strlen('customvocab:'));
                    $vocabRow = $conn->fetchAssociative(
                        'SELECT terms FROM custom_vocab WHERE id = ?',
                        [$vocabId]
                    );
                    if ($vocabRow && $vocabRow['terms']) {
                        $vocabTerms = json_decode($vocabRow['terms'], true);
                    }
                    break;
                }
            }
        }

        return [
            'label' => $prop['label'] ?? '',
            'term' => $prop['term'] ?? '',
            'vocab_terms' => $vocabTerms,
        ];
    }

    /**
     * Parse Omeka's data_type column, which is stored as a JSON array (e.g. '["customvocab:7"]').
     */
    private function parseDataTypes(string $raw): array
    {
        $raw = trim($raw);
        if ($raw === '') {
            return [];
        }
        $decoded = json_decode($raw, true);
        if (is_array($decoded)) {
            return $decoded;
        }
        // Fallback: plain comma-separated string
        return array_map('trim', explode(',', $raw));
    }

    private function getOriginalMediaUrl(array $itemJson): ?string
    {
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
}
