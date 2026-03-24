<?php declare(strict_types=1);

namespace CollectorSubmission\Controller\Admin;

use Doctrine\DBAL\Connection;
use Laminas\Mvc\Controller\AbstractActionController;
use Laminas\View\Model\ViewModel;

class SubmissionController extends AbstractActionController
{
    private Connection $conn;

    public function __construct(Connection $conn)
    {
        $this->conn = $conn;
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

        $newStatus = $request->getPost('status');
        $adminNotes = $request->getPost('admin_notes');

        if (!in_array($newStatus, ['new', 'reviewed', 'accepted', 'rejected'], true)) {
            return $this->redirect()->toRoute('admin/collector-submissions/show', ['id' => $id]);
        }

        $this->conn->update('collector_submission', [
            'status' => $newStatus,
            'admin_notes' => $adminNotes ?: null,
        ], ['id' => $id]);

        $this->messenger()->addSuccess('Submission status updated.');
        return $this->redirect()->toRoute('admin/collector-submissions/show', ['id' => $id]);
    }
}
