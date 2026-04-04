<?php declare(strict_types=1);

namespace CollectorSubmission\Service\Controller;

use CollectorSubmission\Controller\Admin\SubmissionController;
use Interop\Container\ContainerInterface;
use Laminas\ServiceManager\Factory\FactoryInterface;

class SubmissionControllerFactory implements FactoryInterface
{
    public function __invoke(ContainerInterface $container, $requestedName, ?array $options = null): SubmissionController
    {
        $config = $container->get('Config')['collector_submission'] ?? [];
        $settings = $container->get('Omeka\Settings');
        $defaults = \CollectorSubmission\Module::emailDefaults();
        foreach ($defaults as $key => $default) {
            $config['email'][$key] = $settings->get($key, $default);
        }
        return new SubmissionController(
            $container->get('Omeka\Connection'),
            $container->get('Omeka\Mailer'),
            $config
        );
    }
}
