<?php declare(strict_types=1);

namespace SimpleUpload\Controller;

use Laminas\Mvc\Controller\AbstractActionController;
use Laminas\View\Model\JsonModel;
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
                if ($this->isAjax()) {
                    return new JsonModel(['success' => false, 'error' => 'No files were uploaded.']);
                }
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
                            'is_public' => false,
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
                    $this->dispatchEnrichAndIngest($item->id());
                    $results[] = [
                        'filename' => $originalName,
                        'success' => true,
                        'item_id' => $item->id(),
                        'url' => $item->adminUrl(),
                    ];
                } catch (ValidationException $e) {
                    $messages = [];
                    array_walk_recursive(
                        $e->getErrorStore()->getErrors(),
                        function ($val) use (&$messages) {
                            if (is_string($val) && $val !== '') {
                                $messages[] = $val;
                            }
                        }
                    );
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

            if ($this->isAjax()) {
                return new JsonModel($results[0] ?? ['success' => false, 'error' => 'No file processed.']);
            }
        }

        $view = new ViewModel();
        $view->setVariable('results', $results);
        return $view;
    }

    private function dispatchEnrichAndIngest(int $itemId): void
    {
        try {
            $services = $this->getEvent()->getApplication()->getServiceManager();
            $jobDispatcher = $services->get('Omeka\Job\Dispatcher');
            $jobDispatcher->dispatch('EnrichItem\Job\EnrichAndIngest', [
                'item_id' => $itemId,
            ]);
        } catch (\Throwable $e) {
            error_log('SimpleUpload: enrich/ingest dispatch failed: ' . $e->getMessage());
        }
    }

    private function isAjax(): bool
    {
        $request = $this->getRequest();
        return $request->isXmlHttpRequest()
            || str_contains($request->getHeader('Accept', '')?->getFieldValue() ?? '', 'application/json');
    }
}
