<?php declare(strict_types=1);

namespace ShieldedRegistryAssets\Service\Controller;

use Interop\Container\ContainerInterface;
use Laminas\ServiceManager\Factory\FactoryInterface;
use ShieldedRegistryAssets\Controller\Admin\CeremonyController;

class CeremonyControllerFactory implements FactoryInterface
{
    public function __invoke(ContainerInterface $container, $requestedName, array $options = null)
    {
        $settings = $container->get('Omeka\Settings');
        $formElementManager = $container->get('FormElementManager');
        return new CeremonyController($settings, $formElementManager);
    }
}
