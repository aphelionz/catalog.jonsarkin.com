<?php declare(strict_types=1);

namespace EnrichItem\Service;

use Doctrine\DBAL\Connection;

/**
 * DB-backed cache for per-field enrichment results.
 *
 * Stores Claude enrichment values keyed by (item_id, property_id) so results
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
            CREATE TABLE IF NOT EXISTS enrich_field_cache (
                item_id INT NOT NULL,
                property_id INT NOT NULL,
                value TEXT NOT NULL,
                model VARCHAR(32) NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (item_id, property_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ");
    }

    public function get(int $itemId, int $propertyId): ?string
    {
        $row = $this->conn->fetchAssociative(
            'SELECT value FROM enrich_field_cache WHERE item_id = ? AND property_id = ?',
            [$itemId, $propertyId]
        );
        return $row ? $row['value'] : null;
    }

    public function put(int $itemId, int $propertyId, string $value, string $model): void
    {
        $this->conn->executeStatement(
            'INSERT INTO enrich_field_cache (item_id, property_id, value, model, created_at)
             VALUES (?, ?, ?, ?, NOW())
             ON DUPLICATE KEY UPDATE value = VALUES(value), model = VALUES(model), created_at = NOW()',
            [$itemId, $propertyId, $value, $model]
        );
    }

    /**
     * Return all cached values for a given property.
     *
     * @return array<int, string> Keyed by item_id
     */
    public function getAllForProperty(int $propertyId): array
    {
        $rows = $this->conn->fetchAllAssociative(
            'SELECT item_id, value FROM enrich_field_cache WHERE property_id = ?',
            [$propertyId]
        );
        $result = [];
        foreach ($rows as $row) {
            $result[(int) $row['item_id']] = $row['value'];
        }
        return $result;
    }

    /**
     * Return item IDs from the given list that do NOT have a cache entry for this property.
     */
    public function getUncachedItemIds(array $itemIds, int $propertyId): array
    {
        if (empty($itemIds)) {
            return [];
        }
        $placeholders = implode(',', array_fill(0, count($itemIds), '?'));
        $params = array_merge($itemIds, [$propertyId]);
        $cached = $this->conn->fetchFirstColumn(
            "SELECT item_id FROM enrich_field_cache WHERE item_id IN ($placeholders) AND property_id = ?",
            $params
        );
        $cachedSet = array_flip($cached);
        return array_values(array_filter($itemIds, fn($id) => !isset($cachedSet[$id])));
    }
}
