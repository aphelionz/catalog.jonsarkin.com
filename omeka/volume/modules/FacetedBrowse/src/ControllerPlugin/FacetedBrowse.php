<?php
namespace FacetedBrowse\ControllerPlugin;

use FacetedBrowse\Api\Representation\FacetedBrowseCategoryRepresentation;
use Omeka\Api\Exception\NotFoundException;
use Zend\Mvc\Controller\Plugin\AbstractPlugin;
use Zend\ServiceManager\ServiceLocatorInterface;

class FacetedBrowse extends AbstractPlugin
{
    protected $services;

    public function __construct(ServiceLocatorInterface $services)
    {
        $this->services = $services;
    }

    /**
     * Get a FacetedBrowse representation.
     *
     * Provides a single method to get a FacetedBrowse page or category record
     * representation. Used primarily to ensure that the route is valid.
     *
     * @param int $pageId
     * @param int|null $categoryId
     * @return FacetedBrowsePageRepresentation|FacetedBrowseCategoryRepresentation
     */
    public function getRepresentation($pageId, $categoryId = null)
    {
        $controller = $this->getController();
        if ($categoryId) {
            try {
                $category = $controller->api()->read('faceted_browse_categories', $categoryId)->getContent();
            } catch (NotFoundException $e) {
                return false;
            }
            $page = $category->page();
            return ($pageId == $page->id()) ? $category : false;
        }
        try {
            $page = $controller->api()->read('faceted_browse_pages', $pageId)->getContent();
        } catch (NotFoundException $e) {
            return false;
        }
        return $page;
    }

    /**
     * Get the facet type manager.
     *
     * @return FacetedBrowse\FacetType\Manager
     */
    public function getFacetTypes()
    {
        return $this->services->get('FacetedBrowse\FacetTypeManager');
    }

    /**
     * Get the column type manager.
     *
     * @return FacetedBrowse\ColumnType\Manager
     */
    public function getColumnTypes()
    {
        return $this->services->get('FacetedBrowse\ColumnTypeManager');
    }

    /**
     * Get the sortings for a browse page.
     *
     * @param ?FacetedBrowseCategoryRepresentation $category
     * @return array
     */
    public function getSortings(?FacetedBrowseCategoryRepresentation $category)
    {
        $controller = $this->getController();
        $sortConfig = $this->services->get('Omeka\Browse')->getSortConfig('public', 'items');
        if ($category) {
            // Get sortings for a category.
            foreach ($category->columns() as $column) {
                if ($column->excludeSortBy()) {
                    // Don't include sorting if it was excluded.
                    continue;
                }
                $sortBy = $column->sortBy();
                if ($sortBy) {
                    $sortConfig[$column->sortBy()] = $controller->translate($column->name());
                }
            }
        }
        // Add custom artwork sort options.
        $sortConfig['dcterms:date'] = 'Year';
        $sortConfig['size'] = 'Size';
        $sortings = [];
        foreach ($sortConfig as $sortKey => $sortValue) {
            $sortings[] = [
                'label' => $sortValue,
                'value' => $sortKey,
            ];
        }
        return $sortings;
    }

    /**
     * Get the value options for a sort by select element.
     *
     * @param ?FacetedBrowseCategoryRepresentation $category
     * @return array
     */
    public function getSortByValueOptions(?FacetedBrowseCategoryRepresentation $category = null)
    {
        $sortByValueOptions = [];
        foreach ($this->getSortings($category) as $sorting) {
            $sortByValueOptions[$sorting['value']] = $sorting['label'];
        }
        return $sortByValueOptions;
    }

    /**
     * Build filter params excluding a specific facet's own filter key.
     *
     * When computing counts for facet X, we exclude facet X's filter so
     * that selecting a value in X doesn't hide the other values in X.
     *
     * @param array $params Current filter params from the URL
     * @param string $excludeKey The key to exclude (e.g. 'resource_class_id')
     * @param int|null $excludePropertyId For 'property' exclusion, only exclude entries matching this property ID
     * @return array Filtered params
     */
    public function buildBaseParams(array $params, string $excludeKey, $excludePropertyId = null): array
    {
        $query = [];
        foreach ($params as $key => $value) {
            if ($key === $excludeKey) {
                if ($excludeKey === 'property' && $excludePropertyId !== null && is_array($value)) {
                    $kept = [];
                    foreach ($value as $entry) {
                        if (isset($entry['property']) && (string)$entry['property'] !== (string)$excludePropertyId) {
                            $kept[] = $entry;
                        }
                    }
                    if ($kept) {
                        $query['property'] = array_values($kept);
                    }
                }
                continue;
            }
            $query[$key] = $value;
        }
        return $query;
    }

    /**
     * Compute facet value counts using GROUP BY queries.
     *
     * For each facet, builds a base query excluding that facet's own filter,
     * gets the filtered item IDs, and runs a single GROUP BY query to get
     * all value counts at once.
     *
     * @param FacetedBrowseCategoryRepresentation $category
     * @param array $filterParams Current filter params from the URL
     * @param int $siteId
     * @return array [facetId => [valueKey => count, ...], ...]
     */
    public function computeFacetCounts(FacetedBrowseCategoryRepresentation $category, array $filterParams, int $siteId): array
    {
        $em = $this->services->get('Omeka\EntityManager');
        $facetTypeManager = $this->getFacetTypes();

        // Get category resource IDs (if category has a restricting query)
        $categoryResourceIds = null;
        parse_str($category->query() ?? '', $categoryQuery);
        if ($categoryQuery) {
            $categoryResourceIds = $this->getCategoryResourceIds('items', $categoryQuery);
        }

        $facetCounts = [];
        foreach ($category->facets() as $facet) {
            $facetType = $facetTypeManager->get($facet->type());
            if ($facetType instanceof \FacetedBrowse\FacetType\Unknown) {
                continue;
            }

            $type = $facet->type();

            if (!in_array($type, ['resource_class', 'item_set', 'resource_template', 'value'])) {
                continue;
            }

            $baseParams = $filterParams;
            $baseParams['site_id'] = $siteId;
            if ($categoryResourceIds !== null) {
                $baseParams['id'] = $categoryResourceIds;
            }

            // Get filtered item IDs (single API call returning scalar IDs)
            $itemIds = $this->getCategoryResourceIds('items', $baseParams) ?: [0];

            if ($type === 'resource_class') {
                $classIds = $facet->data('class_ids', []);
                if (empty($classIds)) {
                    continue;
                }
                $dql = 'SELECT rc.id AS id, COUNT(r.id) AS cnt
                        FROM Omeka\Entity\Item r
                        JOIN r.resourceClass rc
                        WHERE r.id IN (:itemIds) AND rc.id IN (:classIds)
                        GROUP BY rc.id';
                $results = $em->createQuery($dql)
                    ->setParameter('itemIds', $itemIds)
                    ->setParameter('classIds', $classIds)
                    ->getResult();

                $facetCounts[$facet->id()] = [];
                foreach ($results as $row) {
                    $facetCounts[$facet->id()][(int)$row['id']] = (int)$row['cnt'];
                }
            }

            if ($type === 'item_set') {
                $setIds = $facet->data('item_set_ids', []);
                if (empty($setIds)) {
                    continue;
                }
                $dql = 'SELECT iset.id AS id, COUNT(r.id) AS cnt
                        FROM Omeka\Entity\Item r
                        JOIN r.itemSets iset
                        WHERE r.id IN (:itemIds) AND iset.id IN (:setIds)
                        GROUP BY iset.id';
                $results = $em->createQuery($dql)
                    ->setParameter('itemIds', $itemIds)
                    ->setParameter('setIds', $setIds)
                    ->getResult();

                $facetCounts[$facet->id()] = [];
                foreach ($results as $row) {
                    $facetCounts[$facet->id()][(int)$row['id']] = (int)$row['cnt'];
                }
            }

            if ($type === 'resource_template') {
                $templateIds = $facet->data('template_ids', []);
                if (empty($templateIds)) {
                    continue;
                }
                $dql = 'SELECT rt.id AS id, COUNT(r.id) AS cnt
                        FROM Omeka\Entity\Item r
                        JOIN r.resourceTemplate rt
                        WHERE r.id IN (:itemIds) AND rt.id IN (:templateIds)
                        GROUP BY rt.id';
                $results = $em->createQuery($dql)
                    ->setParameter('itemIds', $itemIds)
                    ->setParameter('templateIds', $templateIds)
                    ->getResult();

                $facetCounts[$facet->id()] = [];
                foreach ($results as $row) {
                    $facetCounts[$facet->id()][(int)$row['id']] = (int)$row['cnt'];
                }
            }

            if ($type === 'value') {
                $facetCounts[$facet->id()] = $this->computeValueFacetCounts(
                    $em, $itemIds, $facet, $baseParams
                );
            }
        }

        return $facetCounts;
    }

    /**
     * Compute counts for a single value-type facet.
     *
     * Uses GROUP BY for exact-match query types (eq, neq) and falls back
     * to per-value API queries for substring types (in, nin).
     */
    protected function computeValueFacetCounts($em, array $itemIds, $facet, array $baseParams): array
    {
        $propertyId = $facet->data('property_id');
        $queryType = $facet->data('query_type');
        $configuredValues = array_filter(
            array_map('trim', explode("\n", $facet->data('values') ?? '')),
            fn($v) => $v !== ''
        );
        if (empty($configuredValues)) {
            return [];
        }

        switch ($queryType) {
            case 'eq':
            case 'neq':
                $qb = $em->createQueryBuilder();
                $qb->select('v.value AS val', 'COUNT(v) AS cnt')
                    ->from('Omeka\Entity\Value', 'v')
                    ->where($qb->expr()->in('v.resource', $itemIds))
                    ->andWhere('v.property = :propertyId')
                    ->setParameter('propertyId', $propertyId)
                    ->groupBy('v.value');
                // Filter to only configured values
                $qb->andWhere($qb->expr()->in('v.value', ':configValues'))
                    ->setParameter('configValues', $configuredValues, \Doctrine\DBAL\Connection::PARAM_STR_ARRAY);

                $counts = [];
                foreach ($qb->getQuery()->getResult() as $row) {
                    $counts[$row['val']] = (int)$row['cnt'];
                }
                return $counts;

            case 'res':
            case 'nres':
                $resIds = [];
                foreach ($configuredValues as $val) {
                    if (preg_match('/^(\d+)/', $val, $m)) {
                        $resIds[] = (int)$m[1];
                    }
                }
                if (empty($resIds)) {
                    return [];
                }
                $qb = $em->createQueryBuilder();
                $qb->select('vr.id AS resId', 'COUNT(v) AS cnt')
                    ->from('Omeka\Entity\Value', 'v')
                    ->join('v.valueResource', 'vr')
                    ->where($qb->expr()->in('v.resource', $itemIds))
                    ->andWhere('v.property = :propertyId')
                    ->andWhere($qb->expr()->in('vr.id', $resIds))
                    ->setParameter('propertyId', $propertyId)
                    ->groupBy('vr.id');

                $counts = [];
                foreach ($qb->getQuery()->getResult() as $row) {
                    $counts[(string)$row['resId']] = (int)$row['cnt'];
                }
                return $counts;

            case 'ex':
            case 'nex':
                $propIds = [];
                foreach ($configuredValues as $val) {
                    if (preg_match('/^(\d+)/', $val, $m)) {
                        $propIds[] = (int)$m[1];
                    }
                }
                if (empty($propIds)) {
                    return [];
                }
                $qb = $em->createQueryBuilder();
                $qb->select('p.id AS propId', 'COUNT(DISTINCT v.resource) AS cnt')
                    ->from('Omeka\Entity\Value', 'v')
                    ->join('v.property', 'p')
                    ->where($qb->expr()->in('v.resource', $itemIds))
                    ->andWhere($qb->expr()->in('p.id', $propIds))
                    ->groupBy('p.id');

                $counts = [];
                foreach ($qb->getQuery()->getResult() as $row) {
                    $counts[(string)$row['propId']] = (int)$row['cnt'];
                }
                return $counts;

            case 'in':
            case 'nin':
            default:
                // Substring matching can't use GROUP BY — fall back to per-value queries
                $api = $this->services->get('Omeka\ApiManager');
                $counts = [];
                foreach ($configuredValues as $val) {
                    $q = $baseParams;
                    $q['property'] = array_merge($q['property'] ?? [], [[
                        'property' => $propertyId,
                        'type' => $queryType,
                        'text' => $val,
                    ]]);
                    $counts[$val] = $api->search('items', $q, ['limit' => 0])->getTotalResults();
                }
                return $counts;
        }
    }

    /**
     * Get all available values and their counts of a property.
     *
     * @param string $resourceType
     * @param int $propertyId
     * @param string $queryType
     * @param array $categoryQuery
     * @return array
     */
    public function getValueValues($resourceType, $propertyId, $queryType, array $categoryQuery)
    {
        $em = $this->services->get('Omeka\EntityManager');
        $qb = $em->createQueryBuilder();
        // Cannot use an empty array to calculate IN(). It results in a Doctrine
        // QueryException. Instead, use an array containing one nonexistent ID.
        $itemIds = $this->getCategoryResourceIds($resourceType, $categoryQuery) ?: [0];
        $qb->from('Omeka\Entity\Value', 'v')
            ->andWhere($qb->expr()->in('v.resource', $itemIds))
            ->groupBy('label')
            ->orderBy('has_count', 'DESC')
            ->addOrderBy('label', 'ASC');
        switch ($queryType) {
            case 'res':
            case 'nres':
                $qb->select("0 id, CONCAT(vr.id, ' ', vr.title) label", 'COUNT(v) has_count')
                    ->join('v.valueResource', 'vr');
                break;
            case 'ex':
            case 'nex':
                $qb->select("0 id, CONCAT(p.id, ' ', vo.label, ': ', p.label) label", 'COUNT(v) has_count')
                    ->join('v.property', 'p')
                    ->join('p.vocabulary', 'vo');
                break;
            case 'eq':
            case 'neq':
            case 'in':
            case 'nin':
            default:
                $qb->select('0 id, v.value label', 'COUNT(v.value) has_count');
        }
        if ($propertyId) {
            $qb->andWhere('v.property = :propertyId')
                ->setParameter('propertyId', $propertyId);
        }
        return $qb->getQuery()->getResult();
    }

    /**
     * Get all available classes and their counts.
     *
     * @param string $resourceType
     * @param arry $query
     * @return array
     */
    public function getResourceClassClasses($resourceType, array $categoryQuery)
    {
        $em = $this->services->get('Omeka\EntityManager');
        $dql = sprintf('
        SELECT rc.id id, CONCAT(v.label, \': \', rc.label) label, COUNT(r.id) has_count
        FROM %s r
        JOIN r.resourceClass rc
        JOIN rc.vocabulary v
        WHERE r.id IN (:resourceIds)
        GROUP BY rc.id
        ORDER BY has_count DESC', $this->getResourceEntityClass($resourceType));
        $query = $em->createQuery($dql)
            ->setParameter('resourceIds', $this->getCategoryResourceIds($resourceType, $categoryQuery));
        return $query->getResult();
    }

    /**
     * Get all available templates and their counts.
     *
     * @param string $resourceType
     * @param arry $categoryQuery
     * @return array
     */
    public function getResourceTemplateTemplates($resourceType, array $categoryQuery)
    {
        $em = $this->services->get('Omeka\EntityManager');
        $dql = sprintf('
        SELECT rt.id id, rt.label label, COUNT(r.id) has_count
        FROM %s r
        JOIN r.resourceTemplate rt
        WHERE r.id IN (:resourceIds)
        GROUP BY rt.id
        ORDER BY has_count DESC', $this->getResourceEntityClass($resourceType));
        $query = $em->createQuery($dql)
            ->setParameter('resourceIds', $this->getCategoryResourceIds($resourceType, $categoryQuery));
        return $query->getResult();
    }

    /**
     * Get all available item sets and their counts.
     *
     * @param string $resourceType
     * @param arry $query
     * @return array
     */
    public function getItemSetItemSets($resourceType, array $categoryQuery)
    {
        $em = $this->services->get('Omeka\EntityManager');
        $dql = sprintf('
        SELECT iset.id id, iset.title label, COUNT(r.id) has_count
        FROM %s r
        JOIN r.itemSets iset
        WHERE r.id IN (:resourceIds)
        GROUP BY iset.id
        ORDER BY has_count DESC', $this->getResourceEntityClass($resourceType));
        $query = $em->createQuery($dql)
            ->setParameter('resourceIds', $this->getCategoryResourceIds($resourceType, $categoryQuery));
        return $query->getResult();
    }

    /**
     * Get the IDs of all resources that satisfy the query.
     *
     * @param string $resourceType
     * @param array $categoryQuery
     * @return array
     */
    public function getCategoryResourceIds($resourceType, array $categoryQuery)
    {
        $api = $this->services->get('Omeka\ApiManager');
        return $api->search($resourceType, $categoryQuery, ['returnScalar' => 'id'])->getContent();
    }

    /**
     * Get the corresponding entity class of a resource.
     *
     * @param string $resourceType
     * @return string
     */
    protected function getResourceEntityClass($resourceType)
    {
        switch ($resourceType) {
            case 'media':
                return 'Omeka\Entity\Media';
            case 'item_sets':
                return 'Omeka\Entity\ItemSet';
            case 'items':
            default:
                return 'Omeka\Entity\Item';
        }
    }
}
