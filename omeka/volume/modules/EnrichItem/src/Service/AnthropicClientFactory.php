<?php declare(strict_types=1);

namespace EnrichItem\Service;

use Laminas\ServiceManager\Factory\FactoryInterface;
use Psr\Container\ContainerInterface;
use Psr\Log\LoggerInterface;
use Psr\Log\NullLogger;

class AnthropicClientFactory implements FactoryInterface
{
    public function __invoke(ContainerInterface $container, $requestedName, array $options = null)
    {
        $apiKey = getenv('ANTHROPIC_API_KEY') ?: '';

        $config = $container->get('Config');
        $moduleConfig = $config['enrich_item'] ?? [];
        $defaultModel = $moduleConfig['default_model'] ?? 'haiku';

        $httpClient = $container->get('Omeka\HttpClient');

        if ($container->has('Omeka\\Logger')) {
            $logger = $container->get('Omeka\\Logger');
        } elseif ($container->has(LoggerInterface::class)) {
            $logger = $container->get(LoggerInterface::class);
        } else {
            $logger = new NullLogger();
        }

        return new AnthropicClient($apiKey, $defaultModel, $httpClient, $logger);
    }
}
