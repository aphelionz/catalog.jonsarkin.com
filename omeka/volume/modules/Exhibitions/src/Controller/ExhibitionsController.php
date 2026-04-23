<?php declare(strict_types=1);

namespace Exhibitions\Controller;

use Doctrine\DBAL\Connection;
use Laminas\Mvc\Controller\AbstractActionController;
use Laminas\View\Model\ViewModel;

/**
 * Public chronological browse of Exhibition items (resource_template_id = 4).
 *
 * Exhibitions with at least one incoming bibo:presentedAt link are "clickable"
 * (they have piece-level documentation worth surfacing); the rest render as
 * static rows.
 */
class ExhibitionsController extends AbstractActionController
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
        $tplId = (int) ($this->config['resource_template_id'] ?? 4);
        $pTitle = (int) ($this->config['property_id_title'] ?? 1);
        $pDate = (int) ($this->config['property_id_date'] ?? 7);
        $pType = (int) ($this->config['property_id_type'] ?? 8);
        $pVenue = (int) ($this->config['property_id_venue'] ?? 230);
        $pOrganizer = (int) ($this->config['property_id_organizer'] ?? 1202);
        $pPresentedAt = (int) ($this->config['property_id_presented_at'] ?? 74);

        // Pull exhibition items + key field values in one pass. We include
        // linked_count so the view can mark clickable rows without a second round trip.
        $sql = <<<SQL
SELECT
    i.id AS id,
    MAX(CASE WHEN v.property_id = :pTitle     THEN v.value END) AS title,
    MAX(CASE WHEN v.property_id = :pDate      THEN v.value END) AS date,
    MAX(CASE WHEN v.property_id = :pType      THEN v.value END) AS type,
    MAX(CASE WHEN v.property_id = :pVenue     THEN v.value END) AS venue,
    MAX(CASE WHEN v.property_id = :pOrganizer THEN v.value END) AS organizer,
    (
        SELECT COUNT(*)
        FROM value vp
        WHERE vp.property_id = :pPresentedAt
          AND vp.value_resource_id = i.id
    ) AS linked_count
FROM item i
JOIN resource r ON r.id = i.id
LEFT JOIN value v ON v.resource_id = i.id
WHERE r.resource_template_id = :tplId
  AND r.is_public = 1
GROUP BY i.id
SQL;

        $stmt = $this->conn->executeQuery($sql, [
            'pTitle' => $pTitle,
            'pDate' => $pDate,
            'pType' => $pType,
            'pVenue' => $pVenue,
            'pOrganizer' => $pOrganizer,
            'pPresentedAt' => $pPresentedAt,
            'tplId' => $tplId,
        ]);

        $rows = $stmt->fetchAllAssociative();

        // Sort reverse-chronological using strtotime() on the freeform date
        // string. Unparseable dates sort last. Ties broken by item id desc
        // so the newest-added stays on top.
        usort($rows, function ($a, $b) {
            $ta = $a['date'] ? (strtotime((string) $a['date']) ?: 0) : 0;
            $tb = $b['date'] ? (strtotime((string) $b['date']) ?: 0) : 0;
            if ($ta === $tb) {
                return ((int) $b['id']) <=> ((int) $a['id']);
            }
            return $tb <=> $ta;
        });

        $view = new ViewModel([
            'exhibitions' => $rows,
            'total' => count($rows),
        ]);
        return $view;
    }
}
