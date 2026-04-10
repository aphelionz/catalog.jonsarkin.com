<?php declare(strict_types=1);

namespace RapidEditor\Controller;

use Doctrine\ORM\EntityManager;
use EnrichItem\Service\AnthropicClient;
use EnrichItem\Service\EnrichmentCache;
use Laminas\Mvc\Controller\AbstractActionController;
use Laminas\View\Model\JsonModel;
use Laminas\View\Model\ViewModel;

class EditorController extends AbstractActionController
{
    private EntityManager $entityManager;
    private ?AnthropicClient $anthropicClient;
    private ?EnrichmentCache $enrichmentCache;

    public function __construct(
        EntityManager $entityManager,
        ?AnthropicClient $anthropicClient = null,
        ?EnrichmentCache $enrichmentCache = null
    ) {
        $this->entityManager = $entityManager;
        $this->anthropicClient = $anthropicClient;
        $this->enrichmentCache = $enrichmentCache;
    }

    public function indexAction()
    {
        return new ViewModel();
    }

    /**
     * Return all Piece items, custom vocabs, and item sets in a single JSON
     * response.  Uses direct DB queries to avoid Omeka's heavy serialisation
     * (which OOMs on large catalogs).
     */
    public function dataAction(): JsonModel
    {
        $conn = $this->entityManager->getConnection();

        // ── Property term map (id → "prefix:localName") ─────────────────
        $termRows = $conn->fetchAllAssociative(
            'SELECT p.id, CONCAT(v.prefix, ":", p.local_name) AS term
             FROM property p
             JOIN vocabulary v ON p.vocabulary_id = v.id'
        );
        $termMap = [];
        foreach ($termRows as $r) {
            $termMap[(int) $r['id']] = $r['term'];
        }

        // ── Items (resource rows) ───────────────────────────────────────
        $resourceRows = $conn->fetchAllAssociative(
            'SELECT r.id, r.resource_class_id, r.resource_template_id, r.is_public
             FROM resource r
             JOIN item i ON i.id = r.id
             WHERE r.resource_template_id = 2'
        );

        $itemIds = [];
        $items = [];
        foreach ($resourceRows as $r) {
            $id = (int) $r['id'];
            $itemIds[] = $id;
            $items[$id] = [
                'o:id' => $id,
                'o:resource_class' => $r['resource_class_id']
                    ? ['o:id' => (int) $r['resource_class_id']]
                    : null,
                'o:resource_template' => ['o:id' => 2],
                'o:is_public' => (bool) $r['is_public'],
                'o:media' => [],
                'o:item_set' => [],
            ];
        }

        if (!$itemIds) {
            return new JsonModel(['items' => [], 'vocabs' => [], 'item_sets' => []]);
        }

        // ── Values ──────────────────────────────────────────────────────
        // Fetch in batches to stay well within packet limits
        foreach (array_chunk($itemIds, 500) as $chunk) {
            $placeholders = implode(',', $chunk);
            $valueRows = $conn->fetchAllAssociative(
                "SELECT v.resource_id, v.property_id, v.type, v.value,
                        v.uri, v.value_resource_id, v.is_public, v.lang
                 FROM value v
                 WHERE v.resource_id IN ($placeholders)
                 ORDER BY v.resource_id, v.property_id, v.id"
            );

            foreach ($valueRows as $v) {
                $resId = (int) $v['resource_id'];
                $propId = (int) $v['property_id'];
                $term = $termMap[$propId] ?? null;
                if (!$term || !isset($items[$resId])) {
                    continue;
                }

                $val = [
                    'type' => $v['type'],
                    'property_id' => $propId,
                    '@value' => $v['value'],
                ];
                if ($v['uri'] !== null) {
                    $val['@id'] = $v['uri'];
                }
                if ($v['value_resource_id'] !== null) {
                    $val['value_resource_id'] = (int) $v['value_resource_id'];
                }
                if ($v['lang']) {
                    $val['@language'] = $v['lang'];
                }
                $val['o:is_public'] = (bool) $v['is_public'];

                $items[$resId][$term][] = $val;
            }
        }

        // ── Media (first per item only — editor fetches full URL lazily) ─
        $mediaRows = $conn->fetchAllAssociative(
            'SELECT m.item_id, m.id
             FROM media m
             WHERE m.item_id IN (' . implode(',', $itemIds) . ')
             ORDER BY m.item_id, m.position, m.id'
        );
        $seenMedia = [];
        foreach ($mediaRows as $m) {
            $itemId = (int) $m['item_id'];
            if (isset($seenMedia[$itemId])) {
                continue; // only first media
            }
            $seenMedia[$itemId] = true;
            $items[$itemId]['o:media'][] = ['o:id' => (int) $m['id']];
        }

        // ── Item sets ───────────────────────────────────────────────────
        $setRows = $conn->fetchAllAssociative(
            'SELECT iis.item_id, iis.item_set_id
             FROM item_item_set iis
             WHERE iis.item_id IN (' . implode(',', $itemIds) . ')'
        );
        foreach ($setRows as $s) {
            $itemId = (int) $s['item_id'];
            if (isset($items[$itemId])) {
                $items[$itemId]['o:item_set'][] = ['o:id' => (int) $s['item_set_id']];
            }
        }

        // ── Custom vocabs ───────────────────────────────────────────────
        $vocabs = [];
        try {
            $cvRows = $conn->fetchAllAssociative(
                'SELECT label, terms FROM custom_vocab'
            );
            foreach ($cvRows as $cv) {
                $terms = json_decode($cv['terms'], true);
                if (is_array($terms)) {
                    $vocabs[$cv['label']] = $terms;
                }
            }
        } catch (\Exception $e) {
            // Non-fatal — JS has defaults
        }

        // ── All item sets (for bucket mode) ─────────────────────────────
        $allSets = [];
        try {
            $setListRows = $conn->fetchAllAssociative(
                'SELECT r.id, r.title
                 FROM resource r
                 JOIN item_set ist ON ist.id = r.id
                 ORDER BY r.title'
            );
            foreach ($setListRows as $s) {
                $allSets[] = [
                    'id' => (int) $s['id'],
                    'label' => $s['title'] ?: "Set {$s['id']}",
                ];
            }
        } catch (\Exception $e) {
            // Non-fatal
        }

        return new JsonModel([
            'items' => array_values($items),
            'vocabs' => $vocabs,
            'item_sets' => $allSets,
        ]);
    }

    /**
     * Create a private item set (used by Exhibition Curation to persist
     * round survivors).  Expects JSON body: { "title": "[Curate] ..." }
     */
    public function createSetAction(): JsonModel
    {
        $body = json_decode($this->getRequest()->getContent(), true);
        $title = trim($body['title'] ?? '');

        if (!str_starts_with($title, '[Curate] ')) {
            $this->getResponse()->setStatusCode(400);
            return new JsonModel(['error' => 'Title must start with "[Curate] "']);
        }

        try {
            $response = $this->api()->create('item_sets', [
                'dcterms:title' => [[
                    'type'        => 'literal',
                    'property_id' => 1,
                    '@value'      => $title,
                ]],
                'o:is_public' => false,
            ]);
            $set = $response->getContent();
            return new JsonModel([
                'o:id'    => $set->id(),
                'o:title' => $title,
            ]);
        } catch (\Throwable $e) {
            $this->getResponse()->setStatusCode(500);
            return new JsonModel(['error' => 'Create failed: ' . $e->getMessage()]);
        }
    }

    /**
     * Proxy GET for a single item through Omeka's internal API so private
     * values (is_public=0) are visible to the editor.  The public REST API
     * strips them, which causes buildPayload / buildBasePayload to silently
     * drop those properties on the next save.
     */
    public function readAction(): JsonModel
    {
        $itemId = (int) $this->params('id');
        if ($itemId < 1) {
            return new JsonModel(['error' => 'Invalid item ID']);
        }

        try {
            $response = $this->api()->read('items', $itemId);
            $item = $response->getContent();
            return new JsonModel(json_decode(json_encode($item), true));
        } catch (\Throwable $e) {
            $this->getResponse()->setStatusCode(404);
            return new JsonModel(['error' => 'Item not found: ' . $e->getMessage()]);
        }
    }

    /**
     * Proxy media reads through Omeka's internal API so private items'
     * media (which is also private) is accessible to the editor.
     */
    public function mediaAction(): JsonModel
    {
        $mediaId = (int) $this->params('id');
        if ($mediaId < 1) {
            return new JsonModel(['error' => 'Invalid media ID']);
        }

        try {
            $response = $this->api()->read('media', $mediaId);
            $media = $response->getContent();
            return new JsonModel(json_decode(json_encode($media), true));
        } catch (\Throwable $e) {
            $this->getResponse()->setStatusCode(404);
            return new JsonModel(['error' => 'Media not found: ' . $e->getMessage()]);
        }
    }

    /**
     * Proxy PATCH requests through Omeka's internal API so the JS editor
     * doesn't need REST API credentials — the admin session handles auth.
     */
    public function patchAction(): JsonModel
    {
        $itemId = (int) $this->params('id');
        if ($itemId < 1) {
            return new JsonModel(['error' => 'Invalid item ID']);
        }

        $body = json_decode($this->getRequest()->getContent(), true);
        if (!is_array($body)) {
            return new JsonModel(['error' => 'Invalid JSON payload']);
        }

        try {
            $response = $this->api()->update('items', $itemId, $body, [], ['isPartial' => true]);
            $item = $response->getContent();
            // Return the updated item as JSON (same shape as REST API response)
            return new JsonModel(json_decode(json_encode($item), true));
        } catch (\Throwable $e) {
            $this->getResponse()->setStatusCode(500);
            return new JsonModel(['error' => 'Update failed: ' . $e->getMessage()]);
        }
    }

    /**
     * Bulk-add items to an item set via direct SQL.
     * POST body: { "item_ids": [1,2,3], "set_id": 42 }
     * This avoids Omeka's full item update which strips unrelated fields.
     */
    public function addToSetAction(): JsonModel
    {
        $body = json_decode($this->getRequest()->getContent(), true);
        $setId = (int) ($body['set_id'] ?? 0);
        $itemIds = $body['item_ids'] ?? [];

        if ($setId < 1 || !is_array($itemIds) || empty($itemIds)) {
            $this->getResponse()->setStatusCode(400);
            return new JsonModel(['error' => 'set_id and item_ids required']);
        }

        $conn = $this->entityManager->getConnection();

        // Get existing memberships to avoid duplicates
        $existing = $conn->executeQuery(
            'SELECT item_id FROM item_item_set WHERE item_set_id = ?',
            [$setId]
        )->fetchFirstColumn();
        $existingSet = array_flip($existing);

        $added = 0;
        foreach ($itemIds as $itemId) {
            $itemId = (int) $itemId;
            if ($itemId < 1 || isset($existingSet[$itemId])) continue;
            $conn->executeStatement(
                'INSERT INTO item_item_set (item_id, item_set_id) VALUES (?, ?)',
                [$itemId, $setId]
            );
            $added++;
        }

        return new JsonModel(['added' => $added, 'set_id' => $setId]);
    }

    /**
     * Proxy tournament-seed request to clip-api.
     * POST body: { "item_ids": [1, 2, 3, ...] }
     */
    public function tournamentSeedAction(): JsonModel
    {
        $body = json_decode($this->getRequest()->getContent(), true);
        if (!is_array($body) || empty($body['item_ids'])) {
            return new JsonModel(['error' => 'item_ids required']);
        }

        $clipUrl = rtrim(getenv('CLIP_API_URL') ?: 'http://clip-api:8000', '/');
        $url = $clipUrl . '/v1/tournament/seed';

        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => json_encode($body),
            CURLOPT_HTTPHEADER => ['Content-Type: application/json'],
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => 30,
        ]);
        $result = curl_exec($ch);
        $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $error = curl_error($ch);
        curl_close($ch);

        if ($result === false) {
            $this->getResponse()->setStatusCode(502);
            return new JsonModel(['error' => 'clip-api unreachable: ' . $error]);
        }

        $this->getResponse()->setStatusCode($httpCode);
        return new JsonModel(json_decode($result, true) ?? ['error' => 'Invalid response']);
    }

    /**
     * Suggest motif tags for an item using Claude with few-shot examples.
     * GET /admin/rapid-editor/suggest-motifs/:id
     */
    public function suggestMotifsAction(): JsonModel
    {
        $itemId = (int) $this->params('id');
        if ($itemId < 1) {
            return new JsonModel(['error' => 'Invalid item ID', 'suggestions' => []]);
        }

        if (!$this->anthropicClient) {
            return new JsonModel(['error' => 'AnthropicClient not available', 'suggestions' => []]);
        }

        $conn = $this->entityManager->getConnection();

        // ── Check cache ────────────────────────────────────────────────
        if ($this->enrichmentCache) {
            $cached = $this->enrichmentCache->get($itemId, 3);
            if ($cached !== null) {
                $tags = array_filter(array_map('trim', explode(',', $cached)));
                return new JsonModel(['suggestions' => array_values($tags), 'from_cache' => true]);
            }
        }

        // ── Get target item's image URL ────────────────────────────────
        try {
            $itemRepr = $this->api()->read('items', $itemId)->getContent();
            $itemJson = json_decode(json_encode($itemRepr), true);
        } catch (\Throwable $e) {
            return new JsonModel(['error' => 'Item not found', 'suggestions' => []]);
        }

        $mediaUrl = $this->getFirstMediaUrl($itemJson);
        if (!$mediaUrl) {
            return new JsonModel(['error' => 'Item has no media', 'suggestions' => []]);
        }

        try {
            [$targetB64, $targetMime] = $this->anthropicClient->downloadAndEncodeImage($mediaUrl);
        } catch (\Throwable $e) {
            return new JsonModel(['error' => 'Image download failed', 'suggestions' => []]);
        }

        // ── Fetch full motif vocabulary ────────────────────────────────
        $vocabRows = $conn->fetchAllAssociative(
            'SELECT DISTINCT v.value FROM value v
             WHERE v.property_id = 3 AND v.value IS NOT NULL AND TRIM(v.value) != ""
             ORDER BY v.value'
        );
        $allMotifs = array_column($vocabRows, 'value');

        // ── Get few-shot examples via clip-api similarity ──────────────
        $examples = $this->getSimilarTaggedItems($conn, $itemId, $allMotifs);

        // ── Build messages array (few-shot) ────────────────────────────
        $messages = [];
        foreach ($examples as $ex) {
            try {
                [$exB64, $exMime] = $this->anthropicClient->downloadAndEncodeImage($ex['image_url'], 512);
            } catch (\Throwable $e) {
                continue;
            }
            $messages[] = [
                'role' => 'user',
                'content' => [
                    ['type' => 'image', 'source' => ['type' => 'base64', 'media_type' => $exMime, 'data' => $exB64]],
                    ['type' => 'text', 'text' => 'Suggest motifs for this artwork.'],
                ],
            ];
            $messages[] = [
                'role' => 'assistant',
                'content' => implode(', ', $ex['motifs']),
            ];
        }

        // Target image (final user turn)
        $messages[] = [
            'role' => 'user',
            'content' => [
                ['type' => 'image', 'source' => ['type' => 'base64', 'media_type' => $targetMime, 'data' => $targetB64]],
                ['type' => 'text', 'text' => 'Suggest motifs for this artwork.'],
            ],
        ];

        // ── System prompt ──────────────────────────────────────────────
        $vocabList = implode(', ', $allMotifs);
        $systemPrompt = <<<PROMPT
You are cataloging artworks by Jon Sarkin (1953-2024) for a catalog raisonne.

Your task is to suggest motif tags for artworks. Motifs include both subjects (fish, face, building) and techniques (crosshatching, contour hatching, scribble).

Tagging principles:
- Be specific but reusable. "Cactus" not "plant" (too vague). A tag should fit 3-5+ works.
- Consistent granularity. Keep subjects and techniques at the same level of specificity.
- Singular nouns. "Fish" not "fishes".
- Tag what you see. "Spiral" is observable; "anxiety" is interpretation.
- 3-8 motifs per work is the sweet spot. Don't over-tag.
- Prefer established terms from the vocabulary below, but you may suggest new ones if clearly needed.

ESTABLISHED VOCABULARY:
{$vocabList}

Return ONLY a comma-separated list of motif tags. No explanations, no numbering, no markdown.
PROMPT;

        // ── Call Claude ────────────────────────────────────────────────
        try {
            $result = $this->anthropicClient->sendMessages($systemPrompt, $messages, 'opus');
        } catch (\Throwable $e) {
            return new JsonModel(['error' => 'Claude API error: ' . $e->getMessage(), 'suggestions' => []]);
        }

        // ── Parse and normalize response ───────────────────────────────
        $rawTags = array_filter(array_map('trim', explode(',', $result['value'])));
        $vocabLower = [];
        foreach ($allMotifs as $m) {
            $vocabLower[strtolower($m)] = $m;
        }
        $normalized = [];
        foreach ($rawTags as $tag) {
            $lower = strtolower($tag);
            $normalized[] = $vocabLower[$lower] ?? $tag;
        }
        $normalized = array_values(array_unique($normalized));

        // ── Cache result ───────────────────────────────────────────────
        if ($this->enrichmentCache && !empty($normalized)) {
            $this->enrichmentCache->put($itemId, 3, implode(', ', $normalized), 'opus');
        }

        return new JsonModel([
            'suggestions' => $normalized,
            'from_cache' => false,
            'usage' => $result['usage'] ?? null,
        ]);
    }

    /**
     * Get few-shot examples: items visually similar to $itemId that have ≥2 motifs.
     * Falls back to random well-tagged items if clip-api is unavailable.
     */
    private function getSimilarTaggedItems($conn, int $itemId, array $allMotifs): array
    {
        $examples = [];

        // Try clip-api similarity search
        $clipUrl = rtrim(getenv('CLIP_API_URL') ?: 'http://clip-api:8000', '/');
        $url = $clipUrl . "/v1/omeka/items/{$itemId}/similar?limit=20";

        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => 10,
        ]);
        $result = curl_exec($ch);
        $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);

        $candidateIds = [];
        if ($result !== false && $httpCode === 200) {
            $data = json_decode($result, true);
            foreach ($data['results'] ?? [] as $r) {
                $rid = (int) ($r['omeka_item_id'] ?? 0);
                if ($rid > 0 && $rid !== $itemId) {
                    $candidateIds[] = $rid;
                }
            }
        }

        // If clip-api failed, use random well-tagged items
        if (empty($candidateIds)) {
            $rows = $conn->fetchAllAssociative(
                'SELECT v.resource_id FROM value v
                 WHERE v.property_id = 3 AND v.value IS NOT NULL
                 GROUP BY v.resource_id
                 HAVING COUNT(*) >= 3
                 ORDER BY RAND()
                 LIMIT 10'
            );
            $candidateIds = array_map(fn($r) => (int) $r['resource_id'], $rows);
        }

        if (empty($candidateIds)) {
            return [];
        }

        // Fetch motifs for candidates
        $placeholders = implode(',', $candidateIds);
        $motifRows = $conn->fetchAllAssociative(
            "SELECT v.resource_id, v.value FROM value v
             WHERE v.property_id = 3 AND v.resource_id IN ($placeholders)
             AND v.value IS NOT NULL AND TRIM(v.value) != ''
             ORDER BY v.resource_id"
        );

        $motifsByItem = [];
        foreach ($motifRows as $r) {
            $motifsByItem[(int) $r['resource_id']][] = $r['value'];
        }

        // Pick candidates with ≥2 motifs, up to 5
        foreach ($candidateIds as $cid) {
            if (count($examples) >= 5) break;
            $motifs = $motifsByItem[$cid] ?? [];
            if (count($motifs) < 2) continue;

            // Get image URL
            try {
                $repr = $this->api()->read('items', $cid)->getContent();
                $json = json_decode(json_encode($repr), true);
                $imgUrl = $this->getFirstMediaUrl($json);
                if (!$imgUrl) continue;
            } catch (\Throwable $e) {
                continue;
            }

            $examples[] = [
                'item_id' => $cid,
                'image_url' => $imgUrl,
                'motifs' => $motifs,
            ];
        }

        return $examples;
    }

    /**
     * Get the internal Docker URL for the first media of an item.
     * The item's o:media array only contains references ({@id, o:id}),
     * so we read the media object separately to get its storage filename.
     */
    private function getFirstMediaUrl(array $itemJson): ?string
    {
        $media = $itemJson['o:media'] ?? [];
        if (empty($media)) return null;

        $mediaId = $media[0]['o:id'] ?? null;
        if (!$mediaId) return null;

        // Look up the storage filename from the DB (avoids another full API read)
        $conn = $this->entityManager->getConnection();
        $filename = $conn->fetchOne(
            'SELECT storage_id FROM media WHERE id = ?',
            [$mediaId]
        );
        if (!$filename) return null;

        // Fetch extension
        $ext = $conn->fetchOne(
            'SELECT extension FROM media WHERE id = ?',
            [$mediaId]
        );

        $fullFilename = $ext ? "{$filename}.{$ext}" : $filename;
        return "http://omeka:80/files/original/{$fullFilename}";
    }
}
