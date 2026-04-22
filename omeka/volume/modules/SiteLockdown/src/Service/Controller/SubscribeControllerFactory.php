<?php declare(strict_types=1);

namespace SiteLockdown\Service\Controller;

use Interop\Container\ContainerInterface;
use Laminas\ServiceManager\Factory\FactoryInterface;
use SiteLockdown\Controller\SubscribeController;

class SubscribeControllerFactory implements FactoryInterface
{
    public function __invoke(ContainerInterface $container, $requestedName, ?array $options = null): SubscribeController
    {
        return new SubscribeController($container->get('Omeka\Connection'));
    }
}
