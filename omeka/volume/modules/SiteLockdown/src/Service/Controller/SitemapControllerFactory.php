<?php declare(strict_types=1);

namespace SiteLockdown\Service\Controller;

use Interop\Container\ContainerInterface;
use Laminas\ServiceManager\Factory\FactoryInterface;
use SiteLockdown\Controller\SitemapController;

class SitemapControllerFactory implements FactoryInterface
{
    public function __invoke(ContainerInterface $container, $requestedName, array $options = null)
    {
        return new SitemapController($container->get('Omeka\Connection'));
    }
}
