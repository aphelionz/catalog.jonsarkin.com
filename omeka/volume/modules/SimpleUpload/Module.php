<?php declare(strict_types=1);

namespace SimpleUpload;

use Laminas\Loader\StandardAutoloader;
use Laminas\Mvc\MvcEvent;
use Laminas\Permissions\Acl\Resource\GenericResource as Resource;
use Omeka\Module\AbstractModule;
use Omeka\Permissions\Acl;

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
        $resourceId = Controller\IndexController::class;

        if (!$acl->hasResource($resourceId)) {
            $acl->addResource(new Resource($resourceId));
        }

        $acl->deny(null, $resourceId);
        $acl->allow(
            [
                Acl::ROLE_GLOBAL_ADMIN,
                Acl::ROLE_SITE_ADMIN,
                Acl::ROLE_EDITOR,
            ],
            $resourceId
        );
    }
}
