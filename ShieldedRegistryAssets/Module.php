<?php declare(strict_types=1);

namespace ShieldedRegistryAssets;

use Laminas\Loader\StandardAutoloader;
use Laminas\Mvc\MvcEvent;
use Laminas\Permissions\Acl\Resource\GenericResource as Resource;
use Omeka\Module\AbstractModule;
use ShieldedRegistryAssets\Controller\Admin\CeremonyController;

class Module extends AbstractModule
{
    private $services;

    public function getConfig(): array
    {
        return include __DIR__ . '/config/module.config.php';
    }

    public function getAutoloaderConfig(): array
    {
        return [
            StandardAutoloader::class => [
                'namespaces' => [
                    __NAMESPACE__ => __DIR__ . '/src',
                ],
            ],
        ];
    }

    public function onBootstrap(MvcEvent $event): void
    {
        parent::onBootstrap($event);

        $this->services = $event->getApplication()->getServiceManager();

        $acl = $this->services->get('Omeka\Acl');
        $resourceId = CeremonyController::class;

        if (method_exists($acl, 'hasResource') && !$acl->hasResource($resourceId)) {
            $acl->addResource(new Resource($resourceId));
        }

        $acl->allow(
            ['global_admin', 'site_admin'],
            $resourceId
        );

        // Allow public (unauthenticated) access to the .well-known endpoint
        $acl->allow(
            null,
            $resourceId,
            'wellKnown'
        );
    }

    /**
     * Return ceremony configuration from Omeka settings.
     */
    public static function getCeremonyConfig(object $settings): array
    {
        $json = $settings->get('sra_ceremony_config', '{}');
        $config = json_decode($json, true);
        return is_array($config) ? $config : [];
    }

    /**
     * Return ceremony result (PK_R, genesis hash) from Omeka settings.
     */
    public static function getCeremonyResult(object $settings): array
    {
        $json = $settings->get('sra_ceremony_result', '{}');
        $result = json_decode($json, true);
        return is_array($result) ? $result : [];
    }

    /**
     * Determine ceremony workflow state.
     *
     * States: NOT_CONFIGURED → ESTATE_CONFIGURED → KEYS_GENERATED → IDENTITY_BOUND
     */
    public static function getCeremonyState(object $settings): string
    {
        $config = self::getCeremonyConfig($settings);
        $result = self::getCeremonyResult($settings);

        if (!empty($result['dns_verified'])) {
            return 'IDENTITY_BOUND';
        }
        if (!empty($result['pk_r'])) {
            return 'KEYS_GENERATED';
        }
        if (!empty($config['ra_name']) && !empty($config['party_mode'])) {
            return 'ESTATE_CONFIGURED';
        }

        return 'NOT_CONFIGURED';
    }
}
