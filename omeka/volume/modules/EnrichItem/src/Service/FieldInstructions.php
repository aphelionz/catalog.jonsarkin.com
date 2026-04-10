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
                source_property_id INT DEFAULT NULL,
                empty_value VARCHAR(255) DEFAULT NULL,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ");

        // Migration: add source_property_id if table already exists without it
        try {
            $this->conn->executeStatement(
                'ALTER TABLE enrich_field_instructions ADD COLUMN source_property_id INT DEFAULT NULL AFTER model'
            );
        } catch (\Throwable $e) {
            // Column already exists — ignore
        }

        // Migration: add empty_value if table already exists without it
        try {
            $this->conn->executeStatement(
                'ALTER TABLE enrich_field_instructions ADD COLUMN empty_value VARCHAR(255) DEFAULT NULL AFTER source_property_id'
            );
        } catch (\Throwable $e) {
            // Column already exists — ignore
        }
    }

    public function get(int $propertyId): ?array
    {
        $row = $this->conn->fetchAssociative(
            'SELECT instructions, model, source_property_id, empty_value FROM enrich_field_instructions WHERE property_id = ?',
            [$propertyId]
        );
        if (!$row) {
            return null;
        }
        $row['source_property_id'] = $row['source_property_id'] ? (int) $row['source_property_id'] : null;
        $row['empty_value'] = $row['empty_value'] ?: null;
        return $row;
    }

    public function getAll(): array
    {
        $rows = $this->conn->fetchAllAssociative(
            'SELECT property_id, instructions, model, source_property_id, empty_value FROM enrich_field_instructions'
        );
        $result = [];
        foreach ($rows as $row) {
            $result[(int) $row['property_id']] = [
                'instructions' => $row['instructions'],
                'model' => $row['model'],
                'source_property_id' => $row['source_property_id'] ? (int) $row['source_property_id'] : null,
                'empty_value' => $row['empty_value'] ?: null,
            ];
        }
        return $result;
    }

    public function save(int $propertyId, string $instructions, string $model, ?int $sourcePropertyId = null, ?string $emptyValue = null): void
    {
        $this->conn->executeStatement(
            'INSERT INTO enrich_field_instructions (property_id, instructions, model, source_property_id, empty_value, updated_at)
             VALUES (?, ?, ?, ?, ?, NOW())
             ON DUPLICATE KEY UPDATE instructions = VALUES(instructions), model = VALUES(model),
                                      source_property_id = VALUES(source_property_id),
                                      empty_value = VALUES(empty_value), updated_at = NOW()',
            [$propertyId, $instructions, $model, $sourcePropertyId, $emptyValue]
        );
    }
}
