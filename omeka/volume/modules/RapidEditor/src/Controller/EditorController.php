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
