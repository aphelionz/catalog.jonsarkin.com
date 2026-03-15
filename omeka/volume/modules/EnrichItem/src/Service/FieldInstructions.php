<?php declare(strict_types=1);

namespace EnrichItem\Service;

use Doctrine\DBAL\Connection;

/**
 * DB-backed storage for per-field enrichment instructions.
 */
class FieldInstructions
{
    private Connection $conn;

    public function __construct(Connection $conn)
    {
        $this->conn = $conn;
    }

    public function ensureTable(): void
    {
        $this->conn->executeStatement("
            CREATE TABLE IF NOT EXISTS enrich_field_instructions (
                property_id INT NOT NULL PRIMARY KEY,
                instructions TEXT NOT NULL,
                model VARCHAR(16) NOT NULL DEFAULT 'haiku',
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ");
    }

    public function get(int $propertyId): ?array
    {
        $row = $this->conn->fetchAssociative(
            'SELECT instructions, model FROM enrich_field_instructions WHERE property_id = ?',
            [$propertyId]
        );
        return $row ?: null;
    }

    public function getAll(): array
    {
        $rows = $this->conn->fetchAllAssociative(
            'SELECT property_id, instructions, model FROM enrich_field_instructions'
        );
        $result = [];
        foreach ($rows as $row) {
            $result[(int) $row['property_id']] = [
                'instructions' => $row['instructions'],
                'model' => $row['model'],
            ];
        }
        return $result;
    }

    public function save(int $propertyId, string $instructions, string $model): void
    {
        $this->conn->executeStatement(
            'INSERT INTO enrich_field_instructions (property_id, instructions, model, updated_at)
             VALUES (?, ?, ?, NOW())
             ON DUPLICATE KEY UPDATE instructions = VALUES(instructions), model = VALUES(model), updated_at = NOW()',
            [$propertyId, $instructions, $model]
        );
    }
}
