<?php declare(strict_types=1);

namespace IconographicProfile;

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

        $acl = $event->getApplication()->getServiceManager()->get('Omeka\Acl');
        foreach ([Controller\IconographyController::class, Controller\MentionsController::class] as $resourceId) {
            if (method_exists($acl, 'hasResource') && !$acl->hasResource($resourceId)) {
                $acl->addResource(new Resource($resourceId));
            }
            $acl->allow(null, $resourceId);
        }
    }
}
