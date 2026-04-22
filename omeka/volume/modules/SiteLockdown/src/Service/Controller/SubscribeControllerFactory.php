<?php declare(strict_types=1);

namespace SiteLockdown\Service\Controller;

use Interop\Container\ContainerInterface;
use Laminas\Http\Client;
use Laminas\ServiceManager\Factory\FactoryInterface;
use SiteLockdown\Controller\SubscribeController;

class SubscribeControllerFactory implements FactoryInterface
{
    public function __invoke(ContainerInterface $container, $requestedName, ?array $options = null): SubscribeController
    {
        $config = $container->get('Config')['site_lockdown'] ?? [];
        return new SubscribeController(new Client(), $config);
    }
}
