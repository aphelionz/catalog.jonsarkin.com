<?php declare(strict_types=1);

namespace CollectorSubmission\Controller\Admin;

use Doctrine\DBAL\Connection;
use Laminas\Mvc\Controller\AbstractActionController;
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

        $view = new ViewModel([
            'submission' => $submission,
        ]);
        $view->setTemplate('collector-submission/admin/submission/show');
        return $view;
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

        // Build media specs using url ingester (files are served by Apache)
        $mediaSpecs = [];
        foreach ($files as $index => $relativePath) {
            $absPath = OMEKA_PATH . '/files/' . $relativePath;
            if (!file_exists($absPath)) {
                continue;
            }
            $mediaSpecs[] = [
                'o:ingester' => 'url',
                'ingest_url' => 'http://localhost/files/' . $relativePath,
                'o:is_public' => true,
            ];
        }

        $collectorName = $submission['collector_name'];

        // --- Build Owner value from credit preference ---
        $creditPref = $submission['credit_preference'] ?? 'full_name';
        $ownerLabels = [
            'full_name' => $collectorName,
            'private' => 'Private Collection',
            'private_city' => 'Private Collection',
            'other' => $collectorName,
        ];
        $ownerValue = $ownerLabels[$creditPref] ?? $collectorName;

        // --- Build Provenance from how_acquired + date_acquired ---
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

        // --- Build private provenance for Box field ---
        $privateProv = $collectorName . ' / ' . $submission['email'];
        if ($submission['may_contact']) {
            $privateProv .= ' / May contact: Yes';
        } else {
            $privateProv .= ' / May contact: No';
        }

        // --- Item payload ---
        // Property IDs: title=1, creator=921, owner=72, provenance=51,
        //   height=603, width=1129, presentedAt=74, box=1424
        $itemData = [
            'o:resource_template' => ['o:id' => 2],   // Artwork (Jon Sarkin)
            'o:resource_class' => ['o:id' => 225],     // VisualArtwork
            'o:is_public' => true,
            'dcterms:title' => [[
                'type' => 'literal',
                'property_id' => 1,
                '@value' => "Untitled — submitted by {$collectorName}",
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

        // Height + Width
        if (!empty($submission['dimensions_height'])) {
            $unit = $submission['dimensions_unit'] ?? 'in';
            $itemData['schema:height'] = [[
                'type' => 'literal',
                'property_id' => 603,
                '@value' => $submission['dimensions_height'] . ' ' . $unit,
            ]];
        }
        if (!empty($submission['dimensions_width'])) {
            $unit = $submission['dimensions_unit'] ?? 'in';
            $itemData['schema:width'] = [[
                'type' => 'literal',
                'property_id' => 1129,
                '@value' => $submission['dimensions_width'] . ' ' . $unit,
            ]];
        }

        // Exhibition / publication history
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

        try {
            $response = $this->api()->create('items', $itemData);
            $item = $response->getContent();
            $itemId = $item->id();

            $this->conn->update('collector_submission', ['item_id' => $itemId], ['id' => $id]);

            // Dispatch enrichment + CLIP ingest jobs
            $this->dispatchEnrichAndIngest($itemId);

            $this->messenger()->addSuccess("Catalog item #{$itemId} created. Edit it below, then return to approve the submission.");
            return $this->redirect()->toUrl('/admin/item/' . $itemId . '/edit');
        } catch (\Exception $e) {
            $this->messenger()->addError('Failed to create item: ' . $e->getMessage());
            return $this->redirect()->toRoute('admin/collector-submissions/show', ['id' => $id]);
        }
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
