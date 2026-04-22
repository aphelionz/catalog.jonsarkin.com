<?php declare(strict_types=1);

namespace SiteLockdown\Controller;

use Doctrine\DBAL\Connection;
use Laminas\Mvc\Controller\AbstractActionController;

class SubscribeController extends AbstractActionController
{
    private Connection $conn;

    public function __construct(Connection $conn)
    {
        $this->conn = $conn;
    }

    public function indexAction()
    {
        $request = $this->getRequest();
        if (!$request->isPost()) {
            return $this->redirect()->toUrl('/');
        }
        $post = $request->getPost()->toArray();

        // Honeypot — filled means bot; pretend success, drop silently
        if (!empty($post['website'] ?? '')) {
            return $this->redirect()->toUrl('/?signup=ok');
        }

        $email = trim($post['email'] ?? '');
        if (!filter_var($email, FILTER_VALIDATE_EMAIL) || strlen($email) > 254) {
            return $this->redirect()->toUrl('/?signup=invalid');
        }

        try {
            $this->conn->executeStatement(
                'INSERT IGNORE INTO prelaunch_signup (email, ip_address, user_agent) VALUES (?, ?, ?)',
                [
                    strtolower($email),
                    $this->clientIp(),
                    substr((string) ($_SERVER['HTTP_USER_AGENT'] ?? ''), 0, 512),
                ]
            );
            return $this->redirect()->toUrl('/?signup=ok');
        } catch (\Throwable $e) {
            error_log('[prelaunch-signup] ' . $e->getMessage());
            return $this->redirect()->toUrl('/?signup=error');
        }
    }

    private function clientIp(): ?string
    {
        $forwarded = $_SERVER['HTTP_X_FORWARDED_FOR'] ?? '';
        if ($forwarded !== '') {
            $parts = explode(',', $forwarded);
            return substr(trim($parts[0]), 0, 45) ?: null;
        }
        $remote = $_SERVER['REMOTE_ADDR'] ?? '';
        return $remote !== '' ? substr($remote, 0, 45) : null;
    }
}
