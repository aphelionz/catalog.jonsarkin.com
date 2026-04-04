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

        // EnrichItem services (optional — suggestion feature degrades gracefully if missing)
        $anthropicClient = $container->has(\EnrichItem\Service\AnthropicClient::class)
            ? $container->get(\EnrichItem\Service\AnthropicClient::class)
            : null;
        $enrichmentCache = $container->has(\EnrichItem\Service\EnrichmentCache::class)
            ? $container->get(\EnrichItem\Service\EnrichmentCache::class)
            : null;

        return new EditorController($entityManager, $anthropicClient, $enrichmentCache);
    }
}
