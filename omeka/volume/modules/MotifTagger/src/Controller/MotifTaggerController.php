<?php declare(strict_types=1);

namespace MotifTagger\Controller;

use Doctrine\ORM\EntityManager;
use Laminas\Http\Client as HttpClient;
use Laminas\Mvc\Controller\AbstractActionController;
use Laminas\View\Model\JsonModel;
use Laminas\View\Model\ViewModel;
use MotifTagger\Job;
use Omeka\Api\Manager as ApiManager;
use Omeka\Job\Dispatcher;
use Omeka\Settings\Settings;

class MotifTaggerController extends AbstractActionController
{
    private HttpClient $httpClient;
    private ApiManager $api;
    private EntityManager $entityManager;
    private Settings $settings;
    private Dispatcher $dispatcher;
    private array $config;

    public function __construct(
        HttpClient $httpClient,
        ApiManager $api,
        EntityManager $entityManager,
        Settings $settings,
        Dispatcher $dispatcher,
        array $config
    ) {
        $this->httpClient = $httpClient;
        $this->api = $api;
        $this->entityManager = $entityManager;
        $this->settings = $settings;
        $this->dispatcher = $dispatcher;
        $this->config = $config;
    }

    public function indexAction(): ViewModel
    {
        $vocabLabel = $this->getSetting('motif_vocab_label', 'Motifs');
        $conn = $this->entityManager->getConnection();
        $termsJson = $conn->fetchOne(
            'SELECT terms FROM custom_vocab WHERE label = ?',
            [$vocabLabel]
        );
        $terms = $termsJson ? (json_decode($termsJson, true) ?: []) : [];
        sort($terms);

        $qdrantStats = $this->fetchQdrantStats();

        $view = new ViewModel([
            'terms' => $terms,
            'defaultLimit' => (int) $this->getSetting('default_limit', 100),
            'defaultThreshold' => (float) $this->getSetting('default_threshold', 0.5),
            'searchUrl' => $this->url()->fromRoute('admin/motif-tagger/search'),
            'tagUrl' => $this->url()->fromRoute('admin/motif-tagger/tag'),
            'addTermUrl' => $this->url()->fromRoute('admin/motif-tagger/add-term'),
            'ingestClipUrl' => $this->url()->fromRoute('admin/motif-tagger/ingest-clip'),
            'ingestDinoUrl' => $this->url()->fromRoute('admin/motif-tagger/ingest-dino'),
            'ingestSegmentsUrl' => $this->url()->fromRoute('admin/motif-tagger/ingest-segments'),
            'qdrantStats' => $qdrantStats,
        ]);
        return $view;
    }

    public function searchAction(): JsonModel
    {
        $request = $this->getRequest();
        if (!$request->isPost()) {
            return new JsonModel(['error' => 'POST required']);
        }

        $files = $request->getFiles()->toArray();
        $file = $files['image'] ?? null;
        if (!$file || empty($file['tmp_name']) || $file['error'] !== UPLOAD_ERR_OK) {
            return new JsonModel(['error' => 'No image uploaded or upload error']);
        }

        $tmpPath = $file['tmp_name'];
        $mimeType = $file['type'] ?? 'image/jpeg';
        $allowedTypes = ['image/jpeg', 'image/png', 'image/webp'];
        if (!in_array($mimeType, $allowedTypes, true)) {
            return new JsonModel(['error' => 'Invalid image type. Allowed: JPEG, PNG, WebP']);
        }

        if ($file['size'] > 10 * 1024 * 1024) {
            return new JsonModel(['error' => 'Image too large (max 10 MB)']);
        }

        $post = $request->getPost();
        $limit = (int) ($post->get('limit') ?: $this->getSetting('default_limit', 100));
        $searchMode = $post->get('search_mode') ?: 'dino';

        $endpoint = match ($searchMode) {
            'dino' => '/v1/omeka/images/motif-search',
            'segment' => '/v1/omeka/images/segment-search',
            default => '/v1/omeka/images/search',
        };
        $clipUrl = rtrim($this->getSetting('clip_api_url', 'http://clip-api:8000'), '/')
            . $endpoint;

        try {
            $client = clone $this->httpClient;
            $client->resetParameters(true);
            $client->setUri($clipUrl);
            $client->setMethod('POST');
            $client->setFileUpload(
                $file['name'] ?? 'upload.jpg',
                'image',
                file_get_contents($tmpPath),
                $mimeType
            );
            $client->setParameterPost([
                'limit' => (string) $limit,
                'catalog_version' => '2',
            ]);
            $client->setEncType('multipart/form-data');
            $client->setOptions(['timeout' => 60]);

            $response = $client->send();
            if (!$response->isSuccess()) {
                return new JsonModel([
                    'error' => sprintf('CLIP service returned HTTP %d', $response->getStatusCode()),
                ]);
            }

            $data = json_decode($response->getBody(), true);
            if (!$data || !isset($data['matches'])) {
                return new JsonModel(['error' => 'Invalid response from CLIP service']);
            }
        } catch (\Exception $e) {
            return new JsonModel([
                'error' => sprintf('Cannot connect to CLIP service at %s: %s', $clipUrl, $e->getMessage()),
            ]);
        }

        $matches = $data['matches'];

        // Normalize field name from clip-api response
        foreach ($matches as &$match) {
            if (isset($match['omeka_item_id']) && !isset($match['omeka_id'])) {
                $match['omeka_id'] = $match['omeka_item_id'];
            }
        }
        unset($match);

        // Enrich with existing motifs and titles
        $omekaIds = array_column($matches, 'omeka_id');
        $existingMotifs = $this->getExistingMotifs($omekaIds);
        $titles = $this->getItemTitles($omekaIds);

        // Build browser-reachable base URL from the current request
        $requestUri = $this->getRequest()->getUri();
        $browserBase = $requestUri->getScheme() . '://' . $requestUri->getHost()
            . ($requestUri->getPort() ? ':' . $requestUri->getPort() : '');

        foreach ($matches as &$match) {
            $id = $match['omeka_id'] ?? null;
            $match['existing_motifs'] = $existingMotifs[$id] ?? [];
            $match['title'] = $titles[$id] ?? '';

            // Rewrite internal container URLs to browser-reachable URLs
            if (isset($match['thumb_url'])) {
                $match['thumb_url'] = preg_replace(
                    '#https?://(?:omeka:\d+|localhost:\d+|catalog\.jonsarkin\.com)#',
                    $browserBase,
                    $match['thumb_url']
                );
            }

            // Rewrite segment_url (relative path from clip-api) to browser-reachable
            if (!empty($match['segment_url'])) {
                $match['segment_url'] = 'http://localhost:8000' . $match['segment_url'];
            }
        }
        unset($match);

        return new JsonModel(['matches' => $matches]);
    }

    public function tagAction(): JsonModel
    {
        $request = $this->getRequest();
        if (!$request->isPost()) {
            return new JsonModel(['error' => 'POST required']);
        }

        $body = json_decode($request->getContent(), true);
        $itemIds = $body['item_ids'] ?? [];
        $motif = trim($body['motif'] ?? '');

        if (empty($itemIds) || !is_array($itemIds)) {
            return new JsonModel(['error' => 'No items selected']);
        }
        if ($motif === '') {
            return new JsonModel(['error' => 'No motif specified']);
        }

        $propertyId = (int) $this->getSetting('motif_property_id', 3);
        $tagged = 0;
        $skipped = 0;
        $errors = [];

        foreach ($itemIds as $itemId) {
            $itemId = (int) $itemId;
            try {
                $item = $this->api->read('items', $itemId)->getContent();
                $itemArray = json_decode(json_encode($item), true);

                // Check existing motifs
                $existingSubjects = $itemArray['dcterms:subject'] ?? [];
                $alreadyHas = false;
                foreach ($existingSubjects as $val) {
                    if (isset($val['@value']) && strcasecmp($val['@value'], $motif) === 0) {
                        $alreadyHas = true;
                        break;
                    }
                }

                if ($alreadyHas) {
                    $skipped++;
                    continue;
                }

                // Build patch payload preserving all existing properties
                $payload = $this->buildPatchPayload($itemArray);

                // Append new motif
                if (!isset($payload['dcterms:subject'])) {
                    $payload['dcterms:subject'] = [];
                }
                $payload['dcterms:subject'][] = [
                    'type' => 'literal',
                    'property_id' => $propertyId,
                    '@value' => $motif,
                ];

                $this->api->update('items', $itemId, $payload, [], ['isPartial' => true]);
                $tagged++;
            } catch (\Exception $e) {
                $errors[] = ['id' => $itemId, 'message' => $e->getMessage()];
            }
        }

        return new JsonModel([
            'tagged' => $tagged,
            'skipped' => $skipped,
            'errors' => $errors,
        ]);
    }

    public function ingestClipAction(): JsonModel
    {
        $request = $this->getRequest();
        if (!$request->isPost()) {
            return new JsonModel(['error' => 'POST required']);
        }

        $this->dispatcher->dispatch(Job\IngestClip::class, []);
        return new JsonModel(['status' => 'dispatched', 'type' => 'clip']);
    }

    public function ingestDinoAction(): JsonModel
    {
        $request = $this->getRequest();
        if (!$request->isPost()) {
            return new JsonModel(['error' => 'POST required']);
        }

        $this->dispatcher->dispatch(Job\IngestDino::class, []);
        return new JsonModel(['status' => 'dispatched', 'type' => 'dino']);
    }

    public function ingestSegmentsAction(): JsonModel
    {
        $request = $this->getRequest();
        if (!$request->isPost()) {
            return new JsonModel(['error' => 'POST required']);
        }

        $this->dispatcher->dispatch(Job\IngestSegments::class, []);
        return new JsonModel(['status' => 'dispatched', 'type' => 'segments']);
    }

    public function addTermAction(): JsonModel
    {
        $request = $this->getRequest();
        if (!$request->isPost()) {
            return new JsonModel(['error' => 'POST required']);
        }

        $body = json_decode($request->getContent(), true);
        $newTerm = trim($body['term'] ?? '');
        if ($newTerm === '') {
            return new JsonModel(['error' => 'Term cannot be empty']);
        }

        $vocabLabel = $this->getSetting('motif_vocab_label', 'Motifs');
        $conn = $this->entityManager->getConnection();
        $row = $conn->fetchAssociative(
            'SELECT id, terms FROM custom_vocab WHERE label = ?',
            [$vocabLabel]
        );

        if (!$row) {
            return new JsonModel(['error' => sprintf('Custom vocab "%s" not found', $vocabLabel)]);
        }

        $terms = json_decode($row['terms'], true) ?: [];

        // Check for duplicates (case-insensitive)
        foreach ($terms as $t) {
            if (strcasecmp($t, $newTerm) === 0) {
                return new JsonModel(['error' => sprintf('Term "%s" already exists', $newTerm)]);
            }
        }

        $terms[] = $newTerm;
        sort($terms);

        $conn->executeStatement(
            'UPDATE custom_vocab SET terms = ? WHERE id = ?',
            [json_encode($terms, JSON_UNESCAPED_UNICODE), $row['id']]
        );

        return new JsonModel(['terms' => $terms, 'added' => $newTerm]);
    }

    private function fetchQdrantStats(): array
    {
        $qdrantUrl = rtrim($this->config['qdrant_url'] ?? 'http://qdrant:6333', '/');
        $collections = [];

        try {
            $client = clone $this->httpClient;
            $client->resetParameters(true);
            $client->setUri($qdrantUrl . '/collections');
            $client->setMethod('GET');
            $client->setOptions(['timeout' => 5]);
            $response = $client->send();

            if (!$response->isSuccess()) {
                return ['error' => 'Qdrant HTTP ' . $response->getStatusCode()];
            }

            $data = json_decode($response->getBody(), true);
            $names = [];
            foreach (($data['result']['collections'] ?? []) as $c) {
                $names[] = $c['name'];
            }
            sort($names);

            foreach ($names as $name) {
                $client->resetParameters(true);
                $client->setUri($qdrantUrl . '/collections/' . $name);
                $client->setMethod('GET');
                $client->setOptions(['timeout' => 5]);
                $resp = $client->send();

                if (!$resp->isSuccess()) {
                    $collections[] = ['name' => $name, 'error' => true];
                    continue;
                }

                $info = json_decode($resp->getBody(), true)['result'] ?? [];
                $vectorsConfig = $info['config']['params']['vectors'] ?? [];

                // Named vectors (e.g. omeka_items) vs single vector
                $vectors = [];
                if (isset($vectorsConfig['size'])) {
                    $vectors[] = [
                        'name' => '(default)',
                        'size' => $vectorsConfig['size'],
                        'distance' => $vectorsConfig['distance'] ?? '',
                    ];
                } else {
                    foreach ($vectorsConfig as $vName => $vConf) {
                        $vectors[] = [
                            'name' => $vName,
                            'size' => $vConf['size'] ?? 0,
                            'distance' => $vConf['distance'] ?? '',
                        ];
                    }
                }

                $quantization = $info['config']['quantization_config'] ?? null;
                $quantDesc = 'none';
                if ($quantization) {
                    if (isset($quantization['scalar'])) {
                        $quantDesc = $quantization['scalar']['type'] ?? 'scalar';
                    } elseif (isset($quantization['binary'])) {
                        $quantDesc = 'binary';
                    }
                }

                $collections[] = [
                    'name' => $name,
                    'status' => $info['status'] ?? 'unknown',
                    'points_count' => $info['points_count'] ?? 0,
                    'indexed_vectors_count' => $info['indexed_vectors_count'] ?? 0,
                    'segments' => $info['segments_count'] ?? 0,
                    'vectors' => $vectors,
                    'quantization' => $quantDesc,
                ];
            }
        } catch (\Exception $e) {
            return ['error' => 'Cannot connect to Qdrant: ' . $e->getMessage()];
        }

        return ['collections' => $collections];
    }

    private function getSetting(string $key, $default = null)
    {
        return $this->settings->get('motiftagger_' . $key, $this->config[$key] ?? $default);
    }

    private function getExistingMotifs(array $omekaIds): array
    {
        if (empty($omekaIds)) {
            return [];
        }

        $propertyId = (int) $this->getSetting('motif_property_id', 3);
        $conn = $this->entityManager->getConnection();
        $placeholders = implode(',', array_fill(0, count($omekaIds), '?'));
        $params = array_merge(array_map('intval', $omekaIds), [$propertyId]);

        $rows = $conn->fetchAllAssociative(
            "SELECT resource_id, value FROM value WHERE resource_id IN ($placeholders) AND property_id = ?",
            $params
        );

        $result = [];
        foreach ($rows as $row) {
            $result[(int) $row['resource_id']][] = $row['value'];
        }
        return $result;
    }

    private function getItemTitles(array $omekaIds): array
    {
        if (empty($omekaIds)) {
            return [];
        }

        $conn = $this->entityManager->getConnection();
        $placeholders = implode(',', array_fill(0, count($omekaIds), '?'));
        $params = array_map('intval', $omekaIds);

        // Property ID 1 = dcterms:title
        $rows = $conn->fetchAllAssociative(
            "SELECT resource_id, value FROM value WHERE resource_id IN ($placeholders) AND property_id = 1",
            $params
        );

        $result = [];
        foreach ($rows as $row) {
            $id = (int) $row['resource_id'];
            if (!isset($result[$id])) {
                $result[$id] = $row['value'];
            }
        }
        return $result;
    }

    /**
     * Build a patch payload preserving all existing property values and system keys.
     * Adapted from EnrichItem\Controller\EnrichController::buildPatchPayload.
     */
    private function buildPatchPayload(array $item): array
    {
        $payload = [];

        // Preserve all property values (keys containing ':' but not 'o:')
        foreach ($item as $key => $val) {
            if (strpos($key, ':') !== false && strpos($key, 'o:') !== 0 && is_array($val)) {
                $payload[$key] = array_map([$this, 'cleanValue'], $val);
            }
        }

        // Preserve system keys
        foreach (['o:resource_class', 'o:item_set', 'o:media', 'o:is_public', 'o:site'] as $sysKey) {
            if (isset($item[$sysKey])) {
                $payload[$sysKey] = $item[$sysKey];
            }
        }

        return $payload;
    }

    /**
     * Strip read-only keys from a value array for safe API writes.
     * Adapted from EnrichItem\Controller\EnrichController::cleanValue.
     */
    private function cleanValue(array $v): array
    {
        $writeKeys = ['type', 'property_id', '@value', '@id', '@language', 'o:label', 'value_resource_id', 'uri', 'o:is_public'];
        $clean = [];
        foreach ($writeKeys as $k) {
            if (array_key_exists($k, $v)) {
                $clean[$k] = $v[$k];
            }
        }
        return $clean;
    }
}
