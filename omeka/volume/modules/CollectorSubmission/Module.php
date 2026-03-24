<?php declare(strict_types=1);

namespace CollectorSubmission;

use Laminas\Mvc\MvcEvent;
use Laminas\Permissions\Acl\Resource\GenericResource as Resource;
use Laminas\ServiceManager\ServiceLocatorInterface;
use Omeka\Module\AbstractModule;

class Module extends AbstractModule
{
    public function getConfig(): array
    {
        return include __DIR__ . '/config/module.config.php';
    }

    public function install(ServiceLocatorInterface $services): void
    {
        $conn = $services->get('Omeka\Connection');
        $conn->exec(<<<'SQL'
CREATE TABLE collector_submission (
    id INT UNSIGNED AUTO_INCREMENT NOT NULL,
    collector_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL,
    num_pieces INT UNSIGNED NOT NULL DEFAULT 1,
    how_acquired VARCHAR(100) NOT NULL,
    date_acquired VARCHAR(255) DEFAULT NULL,
    description LONGTEXT DEFAULT NULL,
    files LONGTEXT NOT NULL COMMENT '(DC2Type:json)',
    exhibition_history LONGTEXT DEFAULT NULL,
    may_contact TINYINT(1) NOT NULL DEFAULT 1,
    credit_preference VARCHAR(100) NOT NULL DEFAULT 'full_name',
    status VARCHAR(20) NOT NULL DEFAULT 'new',
    admin_notes LONGTEXT DEFAULT NULL,
    created DATETIME NOT NULL,
    PRIMARY KEY (id),
    INDEX idx_status (status),
    INDEX idx_created (created)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
SQL);
    }

    public function uninstall(ServiceLocatorInterface $services): void
    {
        $conn = $services->get('Omeka\Connection');
        $conn->exec('DROP TABLE IF EXISTS collector_submission');
    }

    public function onBootstrap(MvcEvent $event): void
    {
        parent::onBootstrap($event);
        $acl = $event->getApplication()->getServiceManager()->get('Omeka\Acl');

        $submitResource = Controller\SubmitController::class;
        if (!$acl->hasResource($submitResource)) {
            $acl->addResource(new Resource($submitResource));
        }
        $acl->allow(null, $submitResource);

        $adminResource = Controller\Admin\SubmissionController::class;
        if (!$acl->hasResource($adminResource)) {
            $acl->addResource(new Resource($adminResource));
        }
        $acl->allow(['editor', 'global_admin', 'site_admin'], $adminResource);
    }
}
