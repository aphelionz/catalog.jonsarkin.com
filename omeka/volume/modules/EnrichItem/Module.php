<?php declare(strict_types=1);

namespace EnrichItem;

use Laminas\EventManager\Event;
use Laminas\EventManager\SharedEventManagerInterface;
use Laminas\Loader\StandardAutoloader;
use Laminas\Mvc\Controller\AbstractController;
use Laminas\Mvc\MvcEvent;
use Laminas\Permissions\Acl\Resource\GenericResource as Resource;
use Laminas\View\Renderer\PhpRenderer;
use Omeka\Module\AbstractModule;

class Module extends AbstractModule
{
    public function getConfig(): array
    {
        return include __DIR__ . '/config/module.config.php';
    }

    public function getAutoloaderConfig(): array
    {
        return [
            StandardAutoloader::class => [
                'namespaces' => [
                    __NAMESPACE__ => __DIR__ . '/src',
                ],
            ],
        ];
    }

    public function onBootstrap(MvcEvent $event): void
    {
        parent::onBootstrap($event);

        $services = $event->getApplication()->getServiceManager();
        $acl = $services->get('Omeka\Acl');
        $resourceId = Controller\EnrichController::class;

        if (method_exists($acl, 'hasResource') && !$acl->hasResource($resourceId)) {
            $acl->addResource(new Resource($resourceId));
        }
        // Only editors and above can enrich
        $acl->allow(['editor', 'global_admin', 'site_admin'], $resourceId);
    }

    public function attachListeners(SharedEventManagerInterface $sharedEventManager): void
    {
        $sharedEventManager->attach(
            'Omeka\Api\Adapter\MediaAdapter',
            'api.create.post',
            [$this, 'onMediaCreate']
        );
    }

    public function onMediaCreate(Event $event): void
    {
        $services = $this->getServiceLocator();
        $settings = $services->get('Omeka\Settings');

        if (!$settings->get('enrich_item_auto_enrich', false)) {
            return;
        }

        $response = $event->getParam('response');
        $media = $response->getContent();
        $mediaType = $media->mediaType();

        if (!$mediaType || !str_starts_with($mediaType, 'image/')) {
            return;
        }

        $itemId = $media->item()->id();
        if (!$itemId) {
            return;
        }

        $autoProperties = $settings->get('enrich_item_auto_enrich_properties', '91');
        $propertyIds = array_map('intval', array_filter(explode(',', $autoProperties)));

        $fieldInstructions = $services->get(Service\FieldInstructions::class);
        $conn = $services->get('Omeka\EntityManager')->getConnection();
        $jobDispatcher = $services->get('Omeka\Job\Dispatcher');
        $logger = $services->get('Omeka\Logger');

        foreach ($propertyIds as $propertyId) {
            $saved = $fieldInstructions->get($propertyId);
            if (!$saved) {
                continue;
            }

            $prop = $conn->fetchAssociative("
                SELECT p.label, CONCAT(v.prefix, ':', p.local_name) AS term
                FROM property p
                JOIN vocabulary v ON p.vocabulary_id = v.id
                WHERE p.id = ?
            ", [$propertyId]);

            if (!$prop) {
                continue;
            }

            $jobDispatcher->dispatch(Job\EnrichFieldBatch::class, [
                'property_id' => $propertyId,
                'term' => $prop['term'],
                'field_label' => $prop['label'],
                'instructions' => $saved['instructions'],
                'model' => $saved['model'],
                'vocab_terms' => null,
                'item_ids' => [$itemId],
                'force' => false,
            ]);

            $logger->info(sprintf(
                'EnrichItem: auto-enrich dispatched for item %d, property %d (%s)',
                $itemId, $propertyId, $prop['term']
            ));
        }
    }

    public function getConfigForm(PhpRenderer $renderer): string
    {
        $settings = $this->getServiceLocator()->get('Omeka\Settings');
        $autoEnrich = (bool) $settings->get('enrich_item_auto_enrich', false);
        $properties = $settings->get('enrich_item_auto_enrich_properties', '91');

        $checkedAttr = $autoEnrich ? ' checked' : '';

        return <<<HTML
<fieldset>
    <legend>Auto-enrich on upload</legend>
    <div class="field">
        <div class="field-meta">
            <label for="enrich-auto-enrich">Enable auto-enrichment</label>
            <div class="field-description">
                Automatically enrich configured fields via Claude when an image is uploaded.
                Requires saved instructions for each property in the Enrich Queue.
            </div>
        </div>
        <div class="inputs">
            <input type="checkbox" id="enrich-auto-enrich" name="auto_enrich" value="1"{$checkedAttr}>
        </div>
    </div>
    <div class="field">
        <div class="field-meta">
            <label for="enrich-auto-properties">Property IDs to auto-enrich</label>
            <div class="field-description">
                Comma-separated property IDs (e.g. 91 for bibo:content / Transcription).
            </div>
        </div>
        <div class="inputs">
            <input type="text" id="enrich-auto-properties" name="auto_enrich_properties" value="{$properties}">
        </div>
    </div>
</fieldset>
HTML;
    }

    public function handleConfigForm(AbstractController $controller): bool
    {
        $settings = $this->getServiceLocator()->get('Omeka\Settings');
        $params = $controller->getRequest()->getPost();

        $settings->set('enrich_item_auto_enrich', !empty($params['auto_enrich']));
        $settings->set(
            'enrich_item_auto_enrich_properties',
            trim($params['auto_enrich_properties'] ?? '91')
        );

        return true;
    }
}
