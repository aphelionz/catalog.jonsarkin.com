<?php declare(strict_types=1);

namespace Press\Service\Controller;

use Press\Controller\PressController;
use Interop\Container\ContainerInterface;
use Laminas\ServiceManager\Factory\FactoryInterface;

class PressControllerFactory implements FactoryInterface
{
    public function __invoke(ContainerInterface $container, $requestedName, array $options = null)
    {
        return new PressController(
            $container->get('Omeka\Connection'),
            $container->get('Config')['press'] ?? []
        );
    }
}
