<?php declare(strict_types=1);

namespace SimpleUpload\Controller;

use Laminas\Mvc\Controller\AbstractActionController;
use Laminas\View\Model\ViewModel;
use Omeka\Api\Exception\ValidationException;

class IndexController extends AbstractActionController
{
    public function indexAction()
    {
        $results = [];

        if ($this->getRequest()->isPost()) {
            $files = $this->getRequest()->getFiles()->toArray();
            $uploadedFiles = $files['file'] ?? [];

            if (empty($uploadedFiles)) {
                $this->messenger()->addError('No files were uploaded.');
                return $this->redirect()->toRoute('admin/simple-upload');
            }

            // Normalize: single file upload produces a flat array, multi produces indexed.
            if (isset($uploadedFiles['name']) && !is_array($uploadedFiles['name'])) {
                $uploadedFiles = [$uploadedFiles];
            }

            foreach ($uploadedFiles as $fileInfo) {
                if (empty($fileInfo['name']) || $fileInfo['error'] === UPLOAD_ERR_NO_FILE) {
                    continue;
                }

                $originalName = $fileInfo['name'];
                $title = pathinfo($originalName, PATHINFO_FILENAME);

                $itemData = [
                    'dcterms:title' => [
                        [
                            'property_id' => 1,
                            'type' => 'literal',
                            '@value' => $title,
                            'is_public' => true,
                        ],
                    ],
                    'o:is_public' => true,
                    'o:media' => [
                        [
                            'o:ingester' => 'upload',
                            'file_index' => 0,
                            'o:is_public' => true,
                        ],
                    ],
                ];

                $fileData = [
                    'file' => [
                        0 => $fileInfo,
                    ],
                ];

                try {
                    $response = $this->api(null, true)->create('items', $itemData, $fileData);
                    $item = $response->getContent();
                    $results[] = [
                        'filename' => $originalName,
                        'success' => true,
                        'item_id' => $item->id(),
                        'url' => $item->adminUrl(),
                    ];
                } catch (ValidationException $e) {
                    $messages = [];
                    foreach ($e->getErrorStore()->getErrors() as $msgs) {
                        foreach ((array) $msgs as $msg) {
                            $messages[] = is_array($msg) ? implode('; ', $msg) : $msg;
                        }
                    }
                    $results[] = [
                        'filename' => $originalName,
                        'success' => false,
                        'error' => implode('. ', $messages) ?: 'Validation failed.',
                    ];
                } catch (\Exception $e) {
                    $results[] = [
                        'filename' => $originalName,
                        'success' => false,
                        'error' => $e->getMessage(),
                    ];
                }
            }
        }

        $view = new ViewModel();
        $view->setVariable('results', $results);
        return $view;
    }
}
