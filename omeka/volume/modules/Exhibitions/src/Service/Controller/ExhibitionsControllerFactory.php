<?php declare(strict_types=1);

namespace Exhibitions\Service\Controller;

use Exhibitions\Controller\ExhibitionsController;
use Interop\Container\ContainerInterface;
use Laminas\ServiceManager\Factory\FactoryInterface;

class ExhibitionsControllerFactory implements FactoryInterface
{
    public function __invoke(ContainerInterface $container, $requestedName, array $options = null)
    {
        return new ExhibitionsController(
            $container->get('Omeka\Connection'),
            $container->get('Config')['exhibitions'] ?? []
        );
    }
}
