<?php declare(strict_types=1);

namespace RapidEditor\Controller;

use Laminas\Mvc\Controller\AbstractActionController;
use Laminas\View\Model\JsonModel;
use Laminas\View\Model\ViewModel;

class EditorController extends AbstractActionController
{
    public function indexAction()
    {
        return new ViewModel();
    }

    /**
     * Return all Piece items, custom vocabs, and item sets in a single JSON
     * response — replaces the chunked REST API fetch on the JS side.
     */
    public function dataAction(): JsonModel
    {
        $api = $this->api();

        // Fetch all items with the Piece resource template (id=2)
        $items = $api->search('items', [
            'resource_template_id' => 2,
            'limit'                => 10000,
        ])->getContent();

        $itemsJson = json_decode(json_encode($items), true);

        // Custom vocabs
        $vocabs = [];
        try {
            $cvs = $api->search('custom_vocabs', ['limit' => 10000])->getContent();
            foreach ($cvs as $cv) {
                $arr = json_decode(json_encode($cv), true);
                $vocabs[$arr['o:label']] = $arr['o:terms'] ?? [];
            }
        } catch (\Exception $e) {
            // Non-fatal — JS has defaults
        }

        // Item sets
        $sets = [];
        try {
            $iSets = $api->search('item_sets', ['limit' => 10000])->getContent();
            foreach ($iSets as $s) {
                $arr = json_decode(json_encode($s), true);
                $sets[] = ['id' => $arr['o:id'], 'label' => $arr['o:title'] ?? "Set {$arr['o:id']}"];
            }
        } catch (\Exception $e) {
            // Non-fatal
        }

        return new JsonModel([
            'items'     => $itemsJson,
            'vocabs'    => $vocabs,
            'item_sets' => $sets,
        ]);
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
        } catch (\Exception $e) {
            $this->getResponse()->setStatusCode(500);
            return new JsonModel(['error' => 'Update failed: ' . $e->getMessage()]);
        }
    }
}
