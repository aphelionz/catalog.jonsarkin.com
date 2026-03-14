<?php declare(strict_types=1);

namespace IconographicProfile\Service\Controller;

use IconographicProfile\Controller\IconographyController;
use Laminas\ServiceManager\Factory\FactoryInterface;
use Psr\Container\ContainerInterface;

class IconographyControllerFactory implements FactoryInterface
{
    public function __invoke(ContainerInterface $container, $requestedName, array $options = null)
    {
        $connection = $container->get('Omeka\Connection');
        return new IconographyController($connection);
    }
}
