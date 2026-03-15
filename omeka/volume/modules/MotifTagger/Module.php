<?php declare(strict_types=1);

namespace MotifTagger;

use Laminas\Loader\StandardAutoloader;
use Laminas\Mvc\Controller\AbstractController;
use Laminas\Mvc\MvcEvent;
use Laminas\Permissions\Acl\Resource\GenericResource as Resource;
use Laminas\View\Renderer\PhpRenderer;
use Omeka\Module\AbstractModule;

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
        $resourceId = Controller\MotifTaggerController::class;

        if (method_exists($acl, 'hasResource') && !$acl->hasResource($resourceId)) {
            $acl->addResource(new Resource($resourceId));
        }
        $acl->allow(['editor', 'global_admin', 'site_admin'], $resourceId);
    }

    public function getConfigForm(PhpRenderer $renderer): string
    {
        $services = $this->resolveServices();
        if (!$services || !$services->has('FormElementManager')) {
            return '';
        }

        $form = $services->get('FormElementManager')->get(Form\ConfigForm::class);
        $config = $services->get('Config');
        $defaults = $config['motif_tagger'] ?? [];
        $settings = $services->get('Omeka\Settings');

        $form->setData([
            'clip_api_url' => $settings->get('motiftagger_clip_api_url', $defaults['clip_api_url'] ?? 'http://clip-api:8000'),
            'default_limit' => $settings->get('motiftagger_default_limit', $defaults['default_limit'] ?? 100),
            'default_threshold' => $settings->get('motiftagger_default_threshold', $defaults['default_threshold'] ?? 0.5),
            'motif_property_id' => $settings->get('motiftagger_motif_property_id', $defaults['motif_property_id'] ?? 3),
            'motif_vocab_label' => $settings->get('motiftagger_motif_vocab_label', $defaults['motif_vocab_label'] ?? 'Motifs'),
        ]);

        return $renderer->formCollection($form, false);
    }

    public function handleConfigForm(AbstractController $controller)
    {
        $services = $this->resolveServices();
        if (!$services || !$services->has('FormElementManager')) {
            return false;
        }

        $form = $services->get('FormElementManager')->get(Form\ConfigForm::class);
        $form->setData($controller->params()->fromPost());
        if (!$form->isValid()) {
            $controller->messenger()->addErrors($form->getMessages());
            return false;
        }

        $data = $form->getData();
        $settings = $services->get('Omeka\Settings');
        $settings->set('motiftagger_clip_api_url', rtrim((string) ($data['clip_api_url'] ?? 'http://clip-api:8000'), '/'));
        $settings->set('motiftagger_default_limit', (int) ($data['default_limit'] ?? 100));
        $settings->set('motiftagger_default_threshold', (float) ($data['default_threshold'] ?? 0.5));
        $settings->set('motiftagger_motif_property_id', (int) ($data['motif_property_id'] ?? 3));
        $settings->set('motiftagger_motif_vocab_label', (string) ($data['motif_vocab_label'] ?? 'Motifs'));

        return true;
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
}
