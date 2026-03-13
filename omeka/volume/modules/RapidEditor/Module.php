<?php declare(strict_types=1);

namespace RapidEditor;

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
        $resourceId = Controller\EditorController::class;

        if (method_exists($acl, 'hasResource') && !$acl->hasResource($resourceId)) {
            $acl->addResource(new Resource($resourceId));
        }
        $acl->allow(['editor', 'global_admin', 'site_admin'], $resourceId);
    }
}
