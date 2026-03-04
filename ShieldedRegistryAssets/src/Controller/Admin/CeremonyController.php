<?php declare(strict_types=1);

namespace ShieldedRegistryAssets\Controller\Admin;

use Laminas\Mvc\Controller\AbstractActionController;
use Laminas\View\Model\JsonModel;
use Laminas\View\Model\ViewModel;
use ShieldedRegistryAssets\Form\CeremonyConfigForm;
use ShieldedRegistryAssets\Module;

class CeremonyController extends AbstractActionController
{
    private object $settings;
    private object $formElementManager;

    public function __construct(object $settings, object $formElementManager)
    {
        $this->settings = $settings;
        $this->formElementManager = $formElementManager;
    }

    public function indexAction()
    {
        $form = $this->formElementManager->get(CeremonyConfigForm::class);

        $config = Module::getCeremonyConfig($this->settings);
        $result = Module::getCeremonyResult($this->settings);
        $form->setData(array_merge($config, $result));

        $request = $this->getRequest();
        if ($request->isPost()) {
            $post = $request->getPost()->toArray();

            // Reset ceremony
            if (($post['action'] ?? '') === 'reset_ceremony') {
                $this->settings->set('sra_ceremony_config', '{}');
                $this->settings->set('sra_ceremony_result', '{}');
                $this->messenger()->addSuccess('Ceremony data reset. You can now start over.');
                return $this->redirect()->toRoute('admin/shielded-registry');
            }

            $this->saveCeremonyData($post);
            $this->messenger()->addSuccess('Saved.');
            return $this->redirect()->toRoute('admin/shielded-registry');
        }

        $state = Module::getCeremonyState($this->settings);

        // Detect dev mode (localhost)
        $host = $_SERVER['HTTP_HOST'] ?? '';
        $isDevMode = (
            str_contains($host, 'localhost')
            || str_contains($host, '127.0.0.1')
            || str_contains($host, '0.0.0.0')
        );

        $view = new ViewModel([
            'form' => $form,
            'state' => $state,
            'config' => $config,
            'result' => $result,
            'isDevMode' => $isDevMode,
        ]);
        $view->setTemplate('shielded-registry-assets/admin/ceremony/index');
        return $view;
    }

    /**
     * AJAX endpoint: check DNS TXT record for _sra-authority.{domain}
     */
    public function checkDnsAction()
    {
        $request = $this->getRequest();
        if (!$request->isPost()) {
            return new JsonModel(['error' => 'POST required']);
        }

        $post = $request->getPost()->toArray();
        $domain = trim($post['domain'] ?? '');
        $expectedPkR = trim($post['pk_r'] ?? '');

        if ($domain === '' || $expectedPkR === '') {
            return new JsonModel([
                'status' => 'error',
                'message' => 'Domain and PK_R are required.',
            ]);
        }

        $lookupHost = '_sra-authority.' . $domain;
        $records = @dns_get_record($lookupHost, DNS_TXT);

        if ($records === false || empty($records)) {
            return new JsonModel([
                'status' => 'not_found',
                'message' => "No TXT records found at $lookupHost",
                'lookup' => $lookupHost,
            ]);
        }

        // Look for sra-authority=<pk_r> in TXT records
        $expectedValue = 'sra-authority=' . strtolower($expectedPkR);
        foreach ($records as $record) {
            $txt = strtolower(trim($record['txt'] ?? ''));
            if ($txt === $expectedValue) {
                // Save verified status
                $existingResult = Module::getCeremonyResult($this->settings);
                $existingResult['dns_verified'] = true;
                $existingResult['dns_verified_at'] = date('c');
                $this->settings->set('sra_ceremony_result', json_encode($existingResult));

                return new JsonModel([
                    'status' => 'verified',
                    'message' => "DNS TXT record verified at $lookupHost",
                    'lookup' => $lookupHost,
                ]);
            }
        }

        // Found TXT records but none match
        $foundValues = array_map(fn($r) => $r['txt'] ?? '', $records);
        return new JsonModel([
            'status' => 'mismatch',
            'message' => "TXT records found at $lookupHost but none match expected value.",
            'lookup' => $lookupHost,
            'expected' => $expectedValue,
            'found' => $foundValues,
        ]);
    }

    /**
     * Public endpoint: /.well-known/sra-authority.json
     */
    public function wellKnownAction()
    {
        $config = Module::getCeremonyConfig($this->settings);
        $result = Module::getCeremonyResult($this->settings);

        // Only serve if we have at least a PK_R
        if (empty($result['pk_r'])) {
            $this->getResponse()->setStatusCode(404);
            return new JsonModel(['error' => 'Registry authority not yet configured.']);
        }

        $scope = $config['ra_scope'] ?? '';
        if (strlen($scope) > 128) {
            $scope = substr($scope, 0, 128);
        }

        $payload = [
            'sra_version' => 1,
            'pk_r' => $result['pk_r'],
            'scope' => $scope,
            'genesis_hash' => $result['genesis_txid'] ?? '',
            'contact' => $config['contact'] ?? '',
        ];

        $response = $this->getResponse();
        $response->getHeaders()->addHeaderLine('Content-Type', 'application/json');
        $response->getHeaders()->addHeaderLine('Access-Control-Allow-Origin', '*');
        $response->setContent(json_encode($payload, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES));
        return $response;
    }

    private function saveCeremonyData(array $data): void
    {
        $existingConfig = Module::getCeremonyConfig($this->settings);
        $existingResult = Module::getCeremonyResult($this->settings);

        // Config fields
        $configFields = [
            'ra_name', 'party_mode', 'ra_scope',
            'threshold_m', 'threshold_n', 'participant_labels',
            'domain', 'contact',
        ];
        $config = $existingConfig;
        foreach ($configFields as $field) {
            if (array_key_exists($field, $data)) {
                $val = trim((string) ($data[$field] ?? ''));
                $config[$field] = in_array($field, ['threshold_m', 'threshold_n'])
                    ? (int) $val
                    : $val;
            }
        }

        // Result fields
        $result = $existingResult;
        if (array_key_exists('pk_r', $data)) {
            $pkR = trim($data['pk_r'] ?? '');
            if ($pkR !== '') {
                $result['pk_r'] = strtolower($pkR);
            }
        }
        if (array_key_exists('genesis_txid', $data)) {
            $genesisTxid = trim($data['genesis_txid'] ?? '');
            if ($genesisTxid !== '') {
                $result['genesis_txid'] = strtolower($genesisTxid);
            }
        }

        $this->settings->set('sra_ceremony_config', json_encode($config));
        $this->settings->set('sra_ceremony_result', json_encode($result));
    }
}
