<?php declare(strict_types=1);

namespace EnrichItem\Service;

use Laminas\ServiceManager\Factory\FactoryInterface;
use Psr\Container\ContainerInterface;

class FieldInstructionsFactory implements FactoryInterface
{
    public function __invoke(ContainerInterface $container, $requestedName, array $options = null)
    {
        $em = $container->get('Omeka\EntityManager');
        $conn = $em->getConnection();

        $service = new FieldInstructions($conn);
        $service->ensureTable();

        return $service;
    }
}
