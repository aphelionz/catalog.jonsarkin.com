<?php declare(strict_types=1);

namespace SiteLockdown;

use Laminas\EventManager\SharedEventManagerInterface;
use Laminas\Mvc\Controller\AbstractController;
use Laminas\Mvc\MvcEvent;
use Laminas\ServiceManager\ServiceLocatorInterface;
use Laminas\View\Renderer\PhpRenderer;
use Omeka\Module\AbstractModule;

class Module extends AbstractModule
{
    // ── Preview item IDs — update these to the real catalog item IDs ──
    const PREVIEW_ITEM_IDS = [2082, 7467, 5440, 8824, 8818];
    const PUBLIC_PAGE_SLUGS = ['about-jon-sarkin', 'methodology', 'about-the-catalog'];

    public function getConfig()
    {
        return include __DIR__ . '/config/module.config.php';
    }

    public function install(ServiceLocatorInterface $services): void
    {
        $conn = $services->get('Omeka\Connection');
        $conn->exec(<<<'SQL'
CREATE TABLE IF NOT EXISTS prelaunch_signup (
    id INT UNSIGNED AUTO_INCREMENT NOT NULL,
    email VARCHAR(254) NOT NULL,
    created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ip_address VARCHAR(45) DEFAULT NULL,
    user_agent VARCHAR(512) DEFAULT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uniq_email (email),
    INDEX idx_created (created)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
SQL);
    }

    public function upgrade($oldVersion, $newVersion, ServiceLocatorInterface $services): void
    {
        $conn = $services->get('Omeka\Connection');
        if (version_compare($oldVersion, '0.2.0', '<')) {
            $conn->exec(<<<'SQL'
CREATE TABLE IF NOT EXISTS prelaunch_signup (
    id INT UNSIGNED AUTO_INCREMENT NOT NULL,
    email VARCHAR(254) NOT NULL,
    created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ip_address VARCHAR(45) DEFAULT NULL,
    user_agent VARCHAR(512) DEFAULT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uniq_email (email),
    INDEX idx_created (created)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
SQL);
        }
    }

    public function onBootstrap(MvcEvent $event): void
    {
        parent::onBootstrap($event);

        // Allow anonymous access to the robots.txt and prelaunch signup controllers
        $acl = $event->getApplication()->getServiceManager()->get('Omeka\Acl');
        $acl->allow(null, [
            Controller\RobotsController::class,
            Controller\SubscribeController::class,
        ]);

        $eventManager = $event->getApplication()->getEventManager();

        // Password gate — runs after routing, before dispatch
        $eventManager->attach(MvcEvent::EVENT_ROUTE, [$this, 'checkPasswordGate'], -1000);

        // X-Robots-Tag header — runs after response is built
        $eventManager->attach(MvcEvent::EVENT_FINISH, [$this, 'addRobotsHeader'], 100);
    }

    public function attachListeners(SharedEventManagerInterface $sharedEventManager): void
    {
        // Meta noindex tag in <head> of every public page
        $sharedEventManager->attach('*', 'view.layout', [$this, 'injectMetaTag']);
    }

    // ── Password gate ───────────────────────────────────────────────

    public function checkPasswordGate(MvcEvent $event): void
    {
        $routeMatch = $event->getRouteMatch();
        if (!$routeMatch) {
            return;
        }

        // Skip admin routes
        if ($routeMatch->getParam('__ADMIN__')) {
            return;
        }

        // Skip robots.txt route (crawlers need to read the disallow)
        if ($routeMatch->getMatchedRouteName() === 'robots-txt') {
            return;
        }

        // Skip API and internal JSON routes (consumed by frontend JS)
        $routeName = $routeMatch->getMatchedRouteName();
        if (strpos($routeName, 'api') === 0 || strpos($routeName, '/api') !== false) {
            return;
        }
        $uri = $event->getRequest()->getUriString();
        if (preg_match('#/api(/|$|\?)#', $uri)) {
            return;
        }
        // Collector submission form — public access
        if ($routeName === 'site/collector-submit') {
            return;
        }
        // Prelaunch email signup — public access
        if ($routeName === 'prelaunch-signup') {
            return;
        }
        // Visual search — public access
        if ($routeName === 'site/visual-search' || $routeName === 'site/visual-search/json') {
            return;
        }

        // SimilarPieces module JSON endpoints (similar, iconography, lexical-profile)
        $jsonRoutes = ['similar-pieces', 'iconography', 'iconography-batch', 'lexical-profile', 'similar-search'];
        if (in_array($routeName, $jsonRoutes, true) || strpos($routeName, 'similar-pieces/') === 0) {
            return;
        }

        // Allow preview item pages through without authentication
        if ($routeName === 'site/resource-id') {
            $controller = $routeMatch->getParam('controller');
            $action = $routeMatch->getParam('action');
            $id = $routeMatch->getParam('id');
            $isItemController = ($controller === 'item'
                || $controller === 'Omeka\Controller\Site\Item'
                || $controller === 'Omeka\\Controller\\Site\\Item');
            if ($isItemController && $action === 'show' && $id
                && in_array((int) $id, self::PREVIEW_ITEM_IDS, true)) {
                return; // Preview item — let through
            }
        }

        // Allow whitelisted site pages through without authentication
        if ($routeName === 'site/page') {
            $pageSlug = $routeMatch->getParam('page-slug');
            if ($pageSlug && in_array($pageSlug, self::PUBLIC_PAGE_SLUGS, true)) {
                return;
            }
        }

        $services = $event->getApplication()->getServiceManager();
        $settings = $services->get('Omeka\Settings');

        $passwordHash = $settings->get('site_lockdown_password_hash', '');
        if (!$passwordHash) {
            return; // No password configured — module active but unconfigured
        }

        $cookieSecret = $settings->get('site_lockdown_cookie_secret', '');
        if (!$cookieSecret) {
            return;
        }

        $request = $event->getRequest();
        if (!$request instanceof \Laminas\Http\Request) {
            return;
        }

        // Check existing cookie
        $expectedToken = hash_hmac('sha256', $passwordHash, $cookieSecret);
        $cookie = $request->getCookie();
        if ($cookie && isset($cookie->site_lockdown_auth)) {
            if (hash_equals($expectedToken, $cookie->site_lockdown_auth)) {
                return; // Valid cookie — let them through
            }
        }

        $response = $event->getResponse();

        // Handle password submission
        if ($request->isPost()) {
            $password = $request->getPost('lockdown_password', '');
            if ($password && password_verify($password, $passwordHash)) {
                // Set auth cookie
                $duration = (int) $settings->get('site_lockdown_cookie_duration', 0);
                $expires = $duration > 0 ? gmdate('D, d M Y H:i:s T', time() + $duration) : '';
                $secure = ($request->getUri()->getScheme() === 'https') ? '; Secure' : '';
                $cookieHeader = 'site_lockdown_auth=' . $expectedToken
                    . '; Path=/'
                    . '; HttpOnly'
                    . '; SameSite=Lax'
                    . $secure;
                if ($expires) {
                    $cookieHeader .= '; Expires=' . $expires;
                }

                $response->getHeaders()->addHeaderLine('Set-Cookie', $cookieHeader);
                $response->setStatusCode(302);
                $response->getHeaders()->addHeaderLine('Location', $request->getUriString());
                $this->shortCircuit($event, $response);
                return;
            }

            // Wrong password — fall through to show form with error
            $this->renderPrompt($event, $response, true);
            return;
        }

        // No cookie, not a POST — show password form
        $this->renderPrompt($event, $response, false);
    }

    private function renderPrompt(MvcEvent $event, $response, bool $showError): void
    {
        $services = $event->getApplication()->getServiceManager();
        $renderer = $services->get('ViewRenderer');

        // Fetch preview items for the landing page grid
        $previewItems = [];
        try {
            $api = $services->get('Omeka\ApiManager');
            $result = $api->search('items', [
                'id' => self::PREVIEW_ITEM_IDS,
                'sort_by' => 'id',
            ]);
            $previewItems = $result->getContent();
        } catch (\Exception $e) {
            // Degrade gracefully — show page without previews
        }

        $viewModel = new \Laminas\View\Model\ViewModel([
            'error' => $showError,
            'previewItems' => $previewItems,
        ]);
        $viewModel->setTemplate('site-lockdown/lockdown-prompt');
        $viewModel->setTerminal(true);

        $html = $renderer->render($viewModel);

        $response->setStatusCode(200);
        $response->setContent($html);
        $response->getHeaders()->addHeaderLine('Content-Type', 'text/html; charset=UTF-8');
        $response->getHeaders()->addHeaderLine('Cache-Control', 'no-store');

        $this->shortCircuit($event, $response);
    }

    private function shortCircuit(MvcEvent $event, $response): void
    {
        // Ensure X-Robots-Tag is on every short-circuited response
        $response->getHeaders()->addHeaderLine('X-Robots-Tag', 'noindex, nofollow');

        $event->setResponse($response);
        $event->stopPropagation(true);
        $event->setResult($response);

        $response->sendHeaders();
        echo $response->getContent();
        exit;
    }

    // ── X-Robots-Tag header ─────────────────────────────────────────

    public function addRobotsHeader(MvcEvent $event): void
    {
        $routeMatch = $event->getRouteMatch();
        if ($routeMatch && $routeMatch->getParam('__ADMIN__')) {
            return;
        }

        $response = $event->getResponse();
        if ($response instanceof \Laminas\Http\Response) {
            $response->getHeaders()->addHeaderLine('X-Robots-Tag', 'noindex, nofollow');
        }
    }

    // ── Meta noindex tag ────────────────────────────────────────────

    public function injectMetaTag($event): void
    {
        $view = $event->getTarget();
        $services = $this->getServiceLocator();

        if (!$services->get('Omeka\Status')->isSiteRequest()) {
            return;
        }

        $view->headMeta()->appendName('robots', 'noindex, nofollow');
    }

    // ── Config form ─────────────────────────────────────────────────

    public function getConfigForm(PhpRenderer $renderer): string
    {
        $services = $this->getServiceLocator();
        $settings = $services->get('Omeka\Settings');

        $form = $services->get('FormElementManager')->get(Form\ConfigForm::class);
        $form->setData([
            'password' => '', // Never pre-populate password
            'cookie_duration' => $settings->get('site_lockdown_cookie_duration', '0'),
        ]);

        return $renderer->formCollection($form, false);
    }

    public function handleConfigForm(AbstractController $controller)
    {
        $services = $this->getServiceLocator();
        $settings = $services->get('Omeka\Settings');

        $form = $services->get('FormElementManager')->get(Form\ConfigForm::class);
        $form->setData($controller->params()->fromPost());

        if (!$form->isValid()) {
            $controller->messenger()->addErrors($form->getMessages());
            return false;
        }

        $data = $form->getData();

        // Ensure a cookie secret exists
        $secret = $settings->get('site_lockdown_cookie_secret', '');
        if (!$secret) {
            $secret = bin2hex(random_bytes(32));
            $settings->set('site_lockdown_cookie_secret', $secret);
        }

        // Hash password if provided, otherwise keep existing
        $password = trim($data['password'] ?? '');
        if ($password !== '') {
            $settings->set('site_lockdown_password_hash', password_hash($password, PASSWORD_BCRYPT));
        }

        $settings->set('site_lockdown_cookie_duration', $data['cookie_duration']);

        return true;
    }
}
