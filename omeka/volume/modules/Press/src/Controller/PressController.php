<?php declare(strict_types=1);

namespace Press\Controller;

use Doctrine\DBAL\Connection;
use Laminas\Mvc\Controller\AbstractActionController;
use Laminas\View\Model\ViewModel;

/**
 * Public chronological wall of press coverage about Jon Sarkin.
 *
 * Members of the curated "Press" item set (resource_template_id = 3, public)
 * are listed newest-first by dcterms:date. Each entry shows the publication's
 * logo and links to the catalog's own page for the piece.
 */
class PressController extends AbstractActionController
{
    private Connection $conn;
    private array $config;

    public function __construct(Connection $conn, array $config)
    {
        $this->conn = $conn;
        $this->config = $config;
    }

    public function indexAction()
    {
        $tplId = (int) ($this->config['resource_template_id'] ?? 3);
        $setTitle = (string) ($this->config['item_set_title'] ?? 'Press');
        $pTitle = (int) ($this->config['property_id_title'] ?? 1);
        $pDate = (int) ($this->config['property_id_date'] ?? 7);
        $pPublisher = (int) ($this->config['property_id_publisher'] ?? 5);
        $logoMap = (array) ($this->config['outlet_logos'] ?? []);

        // Resolve the curated set id by title (item-set ids differ per environment).
        // Join item_set to guarantee the matched resource is an item set.
        $setId = (int) $this->conn->fetchOne(
            'SELECT r.id FROM resource r '
            . 'JOIN item_set s ON s.id = r.id '
            . 'JOIN `value` v ON v.resource_id = r.id AND v.property_id = :pTitle '
            . 'WHERE v.value = :title LIMIT 1',
            ['pTitle' => $pTitle, 'title' => $setTitle]
        );
        if (!$setId) {
            return new ViewModel(['press' => [], 'total' => 0]);
        }

        // One-pass pull of the curated press items. Sort by ISO date
        // (dcterms:date, YYYY-MM-DD) DESC — lexicographic order matches
        // chronological; undated rows fall to the bottom. Tie-break by id.
        $sql = <<<SQL
SELECT
    i.id AS id,
    MAX(CASE WHEN v.property_id = :pTitle     THEN v.value END) AS title,
    MAX(CASE WHEN v.property_id = :pDate      THEN v.value END) AS date,
    MAX(CASE WHEN v.property_id = :pPublisher THEN v.value END) AS publisher
FROM item i
JOIN resource r ON r.id = i.id
JOIN item_item_set iis ON iis.item_id = i.id AND iis.item_set_id = :setId
LEFT JOIN value v ON v.resource_id = i.id
WHERE r.resource_template_id = :tplId
  AND r.is_public = 1
GROUP BY i.id
ORDER BY date DESC, i.id DESC
SQL;

        $stmt = $this->conn->executeQuery($sql, [
            'pTitle' => $pTitle,
            'pDate' => $pDate,
            'pPublisher' => $pPublisher,
            'setId' => $setId,
            'tplId' => $tplId,
        ]);

        $rows = $stmt->fetchAllAssociative();

        // Resolve each row's outlet + logo slug from the config map.
        foreach ($rows as &$row) {
            $outlet = trim((string) ($row['publisher'] ?? ''));
            $row['outlet'] = $outlet;
            $row['logo_slug'] = $logoMap[$outlet] ?? '';
        }
        unset($row);

        return new ViewModel([
            'press' => $rows,
            'total' => count($rows),
        ]);
    }
}
