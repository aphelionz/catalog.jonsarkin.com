<?php declare(strict_types=1);

namespace Exhibitions;

use Laminas\Mvc\MvcEvent;
use Laminas\Permissions\Acl\Resource\GenericResource as Resource;
use Omeka\Module\AbstractModule;

class Module extends AbstractModule
{
    public function getConfig(): array
    {
        return include __DIR__ . '/config/module.config.php';
    }

    public function onBootstrap(MvcEvent $event): void
    {
        parent::onBootstrap($event);
        $acl = $event->getApplication()->getServiceManager()->get('Omeka\Acl');

        $resource = Controller\ExhibitionsController::class;
        if (!$acl->hasResource($resource)) {
            $acl->addResource(new Resource($resource));
        }
        $acl->allow(null, $resource);
    }
}
