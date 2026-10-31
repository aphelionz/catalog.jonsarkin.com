<?php declare(strict_types=1);

namespace CollectorSubmission\Controller;

use CollectorSubmission\Form\SubmitForm;
use Doctrine\DBAL\Connection;
use Laminas\Mvc\Controller\AbstractActionController;
use Laminas\View\Model\ViewModel;
use Omeka\Stdlib\Mailer;

class SubmitController extends AbstractActionController
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
        $request = $this->getRequest();
        $success = $this->params()->fromQuery('success');

        $form = new SubmitForm('collector-submit');
        $form->init();

        $errors = [];

        if ($request->isPost()) {
            $post = $request->getPost()->toArray();

            // Honeypot check — bots fill this in
            if (!empty($post['website'] ?? '')) {
                return $this->redirect()->toRoute('site/collector-submit', [
                    'site-slug' => $this->currentSite()->slug(),
                ], ['query' => ['success' => '1']]);
            }

            $form->setData($post);

            // Validate files separately
            $fileData = $request->getFiles()->toArray();
            $photos = $fileData['photos'] ?? [];

            // Normalize: single file upload comes as associative, multiple as indexed
            if (isset($photos['name']) && !is_array($photos['name'])) {
                $photos = [$photos];
            } elseif (isset($photos['name']) && is_array($photos['name'])) {
                // PHP multi-file format: photos[name][0], photos[name][1], etc.
                $normalized = [];
                foreach ($photos['name'] as $i => $name) {
                    $normalized[] = [
                        'name' => $name,
                        'type' => $photos['type'][$i],
                        'tmp_name' => $photos['tmp_name'][$i],
                        'error' => $photos['error'][$i],
                        'size' => $photos['size'][$i],
                    ];
                }
                $photos = $normalized;
            }

            // Filter out empty file slots
            $photos = array_filter($photos, fn($f) => !empty($f['tmp_name']) && $f['error'] === UPLOAD_ERR_OK);

            if (empty($photos)) {
                $errors['photos'] = 'At least one photo is required.';
            }

            $valid = $form->isValid() && empty($errors);
            if ($valid && !$this->takeRateLimitSlot()) {
                $this->getResponse()->setStatusCode(429);
                $errors['form'] = 'Too many submissions from your network. Please try again in an hour.';
            } elseif ($valid) {
                $data = $form->getData();
                $savedFiles = $this->processUploads($photos);

                if (empty($savedFiles)) {
                    $errors['photos'] = 'File upload failed. Please try again.';
                } else {
                    // One thank-you per address per day, so the form can't be
                    // used to flood someone else's inbox.
                    $sendThankYou = !$this->hasRecentSubmission($data['email']);
                    $this->saveSubmission($data, $savedFiles);
                    $this->sendNotification($data, count($savedFiles));
                    if ($sendThankYou) {
                        $this->sendThankYouEmail($data);
                    }

                    return $this->redirect()->toRoute('site/collector-submit', [
                        'site-slug' => $this->currentSite()->slug(),
                    ], ['query' => ['success' => '1']]);
                }
            }
        }

        $view = new ViewModel([
            'form' => $form,
            'errors' => $errors,
            'success' => $success,
            'site' => $this->currentSite(),
        ]);
        $view->setTemplate('collector-submission/submit/index');
        return $view;
    }

    /**
     * Count a valid submission against the client IP's hourly budget.
     * Returns false when the budget is spent. Counters live in the
     * container temp dir and are lost when the container is recreated,
     * which is fine for throttling.
     */
    private function takeRateLimitSlot(): bool
    {
        $limit = (int) ($this->config['max_submissions_per_ip_per_hour'] ?? 20);
        $dir = sys_get_temp_dir() . '/collector-submit-rate';
        if (!is_dir($dir)) {
            @mkdir($dir, 0700, true);
        }

        $handle = @fopen($dir . '/' . hash('sha256', $this->clientIp()), 'c+');
        if ($handle === false) {
            return true;
        }

        try {
            flock($handle, LOCK_EX);
            $cutoff = time() - 3600;
            $hits = json_decode(stream_get_contents($handle) ?: '[]', true);
            $hits = array_values(array_filter(
                is_array($hits) ? $hits : [],
                fn($t) => is_int($t) && $t > $cutoff
            ));
            if (count($hits) >= $limit) {
                return false;
            }
            $hits[] = time();
            ftruncate($handle, 0);
            rewind($handle);
            fwrite($handle, json_encode($hits));
            return true;
        } finally {
            flock($handle, LOCK_UN);
            fclose($handle);
        }
    }

    /**
     * Traefik replaces any client-sent X-Forwarded-For with the real peer
     * address, so its last entry is the client. Only trust the header when
     * the request came from a private (proxy) address.
     */
    private function clientIp(): string
    {
        $remote = $_SERVER['REMOTE_ADDR'] ?? '';
        $forwarded = $_SERVER['HTTP_X_FORWARDED_FOR'] ?? '';
        $isPublic = filter_var($remote, FILTER_VALIDATE_IP, FILTER_FLAG_NO_PRIV_RANGE | FILTER_FLAG_NO_RES_RANGE);
        if ($forwarded !== '' && $isPublic === false) {
            $parts = array_map('trim', explode(',', $forwarded));
            $last = end($parts);
            if (filter_var($last, FILTER_VALIDATE_IP)) {
                return $last;
            }
        }
        return $remote;
    }

    private function hasRecentSubmission(string $email): bool
    {
        $since = (new \DateTime('-1 day'))->format('Y-m-d H:i:s');
        return (bool) $this->conn->fetchOne(
            'SELECT 1 FROM collector_submission WHERE email = ? AND created > ? LIMIT 1',
            [$email, $since]
        );
    }

    private function processUploads(array $photos): array
    {
        $basePath = OMEKA_PATH . '/files';
        $uploadDir = $basePath . '/submissions';
        if (!is_dir($uploadDir)) {
            mkdir($uploadDir, 0755, true);
        }

        $maxSize = $this->config['max_file_size'] ?? 15 * 1024 * 1024;
        $allowedMimes = ['image/jpeg', 'image/png', 'image/heic', 'image/heif'];
        $saved = [];

        foreach ($photos as $file) {
            if ($file['size'] > $maxSize) {
                continue;
            }

            $mime = (new \finfo(FILEINFO_MIME_TYPE))->file($file['tmp_name']);
            if (!in_array($mime, $allowedMimes, true)) {
                continue;
            }

            $isHeic = in_array($mime, ['image/heic', 'image/heif'], true);
            $ext = $isHeic ? 'jpg' : $this->extensionFromMime($mime, $file['name']);
            $safeName = preg_replace('/[^a-zA-Z0-9._-]/', '_', pathinfo($file['name'], PATHINFO_FILENAME));
            $filename = time() . '_' . bin2hex(random_bytes(4)) . '_' . $safeName . '.' . $ext;
            $dest = $uploadDir . '/' . $filename;

            if ($isHeic) {
                // ImageMagick in the Omeka container reads HEIC but cannot write it.
                // Transcode to JPEG so every downstream path (admin preview, rotation,
                // Omeka ingest) operates on JPEG — one place to fix, many wins.
                $cmd = sprintf(
                    'convert %s -auto-orient -quality 92 %s 2>&1',
                    escapeshellarg($file['tmp_name']),
                    escapeshellarg($dest)
                );
                exec($cmd, $out, $rc);
                if ($rc === 0 && file_exists($dest)) {
                    $saved[] = 'submissions/' . $filename;
                } else {
                    error_log('CollectorSubmission HEIC transcode failed: ' . implode("\n", $out));
                }
            } elseif (move_uploaded_file($file['tmp_name'], $dest)) {
                $saved[] = 'submissions/' . $filename;
            }
        }

        return $saved;
    }

    private function extensionFromMime(string $mime, string $originalName): string
    {
        $map = [
            'image/jpeg' => 'jpg',
            'image/png' => 'png',
            'image/heic' => 'heic',
            'image/heif' => 'heic',
        ];
        return $map[$mime] ?? pathinfo($originalName, PATHINFO_EXTENSION) ?: 'bin';
    }

    private function saveSubmission(array $data, array $files): void
    {
        $this->conn->insert('collector_submission', [
            'collector_name' => $data['collector_name'],
            'email' => $data['email'],
            'num_pieces' => (int) $data['num_pieces'],
            'how_acquired' => $data['how_acquired'],
            'date_acquired' => $data['date_acquired'] ?: null,
            'description' => $data['description'] ?: null,
            'files' => json_encode($files),
            'exhibition_history' => $data['exhibition_history'] ?: null,
            'dimensions_height' => $data['dimensions_height'] ?: null,
            'dimensions_width' => $data['dimensions_width'] ?: null,
            'dimensions_unit' => $data['dimensions_unit'] ?: 'in',
            'may_contact' => (int) ($data['may_contact'] ?? 1),
            'credit_preference' => $data['credit_preference'] ?: 'full_name',
            'status' => 'new',
            'created' => (new \DateTime())->format('Y-m-d H:i:s'),
        ]);
    }

    private function sendThankYouEmail(array $data): void
    {
        try {
            $email = $data['email'] ?? null;
            if (!$email) {
                return;
            }

            $name = explode(' ', $data['collector_name'])[0];
            $replacements = ['{name}' => $name];

            $subject = strtr($this->config['email']['collector_submission_thankyou_subject'] ?? '', $replacements);
            $body = strtr($this->config['email']['collector_submission_thankyou_body'] ?? '', $replacements);

            $message = $this->mailer->createMessage();
            $message->setSubject($subject);
            $message->addTo($email);
            $message->setBody($body);
            $this->mailer->send($message);
        } catch (\Throwable $e) {
            error_log('CollectorSubmission thank-you email failed: ' . $e->getMessage());
        }
    }

    private function sendNotification(array $data, int $fileCount): void
    {
        try {
            $adminEmail = $this->config['admin_email'] ?? null;
            if (!$adminEmail) {
                return;
            }

            $body = "New collector submission received.\n\n"
                . "Name: {$data['collector_name']}\n"
                . "Email: {$data['email']}\n"
                . "Pieces: {$data['num_pieces']}\n"
                . "How acquired: {$data['how_acquired']}\n"
                . "Date acquired: " . ($data['date_acquired'] ?: 'Not specified') . "\n"
                . "Photos: {$fileCount} file(s)\n"
                . "May contact: " . ($data['may_contact'] ? 'Yes' : 'No') . "\n"
                . "Credit: {$data['credit_preference']}\n\n"
                . "Description:\n" . ($data['description'] ?: '(none)') . "\n\n"
                . "Exhibition history:\n" . ($data['exhibition_history'] ?: '(none)') . "\n\n"
                . "Review at: /admin/collector-submissions\n";

            $message = $this->mailer->createMessage();
            $message->setSubject('Catalog Submission: ' . $data['collector_name']);
            $message->addTo($adminEmail);
            $message->setBody($body);
            $this->mailer->send($message);
        } catch (\Throwable $e) {
            // Email failure should not block the submission
            error_log('CollectorSubmission email failed: ' . $e->getMessage());
        }
    }
}
