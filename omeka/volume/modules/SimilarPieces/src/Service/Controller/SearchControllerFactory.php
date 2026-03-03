<?php declare(strict_types=1);

namespace SimilarPieces\Service\Controller;

use Laminas\ServiceManager\Factory\FactoryInterface;
use Psr\Log\LoggerInterface;
use Psr\Log\NullLogger;
use SimilarPieces\Config\ModuleConfig;
use SimilarPieces\Controller\SearchController;
use Psr\Container\ContainerInterface;

class SearchControllerFactory implements FactoryInterface
{
    public function __invoke(ContainerInterface $container, $requestedName, array $options = null)
    {
        $httpClient = $container->get('Omeka\HttpClient');
        $api = $container->get('Omeka\ApiManager');
        if ($container->has('Omeka\\Logger')) {
            $logger = $container->get('Omeka\\Logger');
        } elseif ($container->has(LoggerInterface::class)) {
            $logger = $container->get(LoggerInterface::class);
        } else {
            $logger = new NullLogger();
        }
        $moduleConfig = ModuleConfig::resolve($container);

        return new SearchController($httpClient, $api, $logger, $moduleConfig);
    }
}
