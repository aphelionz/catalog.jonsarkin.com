<?php declare(strict_types=1);

namespace IconographicProfile\Controller;

use Doctrine\DBAL\Connection;
use Laminas\Mvc\Controller\AbstractActionController;

/**
 * Computes cultural reference frequency profiles by querying schema:mentions.
 *
 * For each mention on an item, returns how many other items in the corpus
 * share that reference, enabling a "rarity table" display on the item page.
 */
class MentionsController extends AbstractActionController
{
    private Connection $db;

    private const TEMPLATE_ID = 2;
    private const MENTIONS_PROPERTY_ID = 962;

    // Corpus stats cache
    private static ?array $corpusCache = null;
    private static float $corpusCacheTime = 0;
    private const CORPUS_CACHE_TTL = 300;

    public function __construct(Connection $db)
    {
        $this->db = $db;
    }

    /**
     * GET /mentions/:item_id/json
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

        $mentions = $this->getItemMentions($itemId);
        if ($mentions === null) {
            $response->setStatusCode(404);
            $response->setContent(json_encode(['error' => 'Item not found']));
            return $response;
        }

        if (empty($mentions) || (count($mentions) === 1 && $mentions[0] === 'NONE')) {
            $response->setStatusCode(404);
            $response->setContent(json_encode(['error' => 'No cultural references for this item']));
            return $response;
        }

        $corpus = $this->getCorpusStats();
        $profile = $this->buildProfile($mentions, $corpus);
        $profile['omeka_item_id'] = $itemId;

        $response->setContent(json_encode($profile));
        return $response;
    }

    private function getItemMentions(int $itemId): ?array
    {
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
            [$itemId, self::MENTIONS_PROPERTY_ID]
        );

        return array_column($rows, 'value');
    }

    private function getCorpusStats(): array
    {
        $now = microtime(true);
        if (self::$corpusCache !== null && ($now - self::$corpusCacheTime) < self::CORPUS_CACHE_TTL) {
            return self::$corpusCache;
        }

        $totalItems = (int) $this->db->fetchOne(
            'SELECT COUNT(DISTINCT v.resource_id)
             FROM value v
             JOIN resource r ON r.id = v.resource_id
             JOIN item i ON i.id = r.id
             WHERE v.property_id = ?
               AND v.value != ?
               AND (r.resource_template_id = ? OR r.resource_template_id IS NULL)',
            [self::MENTIONS_PROPERTY_ID, 'NONE', self::TEMPLATE_ID]
        );

        $rows = $this->db->fetchAllAssociative(
            'SELECT v.value AS mention, COUNT(DISTINCT v.resource_id) AS cnt
             FROM value v
             JOIN resource r ON r.id = v.resource_id
             JOIN item i ON i.id = r.id
             WHERE v.property_id = ?
               AND v.value != ?
               AND (r.resource_template_id = ? OR r.resource_template_id IS NULL)
             GROUP BY v.value',
            [self::MENTIONS_PROPERTY_ID, 'NONE', self::TEMPLATE_ID]
        );

        $mentionCounts = [];
        foreach ($rows as $row) {
            $mentionCounts[$row['mention']] = (int) $row['cnt'];
        }

        self::$corpusCache = [
            'total_items' => $totalItems,
            'mention_counts' => $mentionCounts,
        ];
        self::$corpusCacheTime = $now;

        return self::$corpusCache;
    }

    private function buildProfile(array $mentions, array $corpus): array
    {
        $totalItems = $corpus['total_items'];
        $mentionCounts = $corpus['mention_counts'];

        $refs = [];
        foreach ($mentions as $mention) {
            if ($mention === 'NONE') {
                continue;
            }

            $freq = $mentionCounts[$mention] ?? 0;
            $pct = $totalItems > 0 ? ($freq / $totalItems) * 100 : 0;

            // Extract category from brackets
            $category = '';
            if (preg_match('/\[([^\]]+)\]$/', $mention, $m)) {
                $category = $m[1];
            }

            // Clean display name (strip category bracket)
            $name = trim(preg_replace('/\s*\[[^\]]+\]$/', '', $mention));

            $refs[] = [
                'name' => $name,
                'category' => $category,
                'raw' => $mention,
                'corpus_frequency' => $freq,
                'corpus_percentage' => round($pct, 1),
                'unique' => $freq === 1,
            ];
        }

        // Sort: rarest first (lowest frequency)
        usort($refs, fn($a, $b) => $a['corpus_frequency'] <=> $b['corpus_frequency']);

        return [
            'mentions' => $refs,
            'corpus_size' => $totalItems,
        ];
    }
}
