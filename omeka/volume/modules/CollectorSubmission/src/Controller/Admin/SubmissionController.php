<?php declare(strict_types=1);

namespace CollectorSubmission\Controller\Admin;

use Doctrine\DBAL\Connection;
use Laminas\Mvc\Controller\AbstractActionController;
use Laminas\Validator\Csrf as CsrfValidator;
use Laminas\View\Model\JsonModel;
use Laminas\View\Model\ViewModel;
use Omeka\Stdlib\Mailer;

class SubmissionController extends AbstractActionController
{
    private Connection $conn;
    private Mailer $mailer;
    private array $config;

    public function __construct(Connection $conn, Mailer $mailer, array $config)
    {
        $this->conn = $conn;
        $this->mailer = $mailer;
        $this->config = $config;
    }

    public function indexAction()
    {
        $status = $this->params()->fromQuery('status');

        $qb = $this->conn->createQueryBuilder()
            ->select('*')
            ->from('collector_submission')
            ->orderBy('created', 'DESC');

        if ($status && in_array($status, ['new', 'reviewed', 'accepted', 'rejected'], true)) {
            $qb->where('status = :status')->setParameter('status', $status);
        }

        $submissions = $qb->execute()->fetchAllAssociative();

        // Decode files JSON for each submission
        foreach ($submissions as &$s) {
            $s['files'] = json_decode($s['files'] ?? '[]', true) ?: [];
        }
        unset($s);

        // Count by status for filter tabs
        $counts = $this->conn->fetchAllAssociative(
            'SELECT status, COUNT(*) as cnt FROM collector_submission GROUP BY status'
        );
        $statusCounts = ['all' => 0];
        foreach ($counts as $row) {
            $statusCounts[$row['status']] = (int) $row['cnt'];
            $statusCounts['all'] += (int) $row['cnt'];
        }

        $view = new ViewModel([
            'submissions' => $submissions,
            'statusCounts' => $statusCounts,
            'currentStatus' => $status,
        ]);
        $view->setTemplate('collector-submission/admin/submission/index');
        return $view;
    }

    public function showAction()
    {
        $id = (int) $this->params('id');
        $submission = $this->conn->fetchAssociative(
            'SELECT * FROM collector_submission WHERE id = ?',
            [$id]
        );

        if (!$submission) {
            return $this->redirect()->toRoute('admin/collector-submissions');
        }

        $submission['files'] = json_decode($submission['files'] ?? '[]', true) ?: [];

        // Back-compat: the mapping table arrived in 1.3.0. If the migration hasn't
        // been applied yet (code deployed, "Update Database" not yet clicked), the
        // SELECT throws; fall back to the single-item view derived from item_id.
        try {
            $items = $this->conn->fetchAllAssociative(
                'SELECT item_id, sort_order FROM collector_submission_item
                 WHERE submission_id = ? ORDER BY sort_order',
                [$id]
            );
        } catch (\Throwable $e) {
            $items = [];
        }
        if (empty($items) && !empty($submission['item_id'])) {
            $items = [['item_id' => (int) $submission['item_id'], 'sort_order' => 0]];
        }
        $submission['items'] = $items;

        $csrf = new CsrfValidator(['name' => 'cs_rotate_' . $id, 'timeout' => 3600]);
        $csrfToken = $csrf->getHash();

        $view = new ViewModel([
            'submission' => $submission,
            'csrfToken' => $csrfToken,
        ]);
        $view->setTemplate('collector-submission/admin/submission/show');
        return $view;
    }

    public function rotateImageAction()
    {
        $id = (int) $this->params('id');
        $request = $this->getRequest();

        if (!$request->isPost()) {
            return new JsonModel(['ok' => false, 'error' => 'POST required']);
        }

        $submission = $this->conn->fetchAssociative(
            'SELECT * FROM collector_submission WHERE id = ?',
            [$id]
        );
        if (!$submission) {
            return new JsonModel(['ok' => false, 'error' => 'Submission not found']);
        }

        if (!empty($submission['item_id'])) {
            return new JsonModel([
                'ok' => false,
                'error' => 'Catalog item already exists — rotate media in the Omeka item editor.',
            ]);
        }

        $csrf = new CsrfValidator(['name' => 'cs_rotate_' . $id, 'timeout' => 3600]);
        $token = $request->getPost('csrf');
        if (!$token || !$csrf->isValid($token)) {
            return new JsonModel(['ok' => false, 'error' => 'CSRF token invalid or expired']);
        }

        $degrees = (int) $request->getPost('degrees');
        if (!in_array($degrees, [-90, 90, 180], true)) {
            return new JsonModel(['ok' => false, 'error' => 'Invalid degrees']);
        }
        // rotate_hdr.py and `convert` both want positive degrees.
        $positiveDeg = $degrees < 0 ? 360 + $degrees : $degrees;

        $idx = (int) $request->getPost('index');
        $files = json_decode($submission['files'] ?? '[]', true) ?: [];
        if (!isset($files[$idx])) {
            return new JsonModel(['ok' => false, 'error' => 'Invalid index']);
        }

        $relPath = $files[$idx];
        $absPath = OMEKA_PATH . '/files/' . $relPath;
        if (!file_exists($absPath)) {
            return new JsonModel(['ok' => false, 'error' => 'File missing on disk']);
        }

        $ext = strtolower(pathinfo($relPath, PATHINFO_EXTENSION));
        $dir = dirname($absPath);
        $tmp = $dir . '/.rot_' . bin2hex(random_bytes(4)) . '.tmp';

        if (in_array($ext, ['jpg', 'jpeg'], true)) {
            // Lossless rotate + MPF/HDR gain-map reassembly (see CLAUDE.md footgun
            // on mogrify destroying Apple HDR secondary images).
            $scriptPath = realpath(__DIR__ . '/../../../scripts/rotate_hdr.py');
            if (!$scriptPath) {
                return new JsonModel(['ok' => false, 'error' => 'Rotation script missing']);
            }
            $cmd = sprintf(
                'python3 %s %d %s %s 2>&1',
                escapeshellarg($scriptPath),
                $positiveDeg,
                escapeshellarg($absPath),
                escapeshellarg($tmp)
            );
        } elseif ($ext === 'png') {
            $cmd = sprintf(
                'convert %s -rotate %d -auto-orient %s 2>&1',
                escapeshellarg($absPath),
                $positiveDeg,
                escapeshellarg($tmp)
            );
        } else {
            return new JsonModel(['ok' => false, 'error' => 'Unsupported format: ' . $ext]);
        }

        $out = [];
        $rc = 0;
        exec($cmd, $out, $rc);
        if ($rc !== 0 || !file_exists($tmp) || filesize($tmp) === 0) {
            @unlink($tmp);
            return new JsonModel([
                'ok' => false,
                'error' => 'Rotation failed: ' . implode(' | ', array_slice($out, -3)),
            ]);
        }

        if (!rename($tmp, $absPath)) {
            @unlink($tmp);
            return new JsonModel(['ok' => false, 'error' => 'Failed to replace file']);
        }

        return new JsonModel(['ok' => true]);
    }

    public function statusAction()
    {
        $id = (int) $this->params('id');
        $request = $this->getRequest();

        if (!$request->isPost()) {
            return $this->redirect()->toRoute('admin/collector-submissions/show', ['id' => $id]);
        }

        $submission = $this->conn->fetchAssociative(
            'SELECT * FROM collector_submission WHERE id = ?',
            [$id]
        );
        if (!$submission) {
            return $this->redirect()->toRoute('admin/collector-submissions');
        }

        $newStatus = $request->getPost('status');
        $adminNotes = $request->getPost('admin_notes');
        $rejectionReason = $request->getPost('rejection_reason');

        if (!in_array($newStatus, ['new', 'reviewed', 'accepted', 'rejected'], true)) {
            return $this->redirect()->toRoute('admin/collector-submissions/show', ['id' => $id]);
        }

        // Guard: can't approve without a linked item
        if ($newStatus === 'accepted' && empty($submission['item_id'])) {
            $this->messenger()->addError('Create a catalog item before approving this submission.');
            return $this->redirect()->toRoute('admin/collector-submissions/show', ['id' => $id]);
        }

        // Guard: rejection needs a reason
        if ($newStatus === 'rejected' && empty(trim($rejectionReason ?? ''))) {
            $this->messenger()->addError('Please provide a reason when rejecting a submission.');
            return $this->redirect()->toRoute('admin/collector-submissions/show', ['id' => $id]);
        }

        $updateData = [
            'status' => $newStatus,
            'admin_notes' => $adminNotes ?: null,
        ];
        if ($newStatus === 'rejected') {
            $updateData['rejection_reason'] = $rejectionReason;
        }

        $this->conn->update('collector_submission', $updateData, ['id' => $id]);

        // Refresh submission with updated fields
        $submission = array_merge($submission, $updateData);

        // Send collector emails on status change
        if ($newStatus === 'accepted') {
            $this->sendApprovalEmail($submission);
        } elseif ($newStatus === 'rejected') {
            $this->sendRejectionEmail($submission);
        }

        $this->messenger()->addSuccess('Submission status updated.');
        return $this->redirect()->toRoute('admin/collector-submissions/show', ['id' => $id]);
    }

    public function createItemAction()
    {
        $id = (int) $this->params('id');
        $request = $this->getRequest();

        if (!$request->isPost()) {
            return $this->redirect()->toRoute('admin/collector-submissions/show', ['id' => $id]);
        }

        $submission = $this->conn->fetchAssociative(
            'SELECT * FROM collector_submission WHERE id = ?',
            [$id]
        );
        if (!$submission) {
            return $this->redirect()->toRoute('admin/collector-submissions');
        }

        if (!empty($submission['item_id'])) {
            $this->messenger()->addWarning('A catalog item already exists for this submission.');
            return $this->redirect()->toRoute('admin/collector-submissions/show', ['id' => $id]);
        }

        $files = json_decode($submission['files'] ?? '[]', true) ?: [];
        // Drop any files that went missing from disk.
        $files = array_values(array_filter(
            $files,
            fn($p) => file_exists(OMEKA_PATH . '/files/' . $p)
        ));
        if (empty($files)) {
            $this->messenger()->addError('No uploaded photos found on disk for this submission.');
            return $this->redirect()->toRoute('admin/collector-submissions/show', ['id' => $id]);
        }

        $grouping = $request->getPost('grouping', 'one');
        $splitEach = ($grouping === 'many' && count($files) > 1);

        try {
            if ($splitEach) {
                $itemIds = [];
                foreach ($files as $i => $relativePath) {
                    $payload = $this->buildItemPayload($submission, [$relativePath], $i + 1, count($files));
                    $response = $this->api()->create('items', $payload);
                    $itemId = $response->getContent()->id();
                    $itemIds[] = $itemId;
                    $this->conn->insert('collector_submission_item', [
                        'submission_id' => $id,
                        'item_id' => $itemId,
                        'sort_order' => $i,
                    ]);
                    $this->dispatchEnrichAndIngest($itemId);
                }
                // Keep submission.item_id pointing at the first id so existing guards
                // (status-update, approval-email {item_url}) keep working unchanged.
                $this->conn->update('collector_submission', ['item_id' => $itemIds[0]], ['id' => $id]);

                $linkList = implode(', ', array_map(fn($iid) => '#' . $iid, $itemIds));
                $this->messenger()->addSuccess(sprintf(
                    '%d catalog items created (%s). Enrichment queued for each.',
                    count($itemIds),
                    $linkList
                ));
                return $this->redirect()->toRoute('admin/collector-submissions/show', ['id' => $id]);
            }

            // Single-item path (default / 1 photo / grouping=one)
            $payload = $this->buildItemPayload($submission, $files);
            $response = $this->api()->create('items', $payload);
            $itemId = $response->getContent()->id();

            $this->conn->update('collector_submission', ['item_id' => $itemId], ['id' => $id]);
            $this->conn->insert('collector_submission_item', [
                'submission_id' => $id,
                'item_id' => $itemId,
                'sort_order' => 0,
            ]);
            $this->dispatchEnrichAndIngest($itemId);

            $this->messenger()->addSuccess("Catalog item #{$itemId} created. Edit it below, then return to approve the submission.");
            return $this->redirect()->toUrl('/admin/item/' . $itemId . '/edit');
        } catch (\Exception $e) {
            $this->messenger()->addError('Failed to create item: ' . $e->getMessage());
            return $this->redirect()->toRoute('admin/collector-submissions/show', ['id' => $id]);
        }
    }

    /**
     * Build the Omeka item payload for a submission + subset of its files.
     *
     * When $partIndex/$partTotal are set, suffixes the title with "#n" so split
     * items are distinguishable before enrichment fills in a real title.
     */
    private function buildItemPayload(
        array $submission,
        array $fileSubset,
        ?int $partIndex = null,
        ?int $partTotal = null
    ): array {
        $mediaSpecs = [];
        foreach ($fileSubset as $relativePath) {
            $mediaSpecs[] = [
                'o:ingester' => 'url',
                'ingest_url' => 'http://localhost/files/' . $relativePath,
                'o:is_public' => true,
            ];
        }

        $collectorName = $submission['collector_name'];

        $creditPref = $submission['credit_preference'] ?? 'full_name';
        $ownerLabels = [
            'full_name' => $collectorName,
            'private' => 'Private Collection',
            'private_city' => 'Private Collection',
            'other' => $collectorName,
        ];
        $ownerValue = $ownerLabels[$creditPref] ?? $collectorName;

        $howLabels = [
            'purchased' => 'Purchased from artist',
            'gift' => 'Gift from artist',
            'boltflash' => 'Boltflash (unsolicited mailing)',
            'secondary' => 'Acquired on secondary market',
            'other' => 'Acquired',
        ];
        $provenance = $howLabels[$submission['how_acquired']] ?? $submission['how_acquired'];
        if (!empty($submission['date_acquired'])) {
            $provenance .= ', ' . $submission['date_acquired'];
        }

        $privateProv = $collectorName . ' / ' . $submission['email'];
        $privateProv .= $submission['may_contact'] ? ' / May contact: Yes' : ' / May contact: No';

        $title = "Untitled — submitted by {$collectorName}";
        if ($partIndex !== null && $partTotal !== null && $partTotal > 1) {
            $title = "Untitled #{$partIndex} of {$partTotal} — submitted by {$collectorName}";
        }

        // Property IDs: title=1, creator=921, owner=72, provenance=51,
        //   height=603, width=1129, presentedAt=74, box=1424
        $itemData = [
            'o:resource_template' => ['o:id' => 2],
            'o:resource_class' => ['o:id' => 225],
            'o:is_public' => true,
            'dcterms:title' => [[
                'type' => 'literal',
                'property_id' => 1,
                '@value' => $title,
            ]],
            'schema:creator' => [[
                'type' => 'literal',
                'property_id' => 921,
                '@value' => 'Jon Sarkin',
            ]],
            'bibo:owner' => [[
                'type' => 'literal',
                'property_id' => 72,
                '@value' => $ownerValue,
            ]],
            'dcterms:provenance' => [[
                'type' => 'literal',
                'property_id' => 51,
                '@value' => $provenance,
            ]],
            'schema:box' => [[
                'type' => 'literal',
                'property_id' => 1424,
                '@value' => $privateProv,
                'is_public' => false,
            ]],
        ];

        $unit = $submission['dimensions_unit'] ?? 'in';
        if (!empty($submission['dimensions_height'])) {
            $value = trim((string) $submission['dimensions_height']);
            if (is_numeric($value)) {
                $inches = $unit === 'cm' ? (float) $value / 2.54 : (float) $value;
                $itemData['schema:height'] = [[
                    'type' => 'literal',
                    'property_id' => 603,
                    '@value' => (string) round($inches, 2),
                ]];
            }
        }
        if (!empty($submission['dimensions_width'])) {
            $value = trim((string) $submission['dimensions_width']);
            if (is_numeric($value)) {
                $inches = $unit === 'cm' ? (float) $value / 2.54 : (float) $value;
                $itemData['schema:width'] = [[
                    'type' => 'literal',
                    'property_id' => 1129,
                    '@value' => (string) round($inches, 2),
                ]];
            }
        }

        if (!empty($submission['exhibition_history'])) {
            $itemData['bibo:presentedAt'] = [[
                'type' => 'literal',
                'property_id' => 74,
                '@value' => $submission['exhibition_history'],
            ]];
        }

        if (!empty($mediaSpecs)) {
            $itemData['o:media'] = $mediaSpecs;
        }

        return $itemData;
    }

    /**
     * Dispatch a single background job that enriches transcription, ingests into
     * CLIP/Qdrant, then sets the item back to private.
     *
     * The item is temporarily public so the Omeka API can read it in job context
     * (background jobs have no authenticated user).
     */
    private function dispatchEnrichAndIngest(int $itemId): void
    {
        try {
            $services = $this->getEvent()->getApplication()->getServiceManager();
            $jobDispatcher = $services->get('Omeka\Job\Dispatcher');
            $jobDispatcher->dispatch('EnrichItem\Job\EnrichAndIngest', [
                'item_id' => $itemId,
            ]);
        } catch (\Throwable $e) {
            error_log('CollectorSubmission: enrich/ingest dispatch failed: ' . $e->getMessage());
        }
    }

    private function sendApprovalEmail(array $submission): void
    {
        try {
            $email = $submission['email'] ?? null;
            if (!$email) {
                return;
            }

            $siteUrl = $this->config['site_url'] ?? 'https://catalog.jonsarkin.com/s/catalog';
            $itemUrl = $siteUrl . '/item/' . $submission['item_id'];
            $name = explode(' ', $submission['collector_name'])[0];

            $replacements = ['{name}' => $name, '{item_url}' => $itemUrl];
            $subject = strtr($this->config['email']['collector_submission_approval_subject'] ?? '', $replacements);
            $body = strtr($this->config['email']['collector_submission_approval_body'] ?? '', $replacements);

            $message = $this->mailer->createMessage();
            $message->setSubject($subject);
            $message->addTo($email);
            $message->setBody($body);
            $this->mailer->send($message);
        } catch (\Throwable $e) {
            error_log('CollectorSubmission approval email failed: ' . $e->getMessage());
            $this->messenger()->addWarning('Status updated but the notification email failed to send.');
        }
    }

    private function sendRejectionEmail(array $submission): void
    {
        try {
            $email = $submission['email'] ?? null;
            if (!$email) {
                return;
            }

            $name = explode(' ', $submission['collector_name'])[0];
            $reason = $submission['rejection_reason'] ?? '';

            $replacements = ['{name}' => $name, '{reason}' => $reason];
            $subject = strtr($this->config['email']['collector_submission_rejection_subject'] ?? '', $replacements);
            $body = strtr($this->config['email']['collector_submission_rejection_body'] ?? '', $replacements);

            $message = $this->mailer->createMessage();
            $message->setSubject($subject);
            $message->addTo($email);
            $message->setBody($body);
            $this->mailer->send($message);
        } catch (\Throwable $e) {
            error_log('CollectorSubmission rejection email failed: ' . $e->getMessage());
            $this->messenger()->addWarning('Status updated but the notification email failed to send.');
        }
    }
}
