<?php declare(strict_types=1);

namespace CollectorSubmission;

use Laminas\Mvc\Controller\AbstractController;
use Laminas\Mvc\MvcEvent;
use Laminas\Permissions\Acl\Resource\GenericResource as Resource;
use Laminas\ServiceManager\ServiceLocatorInterface;
use Laminas\View\Renderer\PhpRenderer;
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

    public function upgrade($oldVersion, $newVersion, ServiceLocatorInterface $services): void
    {
        $conn = $services->get('Omeka\Connection');
        if (version_compare($oldVersion, '1.1.0', '<')) {
            $conn->exec('ALTER TABLE collector_submission ADD COLUMN item_id INT UNSIGNED DEFAULT NULL AFTER admin_notes');
            $conn->exec('ALTER TABLE collector_submission ADD COLUMN rejection_reason TEXT DEFAULT NULL AFTER item_id');
        }
        if (version_compare($oldVersion, '1.2.0', '<')) {
            $conn->exec('ALTER TABLE collector_submission ADD COLUMN dimensions_height VARCHAR(50) DEFAULT NULL AFTER exhibition_history');
            $conn->exec('ALTER TABLE collector_submission ADD COLUMN dimensions_width VARCHAR(50) DEFAULT NULL AFTER dimensions_height');
            $conn->exec("ALTER TABLE collector_submission ADD COLUMN dimensions_unit VARCHAR(10) NOT NULL DEFAULT 'in' AFTER dimensions_width");
        }
        if (version_compare($oldVersion, '1.3.0', '<')) {
            $conn->exec(<<<'SQL'
CREATE TABLE collector_submission_item (
    submission_id INT UNSIGNED NOT NULL,
    item_id       INT UNSIGNED NOT NULL,
    sort_order    INT UNSIGNED NOT NULL DEFAULT 0,
    PRIMARY KEY (submission_id, item_id),
    INDEX idx_submission (submission_id),
    INDEX idx_item (item_id),
    CONSTRAINT fk_csi_submission FOREIGN KEY (submission_id)
        REFERENCES collector_submission(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
SQL);
        }
    }

    public function uninstall(ServiceLocatorInterface $services): void
    {
        $conn = $services->get('Omeka\Connection');
        $conn->exec('DROP TABLE IF EXISTS collector_submission_item');
        $conn->exec('DROP TABLE IF EXISTS collector_submission');
    }

    public function getConfigForm(PhpRenderer $renderer): string
    {
        $settings = $this->getServiceLocator()->get('Omeka\Settings');
        $d = self::emailDefaults();

        $fields = [
            [
                'key' => 'thankyou',
                'legend' => 'Thank-You Email',
                'hint' => 'Sent to the collector immediately after submission.',
                'placeholders' => '{name}',
            ],
            [
                'key' => 'approval',
                'legend' => 'Approval Email',
                'hint' => 'Sent when a submission is accepted.',
                'placeholders' => '{name}, {item_url}',
            ],
            [
                'key' => 'rejection',
                'legend' => 'Rejection Email',
                'hint' => 'Sent when a submission is rejected.',
                'placeholders' => '{name}, {reason}',
            ],
        ];

        $html = '';
        foreach ($fields as $f) {
            $subjectKey = "collector_submission_{$f['key']}_subject";
            $bodyKey = "collector_submission_{$f['key']}_body";
            $subjectVal = htmlspecialchars($settings->get($subjectKey, $d[$subjectKey]), ENT_QUOTES);
            $bodyVal = htmlspecialchars($settings->get($bodyKey, $d[$bodyKey]), ENT_QUOTES);

            $html .= <<<HTML
<fieldset>
    <legend>{$f['legend']}</legend>
    <p class="field-description" style="margin:0 0 1rem;color:#888;font-size:0.85rem">{$f['hint']}<br>Placeholders: <code>{$f['placeholders']}</code></p>
    <div class="field">
        <div class="field-meta"><label for="{$subjectKey}">Subject</label></div>
        <div class="inputs"><input type="text" id="{$subjectKey}" name="{$subjectKey}" value="{$subjectVal}" style="width:100%"></div>
    </div>
    <div class="field">
        <div class="field-meta"><label for="{$bodyKey}">Body</label></div>
        <div class="inputs"><textarea id="{$bodyKey}" name="{$bodyKey}" rows="6" style="width:100%;font-family:inherit">{$bodyVal}</textarea></div>
    </div>
</fieldset>
HTML;
        }
        return $html;
    }

    public function handleConfigForm(AbstractController $controller): bool
    {
        $settings = $this->getServiceLocator()->get('Omeka\Settings');
        $params = $controller->getRequest()->getPost();
        $keys = [
            'collector_submission_thankyou_subject',
            'collector_submission_thankyou_body',
            'collector_submission_approval_subject',
            'collector_submission_approval_body',
            'collector_submission_rejection_subject',
            'collector_submission_rejection_body',
        ];
        foreach ($keys as $key) {
            $settings->set($key, $params[$key] ?? '');
        }
        return true;
    }

    public static function emailDefaults(): array
    {
        return [
            'collector_submission_thankyou_subject' => 'Thanks for your submission to the Sarkin Catalog',
            'collector_submission_thankyou_body' => "Hi {name},\n\nThanks for submitting to the Sarkin Catalog — I really appreciate you taking the time. I'll review your submission and get back to you soon.\n\nBest,\nMark",
            'collector_submission_approval_subject' => 'Your piece is now in the Sarkin Catalog',
            'collector_submission_approval_body' => "Hi {name},\n\nGreat news — your piece has been added to the Sarkin Catalog. You can view it here:\n\n{item_url}\n\nThanks for contributing to the catalog. If you have any questions or need to update any details, just reply to this email.\n\nBest,\nMark",
            'collector_submission_rejection_subject' => 'Update on your Sarkin Catalog submission',
            'collector_submission_rejection_body' => "Hi {name},\n\nThanks for thinking of the Sarkin Catalog and taking the time to submit your piece. After reviewing it, I'm not able to include it in the catalog at this time.\n\n{reason}\n\nI appreciate your interest, and please don't hesitate to reach out if you have any questions or would like to submit another piece in the future.\n\nBest,\nMark",
        ];
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
