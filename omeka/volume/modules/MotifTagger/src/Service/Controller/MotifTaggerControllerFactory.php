<?php declare(strict_types=1);

namespace MotifTagger\Service\Controller;

use MotifTagger\Controller\MotifTaggerController;
use Laminas\ServiceManager\Factory\FactoryInterface;
use Psr\Container\ContainerInterface;

class MotifTaggerControllerFactory implements FactoryInterface
{
    public function __invoke(ContainerInterface $container, $requestedName, array $options = null)
    {
        $httpClient = $container->get('Omeka\HttpClient');
        $api = $container->get('Omeka\ApiManager');
        $entityManager = $container->get('Omeka\EntityManager');
        $settings = $container->get('Omeka\Settings');
        $dispatcher = $container->get('Omeka\Job\Dispatcher');
        $config = $container->get('Config');
        $moduleConfig = $config['motif_tagger'] ?? [];

        return new MotifTaggerController($httpClient, $api, $entityManager, $settings, $dispatcher, $moduleConfig);
    }
}
