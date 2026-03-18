<?php declare(strict_types=1);

namespace RapidEditor\Service\Controller;

use Laminas\ServiceManager\Factory\FactoryInterface;
use Psr\Container\ContainerInterface;
use RapidEditor\Controller\EditorController;

class EditorControllerFactory implements FactoryInterface
{
    public function __invoke(ContainerInterface $container, $requestedName, array $options = null)
    {
        $entityManager = $container->get('Omeka\EntityManager');
        return new EditorController($entityManager);
    }
}
