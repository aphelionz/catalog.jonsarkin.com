<?php declare(strict_types=1);

namespace IconographicProfile\Controller;

use Doctrine\DBAL\Connection;
use Laminas\Mvc\Controller\AbstractActionController;

/**
 * Computes iconographic rarity profiles by querying dcterms:subject
 * directly from MariaDB (source of truth).
 *
 * Rarity algorithm ported from sarkin-clip/clip_api/rarity.py:
 *   - IDF with Laplace smoothing: log((N+1) / (1+df))
 *   - Motif weights by category (visual=1.0, box=0.7, default=0.85)
 *   - Geometric mean of weighted IDFs, normalized to 0–100
 */
class IconographyController extends AbstractActionController
{
    private Connection $db;

    // Resource template for artwork items
    private const TEMPLATE_ID = 2;

    // dcterms:subject property ID
    private const SUBJECT_PROPERTY_ID = 3;

    // Corpus stats cache (shared across requests in the same PHP process)
    private static ?array $corpusCache = null;
    private static float $corpusCacheTime = 0;
    private const CORPUS_CACHE_TTL = 300; // 5 minutes

    private const VISUAL_MOTIFS = [
        'Eyes', 'Fish', 'Faces', 'Hands', 'Text Fragments', 'Grids',
        'Circles', 'Patterns', 'Animals', 'Names/Words', 'Maps', 'Numbers',
    ];

    private const BOX_CATEGORIES = [
        'Desert', 'Comic', 'Portraits', 'Ladies', 'Creature', 'Pop Culture',
        'Super Artist', 'Boat', 'Cardboard Artist', 'Ocean', 'Tree', 'Vehicle',
        'Building', 'Spiral/Mouth', 'Nipple', 'Brancusi', 'Skull', 'Window',
        'MRI', 'Heart', 'CBM', 'Pencil',
    ];

    private const VISUAL_WEIGHT = 1.0;
    private const BOX_WEIGHT = 0.7;
    private const DEFAULT_WEIGHT = 0.85;

    private const CLASS_THRESHOLDS = [20, 40, 60, 80];

    public function __construct(Connection $db)
    {
        $this->db = $db;
    }

    /**
     * GET /iconography/:item_id/json
     */
    public function itemAction()
    {
        $itemId = (int) $this->params()->fromRoute('item_id', 0);
        $response = $this->getResponse();
        $response->getHeaders()->addHeaderLine('Content-Type', 'application/json');

        if ($itemId <= 0) {
            $response->setStatusCode(404);
            $response->setContent(json_encode(['error' => 'Invalid item ID']));
            return $response;
        }

        $subjects = $this->getItemSubjects($itemId);
        if ($subjects === null) {
            $response->setStatusCode(404);
            $response->setContent(json_encode(['error' => 'Item not found']));
            return $response;
        }

        if (empty($subjects)) {
            $response->setStatusCode(404);
            $response->setContent(json_encode(['error' => 'No subjects for this item']));
            return $response;
        }

        $corpus = $this->getCorpusStats();
        $rarity = $this->computeRarity($subjects, $corpus);
        $rarity['omeka_item_id'] = $itemId;

        $response->setContent(json_encode($rarity));
        return $response;
    }

    /**
     * GET /iconography/batch/json?ids=1,2,3
     */
    public function batchAction()
    {
        $response = $this->getResponse();
        $response->getHeaders()->addHeaderLine('Content-Type', 'application/json');

        $idsParam = $this->params()->fromQuery('ids', '');
        $ids = array_filter(array_map('intval', explode(',', $idsParam)));

        if (empty($ids)) {
            $response->setContent(json_encode(['items' => []]));
            return $response;
        }

        if (count($ids) > 100) {
            $response->setStatusCode(400);
            $response->setContent(json_encode(['error' => 'Max 100 IDs per request']));
            return $response;
        }

        $corpus = $this->getCorpusStats();
        $allSubjects = $this->getBatchSubjects($ids);
        $items = [];

        foreach ($ids as $id) {
            $subjects = $allSubjects[$id] ?? null;
            if ($subjects === null || empty($subjects)) {
                continue;
            }
            $rarity = $this->computeRarity($subjects, $corpus);
            $items[] = [
                'omeka_item_id' => $id,
                'class_number' => $rarity['class_number'],
            ];
        }

        $response->setContent(json_encode(['items' => $items]));
        return $response;
    }

    /**
     * Get dcterms:subject values for a single item.
     * Returns null if the item doesn't exist, empty array if no subjects.
     */
    private function getItemSubjects(int $itemId): ?array
    {
        // Check item exists (artwork template or NULL — excludes writings)
        $exists = $this->db->fetchOne(
            'SELECT 1 FROM resource r JOIN item i ON i.id = r.id
             WHERE r.id = ? AND (r.resource_template_id = ? OR r.resource_template_id IS NULL)',
            [$itemId, self::TEMPLATE_ID]
        );
        if (!$exists) {
            return null;
        }

        $rows = $this->db->fetchAllAssociative(
            'SELECT v.value FROM value v WHERE v.resource_id = ? AND v.property_id = ? ORDER BY v.id',
            [$itemId, self::SUBJECT_PROPERTY_ID]
        );

        return array_column($rows, 'value');
    }

    /**
     * Get dcterms:subject values for multiple items.
     * Returns [item_id => [subjects...]].
     */
    private function getBatchSubjects(array $itemIds): array
    {
        $placeholders = implode(',', array_fill(0, count($itemIds), '?'));
        $params = array_merge($itemIds, [self::TEMPLATE_ID, self::SUBJECT_PROPERTY_ID]);

        $rows = $this->db->fetchAllAssociative(
            "SELECT v.resource_id AS item_id, v.value
             FROM value v
             JOIN resource r ON r.id = v.resource_id
             JOIN item i ON i.id = r.id
             WHERE v.resource_id IN ($placeholders)
               AND (r.resource_template_id = ? OR r.resource_template_id IS NULL)
               AND v.property_id = ?
             ORDER BY v.resource_id, v.id",
            $params
        );

        $result = [];
        foreach ($rows as $row) {
            $result[(int) $row['item_id']][] = $row['value'];
        }

        return $result;
    }

    /**
     * Build corpus-wide motif frequency stats, cached for CORPUS_CACHE_TTL seconds.
     * Returns ['total_items' => int, 'motif_counts' => [motif => count]].
     */
    private function getCorpusStats(): array
    {
        $now = microtime(true);
        if (self::$corpusCache !== null && ($now - self::$corpusCacheTime) < self::CORPUS_CACHE_TTL) {
            return self::$corpusCache;
        }

        // Count total artwork items (template 2 or NULL — excludes writings)
        $totalItems = (int) $this->db->fetchOne(
            'SELECT COUNT(*) FROM resource r JOIN item i ON i.id = r.id
             WHERE r.resource_template_id = ? OR r.resource_template_id IS NULL',
            [self::TEMPLATE_ID]
        );

        // Count items per motif
        $rows = $this->db->fetchAllAssociative(
            'SELECT v.value AS motif, COUNT(DISTINCT v.resource_id) AS cnt
             FROM value v
             JOIN resource r ON r.id = v.resource_id
             JOIN item i ON i.id = r.id
             WHERE (r.resource_template_id = ? OR r.resource_template_id IS NULL)
               AND v.property_id = ?
             GROUP BY v.value',
            [self::TEMPLATE_ID, self::SUBJECT_PROPERTY_ID]
        );

        $motifCounts = [];
        foreach ($rows as $row) {
            $motifCounts[$row['motif']] = (int) $row['cnt'];
        }

        self::$corpusCache = [
            'total_items' => $totalItems,
            'motif_counts' => $motifCounts,
        ];
        self::$corpusCacheTime = $now;

        return self::$corpusCache;
    }

    /**
     * Compute rarity for an item given its subjects and corpus stats.
     * Port of sarkin-clip/clip_api/rarity.py compute_item_rarity().
     */
    private function computeRarity(array $subjects, array $corpus): array
    {
        $totalItems = $corpus['total_items'];
        $motifCounts = $corpus['motif_counts'];

        $motifs = [];
        $weightedIdfs = [];

        foreach ($subjects as $motif) {
            $docFreq = $motifCounts[$motif] ?? 0;
            $idf = log(($totalItems + 1) / (1 + $docFreq));
            $weight = $this->motifWeight($motif);
            $wIdf = $idf * $weight;
            $weightedIdfs[] = $wIdf;

            $pct = $totalItems > 0 ? ($docFreq / $totalItems) * 100 : 0;
            $motifs[] = [
                'motif' => $motif,
                'corpus_frequency' => $docFreq,
                'corpus_percentage' => round($pct, 1),
                'weighted_idf' => round($wIdf, 4),
            ];
        }

        // Sort rarest first (highest weighted IDF)
        usort($motifs, fn($a, $b) => $b['weighted_idf'] <=> $a['weighted_idf']);

        // Geometric mean of weighted IDFs
        $product = 1.0;
        foreach ($weightedIdfs as $val) {
            $product *= max($val, 0.001);
        }
        $geoMean = $product ** (1.0 / count($weightedIdfs));

        // Normalize to 0–100
        $maxIdf = log($totalItems + 1);
        $rawScore = $maxIdf > 0 ? ($geoMean / $maxIdf) * 100 : 0;
        $score = max(0.0, min(100.0, round($rawScore, 1)));

        // Strip weighted_idf from response (internal only)
        $responseMotifs = array_map(function ($m) {
            return [
                'motif' => $m['motif'],
                'corpus_frequency' => $m['corpus_frequency'],
                'corpus_percentage' => $m['corpus_percentage'],
            ];
        }, $motifs);

        return [
            'omeka_item_id' => 0, // caller should override if needed
            'score' => $score,
            'class_number' => $this->scoreToClass($score),
            'motifs' => $responseMotifs,
            'corpus_size' => $totalItems,
        ];
    }

    private function motifWeight(string $motif): float
    {
        if (in_array($motif, self::VISUAL_MOTIFS, true)) {
            return self::VISUAL_WEIGHT;
        }
        if (in_array($motif, self::BOX_CATEGORIES, true)) {
            return self::BOX_WEIGHT;
        }
        return self::DEFAULT_WEIGHT;
    }

    private function scoreToClass(float $score): int
    {
        foreach (self::CLASS_THRESHOLDS as $i => $threshold) {
            if ($score <= $threshold) {
                return $i + 1;
            }
        }
        return 5;
    }
}
