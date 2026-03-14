<?php declare(strict_types=1);

namespace EnrichItem\Service;

use Doctrine\DBAL\Connection;

/**
 * DB-backed cache for enrichment results.
 *
 * Stores Claude enrichment JSON keyed by (item_id, prompt_version) so results
 * survive `make pull` and can be re-applied without API cost.
 */
class EnrichmentCache
{
    private Connection $conn;

    public function __construct(Connection $conn)
    {
        $this->conn = $conn;
    }

    public function ensureTable(): void
    {
        $this->conn->executeStatement("
            CREATE TABLE IF NOT EXISTS enrich_cache (
                item_id INT NOT NULL,
                prompt_version INT NOT NULL,
                enrichment JSON NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (item_id, prompt_version)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ");
    }

    public function get(int $itemId, int $promptVersion): ?array
    {
        $row = $this->conn->fetchAssociative(
            'SELECT enrichment FROM enrich_cache WHERE item_id = ? AND prompt_version = ?',
            [$itemId, $promptVersion]
        );
        if (!$row) {
            return null;
        }
        return json_decode($row['enrichment'], true);
    }

    public function put(int $itemId, int $promptVersion, array $enrichment): void
    {
        // Remove usage info before caching
        unset($enrichment['usage']);

        $json = json_encode($enrichment, JSON_UNESCAPED_UNICODE);
        $this->conn->executeStatement(
            'INSERT INTO enrich_cache (item_id, prompt_version, enrichment, created_at)
             VALUES (?, ?, ?, NOW())
             ON DUPLICATE KEY UPDATE enrichment = VALUES(enrichment), created_at = NOW()',
            [$itemId, $promptVersion, $json]
        );
    }

    /**
     * Return item IDs from the given list that do NOT have a cache entry.
     */
    public function getUncachedItemIds(array $itemIds, int $promptVersion): array
    {
        if (empty($itemIds)) {
            return [];
        }
        $placeholders = implode(',', array_fill(0, count($itemIds), '?'));
        $params = array_merge($itemIds, [$promptVersion]);
        $cached = $this->conn->fetchFirstColumn(
            "SELECT item_id FROM enrich_cache WHERE item_id IN ($placeholders) AND prompt_version = ?",
            $params
        );
        $cachedSet = array_flip($cached);
        return array_values(array_filter($itemIds, fn($id) => !isset($cachedSet[$id])));
    }

    /**
     * Return all cached enrichments for a given prompt version.
     *
     * @return array<int, array> Keyed by item_id
     */
    public function getAllForVersion(int $promptVersion): array
    {
        $rows = $this->conn->fetchAllAssociative(
            'SELECT item_id, enrichment FROM enrich_cache WHERE prompt_version = ?',
            [$promptVersion]
        );
        $result = [];
        foreach ($rows as $row) {
            $result[(int) $row['item_id']] = json_decode($row['enrichment'], true);
        }
        return $result;
    }

    /**
     * Count cached entries for a prompt version.
     */
    public function countForVersion(int $promptVersion): int
    {
        return (int) $this->conn->fetchOne(
            'SELECT COUNT(*) FROM enrich_cache WHERE prompt_version = ?',
            [$promptVersion]
        );
    }

}
