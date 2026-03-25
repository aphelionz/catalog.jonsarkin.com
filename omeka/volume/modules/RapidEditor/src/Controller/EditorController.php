<?php declare(strict_types=1);

namespace RapidEditor\Controller;

use Doctrine\ORM\EntityManager;
use Laminas\Mvc\Controller\AbstractActionController;
use Laminas\View\Model\JsonModel;
use Laminas\View\Model\ViewModel;

class EditorController extends AbstractActionController
{
    private EntityManager $entityManager;

    public function __construct(EntityManager $entityManager)
    {
        $this->entityManager = $entityManager;
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
            $response = $this->api()->update('items', $itemId, $body);
            $item = $response->getContent();
            // Return the updated item as JSON (same shape as REST API response)
            return new JsonModel(json_decode(json_encode($item), true));
        } catch (\Throwable $e) {
            $this->getResponse()->setStatusCode(500);
            return new JsonModel(['error' => 'Update failed: ' . $e->getMessage()]);
        }
    }
}
