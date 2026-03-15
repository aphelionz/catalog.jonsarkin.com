<?php declare(strict_types=1);

namespace EnrichItem\Service\Controller;

use EnrichItem\Controller\EnrichController;
use EnrichItem\Service\AnthropicClient;
use EnrichItem\Service\EnrichmentCache;
use EnrichItem\Service\FieldInstructions;
use Laminas\ServiceManager\Factory\FactoryInterface;
use Psr\Container\ContainerInterface;
use Psr\Log\LoggerInterface;
use Psr\Log\NullLogger;

class EnrichControllerFactory implements FactoryInterface
{
    public function __invoke(ContainerInterface $container, $requestedName, array $options = null)
    {
        $api = $container->get('Omeka\ApiManager');
        $entityManager = $container->get('Omeka\EntityManager');
        $jobDispatcher = $container->get('Omeka\Job\Dispatcher');
        $anthropicClient = $container->get(AnthropicClient::class);
        $enrichmentCache = $container->get(EnrichmentCache::class);
        $fieldInstructions = $container->get(FieldInstructions::class);

        if ($container->has('Omeka\\Logger')) {
            $logger = $container->get('Omeka\\Logger');
        } elseif ($container->has(LoggerInterface::class)) {
            $logger = $container->get(LoggerInterface::class);
        } else {
            $logger = new NullLogger();
        }

        $config = $container->get('Config');
        $moduleConfig = $config['enrich_item'] ?? [];

        return new EnrichController($api, $logger, $moduleConfig, $entityManager, $jobDispatcher, $anthropicClient, $enrichmentCache, $fieldInstructions);
    }
}
