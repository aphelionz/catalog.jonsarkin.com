<?php declare(strict_types=1);

namespace RapidEditor;

use Laminas\EventManager\Event;
use Laminas\EventManager\SharedEventManagerInterface;
use Laminas\Loader\StandardAutoloader;
use Laminas\Mvc\MvcEvent;
use Laminas\Permissions\Acl\Resource\GenericResource as Resource;
use Omeka\Api\Adapter\ItemAdapter;
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
        $resourceId = Controller\EditorController::class;

        if (method_exists($acl, 'hasResource') && !$acl->hasResource($resourceId)) {
            $acl->addResource(new Resource($resourceId));
        }
        $acl->allow(['editor', 'global_admin', 'site_admin'], $resourceId);
    }

    public function attachListeners(SharedEventManagerInterface $sharedEventManager): void
    {
        $sharedEventManager->attach(
            ItemAdapter::class,
            'api.hydrate.post',
            [$this, 'enforcePrivateTemplateProperties']
        );
    }

    /**
     * Omeka applies a resource template's "Private" flag only as a default in
     * the admin form. Enforce it on every item write (admin form, RapidEditor,
     * EnrichItem, BulkEdit) so internal fields such as schema:box and
     * curation:note stay private on the public site and API.
     */
    public function enforcePrivateTemplateProperties(Event $event): void
    {
        $item = $event->getParam('entity');
        $template = $item->getResourceTemplate();
        if (!$template) {
            return;
        }

        $privatePropertyIds = [];
        foreach ($template->getResourceTemplateProperties() as $templateProperty) {
            if ($templateProperty->isPrivate()) {
                $privatePropertyIds[$templateProperty->getProperty()->getId()] = true;
            }
        }

        foreach ($item->getValues() as $value) {
            if (isset($privatePropertyIds[$value->getProperty()->getId()])) {
                $value->setIsPublic(false);
            }
        }
    }
}
