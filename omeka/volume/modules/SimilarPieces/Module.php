<?php declare(strict_types=1);

namespace SimilarPieces;

use Laminas\EventManager\Event;
use Laminas\EventManager\SharedEventManagerInterface;
use Laminas\Loader\StandardAutoloader;
use Laminas\Mvc\Controller\AbstractController;
use Laminas\Mvc\MvcEvent;
use Laminas\Navigation\Page\Mvc as NavigationPage;
use Laminas\Permissions\Acl\Resource\GenericResource as Resource;
use Laminas\View\Renderer\PhpRenderer;
use Omeka\Module\AbstractModule;
use Psr\Log\LoggerInterface;
use Psr\Log\NullLogger;
use RuntimeException;
use SimilarPieces\Config\ModuleConfig;
use SimilarPieces\Controller\SearchController;
use SimilarPieces\Controller\SimilarController;
use SimilarPieces\Form\ConfigForm;
use Throwable;

class Module extends AbstractModule
{
    private $services;
    private ?object $logger = null;
    private ?bool $serviceHealthy = null;

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

        $services = $event->getApplication()->getServiceManager();
        $this->services = $services;
        $this->serviceHealthy = null;
        $acl = $services->get('Omeka\Acl');
        $resourceId = SimilarController::class;
        $searchResourceId = SearchController::class;

        if (method_exists($acl, 'hasResource') && !$acl->hasResource($resourceId)) {
            $acl->addResource(new Resource($resourceId));
        }
        if (method_exists($acl, 'hasResource') && !$acl->hasResource($searchResourceId)) {
            $acl->addResource(new Resource($searchResourceId));
        }

        $acl->allow(null, $resourceId);
        $acl->allow(null, $searchResourceId);

        $event->getApplication()->getEventManager()->attach(
            MvcEvent::EVENT_RENDER,
            [$this, 'injectSearchNavigation']
        );
    }

    public function attachListeners(SharedEventManagerInterface $sharedEventManager): void
    {
        $sharedEventManager->attach(
            'Omeka\\Controller\\Site\\Item',
            'view.show.after',
            [$this, 'appendSimilarLink']
        );
    }

    public function getConfigForm(PhpRenderer $renderer): string
    {
        $services = $this->resolveServices();
        if (!$services || !$services->has('FormElementManager')) {
            return '';
        }

        $form = $services->get('FormElementManager')->get(ConfigForm::class);
        $config = $this->getModuleConfig();
        $form->setData([
            'base_url' => $config['base_url'] ?? '',
            'enable_search_ui' => !empty($config['enable_search_ui']),
            'debug' => !empty($config['debug']),
        ]);

        return $renderer->formCollection($form, false);
    }

    public function handleConfigForm(AbstractController $controller)
    {
        $services = $this->resolveServices();
        if (!$services || !$services->has('FormElementManager')) {
            return false;
        }

        $form = $services->get('FormElementManager')->get(ConfigForm::class);
        $form->setData($controller->params()->fromPost());
        if (!$form->isValid()) {
            $controller->messenger()->addErrors($form->getMessages());
            return false;
        }

        $data = $form->getData();
        $baseUrl = ModuleConfig::normalizeBaseUrl((string) ($data['base_url'] ?? ''), 'https://similar.jonsarkin.com');
        $debug = !empty($data['debug']);
        $enableSearchUi = !empty($data['enable_search_ui']);

        if ($services->has('Omeka\\Settings')) {
            $settings = $services->get('Omeka\\Settings');
            $settings->set('similar_pieces_base_url', $baseUrl);
            $settings->set('similar_pieces_debug', $debug);
            $settings->set('similar_pieces_enable_search_ui', $enableSearchUi);
        }

        return true;
    }

    public function appendSimilarLink(Event $event): void
    {
        // Similar pieces are now rendered inline on the item page via JS.
        // This hook is retained as a no-op so existing event wiring doesn't break.
    }

    private function getModuleConfig(): array
    {
        $services = $this->resolveServices();
        if ($services) {
            return ModuleConfig::resolve($services);
        }

        $config = include __DIR__ . '/config/module.config.php';
        $moduleConfig = $config['similar_pieces'] ?? [];
        $moduleConfig['base_url'] = ModuleConfig::normalizeBaseUrl(
            (string) ($moduleConfig['base_url'] ?? ''),
            'https://similar.jonsarkin.com'
        );
        $moduleConfig['debug'] = !empty($moduleConfig['debug']);
        $moduleConfig['enable_search_ui'] = !empty($moduleConfig['enable_search_ui']);

        return $moduleConfig;
    }

    private function resolveServices(): ?object
    {
        if ($this->services) {
            return $this->services;
        }

        if (method_exists($this, 'getServiceLocator')) {
            return $this->getServiceLocator();
        }

        return null;
    }

    private function isServiceHealthy(): bool
    {
        if ($this->serviceHealthy !== null) {
            return $this->serviceHealthy;
        }

        try {
            $status = $this->fetchHealthStatus();
            $this->serviceHealthy = ($status === 'ok');
        } catch (Throwable $e) {
            $this->serviceHealthy = false;
            $this->logError(sprintf('SimilarPieces health check error: %s', $e->getMessage()), $e);
        }

        return $this->serviceHealthy;
    }

    public function injectSearchNavigation(MvcEvent $event): void
    {
        $moduleConfig = $this->getModuleConfig();
        if (empty($moduleConfig['enable_search_ui'])) {
            return;
        }

        $services = $event->getApplication()->getServiceManager();
        if (!$services || !$services->has('ViewRenderer')) {
            return;
        }

        $view = $services->get('ViewRenderer');
        if (!$view instanceof PhpRenderer) {
            return;
        }

        $routeMatch = $event->getRouteMatch();
        if ($routeMatch) {
            $routeName = (string) $routeMatch->getMatchedRouteName();
            $isPublic = (strpos($routeName, 'site') !== false)
                || in_array($routeName, ['similar-search', 'similar-pieces'], true);
            if (!$isPublic) {
                return;
            }
        }

        try {
            $navigationHelper = $view->navigation();
        } catch (Throwable $e) {
            return;
        }

        $container = $navigationHelper->getContainer();
        if (!$container) {
            return;
        }

        foreach ($container as $page) {
            if (method_exists($page, 'getRoute') && $page->getRoute() === 'similar-search') {
                return;
            }
            if (method_exists($page, 'getLabel') && $page->getLabel() === 'Search') {
                return;
            }
        }

        $siteSlug = null;
        if (method_exists($view, 'currentSite')) {
            $site = $view->currentSite();
            if ($site) {
                $siteSlug = (string) $site->slug();
            }
        }

        $routeOptions = [];
        if ($siteSlug) {
            $routeOptions['query'] = ['site' => $siteSlug];
        }

        try {
            $url = $view->url('similar-search', [], $routeOptions);
        } catch (Throwable $e) {
            return;
        }

        $container->addPage(new NavigationPage([
            'label' => 'Search',
            'uri' => $url,
        ]));
    }

    private function fetchHealthStatus(): string
    {
        $services = $this->resolveServices();
        if (!$services || !$services->has('Omeka\\HttpClient')) {
            throw new RuntimeException('Similarity health check unavailable');
        }

        $moduleConfig = $this->getModuleConfig();
        $baseUrl = (string) ($moduleConfig['base_url'] ?? 'https://similar.jonsarkin.com');
        $baseUrl = rtrim($baseUrl, '/');
        $url = $baseUrl . '/healthz';

        $timeout = (int) ($moduleConfig['health_timeout'] ?? $moduleConfig['timeout'] ?? 3);
        $timeout = max(1, min(10, $timeout));

        $client = clone $services->get('Omeka\\HttpClient');
        $client->resetParameters(true);
        $client->setUri($url);
        $client->setMethod('GET');
        $client->setHeaders(['Accept' => 'application/json']);
        $client->setOptions([
            'timeout' => $timeout,
        ]);

        $response = $client->send();
        if (!$response->isSuccess()) {
            throw new RuntimeException(sprintf('Similarity health check HTTP %d', $response->getStatusCode()));
        }

        $body = $response->getBody();
        $data = json_decode($body, true);
        if (json_last_error() !== JSON_ERROR_NONE) {
            throw new RuntimeException('Similarity health check returned invalid JSON');
        }

        if (!is_array($data)) {
            throw new RuntimeException('Similarity health check returned unexpected payload');
        }

        $status = $data['status'] ?? null;
        if (!is_string($status) || $status === '') {
            throw new RuntimeException('Similarity health check returned missing status');
        }

        return strtolower($status);
    }

    private function getLogger(): object
    {
        if ($this->logger) {
            return $this->logger;
        }

        $services = $this->resolveServices();
        if ($services && $services->has('Omeka\\Logger')) {
            $this->logger = $services->get('Omeka\\Logger');
        } elseif ($services && $services->has(LoggerInterface::class)) {
            $this->logger = $services->get(LoggerInterface::class);
        } else {
            $this->logger = new NullLogger();
        }

        return $this->logger;
    }

    private function logError(string $message, ?Throwable $exception = null): void
    {
        if ($exception && $this->getModuleConfig()['debug']) {
            $message = sprintf("%s\n%s", $message, $exception->getTraceAsString());
        }

        $logger = $this->getLogger();
        if (method_exists($logger, 'err')) {
            $logger->err($message);
            return;
        }
        if (method_exists($logger, 'error')) {
            $logger->error($message);
        }
    }
}
