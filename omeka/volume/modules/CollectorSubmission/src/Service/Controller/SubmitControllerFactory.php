<?php declare(strict_types=1);

namespace CollectorSubmission\Service\Controller;

use CollectorSubmission\Controller\SubmitController;
use Interop\Container\ContainerInterface;
use Laminas\ServiceManager\Factory\FactoryInterface;

class SubmitControllerFactory implements FactoryInterface
{
    public function __invoke(ContainerInterface $container, $requestedName, ?array $options = null): SubmitController
    {
        $config = $container->get('Config')['collector_submission'] ?? [];
        return new SubmitController(
            $container->get('Omeka\Connection'),
            $container->get('Omeka\Mailer'),
            $config
        );
    }
}
