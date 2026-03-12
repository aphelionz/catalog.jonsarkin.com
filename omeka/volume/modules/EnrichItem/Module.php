<?php declare(strict_types=1);

namespace EnrichItem;

use Laminas\EventManager\Event;
use Laminas\EventManager\SharedEventManagerInterface;
use Laminas\Loader\StandardAutoloader;
use Laminas\Mvc\MvcEvent;
use Laminas\Permissions\Acl\Resource\GenericResource as Resource;
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
        // Inject the enrich panel on admin item show pages
        $sharedEventManager->attach(
            'Omeka\Controller\Admin\Item',
            'view.show.section_nav',
            [$this, 'addEnrichSectionNav']
        );
        $sharedEventManager->attach(
            'Omeka\Controller\Admin\Item',
            'view.show.after',
            [$this, 'appendEnrichPanel']
        );

        // Auto-trigger enrichment on item create
        $sharedEventManager->attach(
            'Omeka\Api\Adapter\ItemAdapter',
            'api.create.post',
            [$this, 'onItemCreate']
        );
    }

    public function addEnrichSectionNav(Event $event): void
    {
        $sectionNav = $event->getParam('section_nav');
        $sectionNav['enrich'] = 'Enrich';
        $event->setParam('section_nav', $sectionNav);
    }

    public function appendEnrichPanel(Event $event): void
    {
        $view = $event->getTarget();
        $item = $view->item;
        $itemId = $item->id();
        echo $view->partial('enrich-item/enrich/panel', [
            'itemId' => $itemId,
            'item' => $item,
        ]);
    }

    public function onItemCreate(Event $event): void
    {
        $request = $event->getParam('request');
        $response = $event->getParam('response');
        $item = $response->getContent();

        // Only auto-enrich items with the Artwork template
        $resourceTemplate = $item->getResourceTemplate();
        if (!$resourceTemplate) {
            return;
        }

        $services = $this->getServiceLocator();
        $config = $services->get('Config');
        $moduleConfig = $config['enrich_item'] ?? [];
        $templateId = (int) ($moduleConfig['resource_template_id'] ?? 2);

        if ($resourceTemplate->getId() !== $templateId) {
            return;
        }

        // Check if item has media
        $media = $item->getMedia();
        if ($media->isEmpty()) {
            return;
        }

        // Dispatch background enrichment job
        $dispatcher = $services->get('Omeka\Job\Dispatcher');
        $dispatcher->dispatch(Job\EnrichBatch::class, [
            'item_ids' => [$item->getId()],
        ]);
    }
}
