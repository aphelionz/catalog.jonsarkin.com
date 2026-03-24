<?php declare(strict_types=1);

namespace CollectorSubmission\Service\Controller;

use CollectorSubmission\Controller\Admin\SubmissionController;
use Interop\Container\ContainerInterface;
use Laminas\ServiceManager\Factory\FactoryInterface;

class SubmissionControllerFactory implements FactoryInterface
{
    public function __invoke(ContainerInterface $container, $requestedName, ?array $options = null): SubmissionController
    {
        return new SubmissionController(
            $container->get('Omeka\Connection')
        );
    }
}
