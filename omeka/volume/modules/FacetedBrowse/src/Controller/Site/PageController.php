<?php
namespace FacetedBrowse\Controller\Site;

use Laminas\Mvc\Controller\AbstractActionController;
use Laminas\View\Model\ViewModel;

class PageController extends AbstractActionController
{
    /**
     * Re-sort and paginate items by size (height × width) using direct SQL.
     *
     * Gets all filtered item IDs via the API, then sorts them by computed area
     * in SQL, applies pagination, and fetches the final page of items.
     *
     * @param array $query The API search query params
     * @param string $resourceType The resource type (e.g. 'items')
     * @param string $sortOrder 'asc' or 'desc'
     * @param int $page Current page number
     * @param int $perPage Items per page
     * @return array [items, totalResults]
     */
    protected function searchSortedBySize(array $query, string $resourceType, string $sortOrder, int $page, int $perPage): array
    {
        // Get all matching item IDs (no pagination, no sort)
        $query['sort_by'] = 'id';
        $allIds = $this->api()
            ->search($resourceType, $query, ['returnScalar' => 'id'])
            ->getContent();
        $totalResults = count($allIds);

        if (!$allIds) {
            return [[], 0];
        }

        // Sort by height × width using direct SQL
        $conn = $this->getEvent()->getApplication()
            ->getServiceManager()->get('Omeka\Connection');
        $placeholders = implode(',', array_fill(0, count($allIds), '?'));
        $dir = strtoupper($sortOrder) === 'ASC' ? 'ASC' : 'DESC';
        // Items without dimensions sort last
        $nullSort = $dir === 'DESC' ? 1 : 0;
        $offset = ($page - 1) * $perPage;

        $sql = "SELECT r.id
                FROM resource r
                LEFT JOIN value h ON h.resource_id = r.id AND h.property_id = 603
                LEFT JOIN value w ON w.resource_id = r.id AND w.property_id = 1129
                WHERE r.id IN ($placeholders)
                GROUP BY r.id
                ORDER BY (CASE WHEN h.value IS NULL OR w.value IS NULL THEN $nullSort ELSE 1-$nullSort END),
                         (MAX(h.value) * MAX(w.value)) $dir,
                         r.id DESC
                LIMIT $perPage OFFSET $offset";

        $stmt = $conn->executeQuery($sql, array_values($allIds));
        $sortedIds = $stmt->fetchFirstColumn();

        if (!$sortedIds) {
            return [[], $totalResults];
        }

        // Fetch the actual items in the sorted order
        $items = [];
        $response = $this->api()->search($resourceType, [
            'id' => $sortedIds,
            'sort_by' => 'id',
            'site_id' => $query['site_id'] ?? null,
        ]);
        $itemsById = [];
        foreach ($response->getContent() as $item) {
            $itemsById[$item->id()] = $item;
        }
        foreach ($sortedIds as $id) {
            if (isset($itemsById[$id])) {
                $items[] = $itemsById[$id];
            }
        }

        return [$items, $totalResults];
    }

    public function pageAction()
    {
        $pageId = $this->params('page-id');
        $page = $this->api()->read('faceted_browse_pages', $pageId)->getContent();

        $categories = $page->categories();
        $categoryId = $this->params()->fromQuery('faceted_browse_category_id');

        // Auto-select sole category
        $category = null;
        if ($categoryId) {
            $category = $this->api()->read('faceted_browse_categories', $categoryId)->getContent();
        } elseif (1 === count($categories)) {
            $category = current($categories);
            $categoryId = $category->id();
        }

        $facetCounts = [];
        $items = [];
        $sortings = [];
        $columns = null;
        $query = [];

        if ($category) {
            $siteId = $this->currentSite()->id();
            $columns = $category->columns();

            // Filter params from URL (strip non-filter keys)
            $filterParams = $this->params()->fromQuery();
            unset($filterParams['faceted_browse_category_id']);
            unset($filterParams['page']);
            unset($filterParams['per_page']);
            unset($filterParams['sort_by']);
            unset($filterParams['sort_order']);

            // Facet counts (optimized GROUP BY queries)
            $facetCounts = $this->facetedBrowse()->computeFacetCounts(
                $category, $filterParams, $siteId
            );

            // Browse results (same logic as browseAction)
            $browseDefaults = $this->siteSettings()->get('browse_defaults_public_items');
            $sortBy = $browseDefaults['sort_by'] ?? 'created';
            $sortByValueOptions = $this->facetedBrowse()->getSortByValueOptions($category);
            $sortBy = array_key_exists($category->sortBy(), $sortByValueOptions)
                ? $category->sortBy()
                : $sortBy;
            $sortOrder = in_array($category->sortOrder(), ['desc', 'asc'])
                ? $category->sortOrder()
                : ($browseDefaults['sort_order'] ?? 'desc');
            $this->setBrowseDefaults($sortBy, $sortOrder);

            $categoryResourceIds = null;
            parse_str($category->query() ?? '', $categoryQuery);
            if ($categoryQuery) {
                $categoryResourceIds = $this->api()
                    ->search($page->resourceType(), $categoryQuery, ['returnScalar' => 'id'])
                    ->getContent();
            }

            $query = array_merge(
                $this->params()->fromQuery(),
                ['id' => $categoryResourceIds],
                ['site_id' => $siteId]
            );

            $activeSortBy = $this->params()->fromQuery('sort_by', $sortBy);
            if ($activeSortBy === 'size') {
                $activeSortOrder = $this->params()->fromQuery('sort_order', $sortOrder);
                $currentPage = (int) $this->params()->fromQuery('page', 1);
                $perPage = (int) ($this->params()->fromQuery('per_page') ?: $this->settings()->get('pagination_per_page', 25));
                [$items, $totalResults] = $this->searchSortedBySize(
                    $query, $page->resourceType(), $activeSortOrder, $currentPage, $perPage
                );
                $this->paginator($totalResults, $currentPage);
            } else {
                $response = $this->api()->search($page->resourceType(), $query);
                $totalResults = $response->getTotalResults();
                $this->paginator($totalResults, $this->params()->fromQuery('page'));
                $items = $response->getContent();
            }

            $sortings = $this->facetedBrowse()->getSortings($category);
        }

        // Parse active filter state from URL params for server-side selection
        $activeFilters = [
            'resource_class_id' => (array)($this->params()->fromQuery('resource_class_id', [])),
            'item_set_id' => (array)($this->params()->fromQuery('item_set_id', [])),
            'resource_template_id' => (array)($this->params()->fromQuery('resource_template_id', [])),
            'property' => $this->params()->fromQuery('property', []),
            'fulltext_search' => $this->params()->fromQuery('fulltext_search', ''),
        ];

        $view = new ViewModel;
        $view->setVariable('page', $page);
        $view->setVariable('categories', $categories);
        $view->setVariable('category', $category);
        $view->setVariable('categoryId', $categoryId);
        $view->setVariable('facetCounts', $facetCounts);
        $view->setVariable('items', $items);
        $view->setVariable('sortings', $sortings);
        $view->setVariable('columns', $columns);
        $view->setVariable('query', $query);
        $view->setVariable('activeFilters', $activeFilters);
        $view->setVariable('totalResults', $totalResults ?? 0);
        return $view;
    }

    public function categoriesAction()
    {
        $pageId = $this->params('page-id');
        $page = $this->api()->read('faceted_browse_pages', $pageId)->getContent();

        $view = new ViewModel;
        $view->setTerminal(true);
        $view->setVariable('page', $page);
        return $view;
    }

    public function facetsAction()
    {
        $categoryId = $this->params()->fromQuery('category_id');
        $category = $this->api()->read('faceted_browse_categories', $categoryId)->getContent();

        $view = new ViewModel;
        $view->setTerminal(true);
        $view->setVariable('category', $category);
        return $view;
    }

    public function browseAction()
    {
        $pageId = $this->params('page-id');
        $page = $this->api()->read('faceted_browse_pages', $pageId)->getContent();

        $categoryId = $this->params()->fromQuery('faceted_browse_category_id');
        $category = $categoryId ? $this->api()->read('faceted_browse_categories', $categoryId)->getContent() : null;

        $columns = $category ? $category->columns() : null;

        // Set default sort.
        $browseDefaults = $this->siteSettings()->get('browse_defaults_public_items');
        $sortBy = $browseDefaults['sort_by'] ?? 'created';
        if ($category) {
            $sortByValueOptions = $this->facetedBrowse()->getSortByValueOptions($category);
            $sortBy = array_key_exists($category->sortBy(), $sortByValueOptions)
                ? $category->sortBy()
                : $sortBy;
        }
        $sortOrder = $browseDefaults['sort_order'] ?? 'desc';
        if ($category) {
            $sortOrder = in_array($category->sortOrder(), ['desc', 'asc'])
                ? $category->sortOrder()
                : $sortOrder;
        }
        $this->setBrowseDefaults($sortBy, $sortOrder);

        $categoryResourceIds = null;
        if ($category) {
            parse_str($category->query(), $categoryQuery);
            if ($categoryQuery) {
                // If a category query is set, get the IDs of all resources in this
                // category, and include them in the facets query below. This ensures
                // that the result of the facets query only includes resources that
                // are part of the category query. We do this only when the category
                // query is set to avoid the overhead of an additional query when
                // it's not needed. In that case, the "category" is all the resources
                // assigned to the site.
                $categoryResourceIds = $this->api()
                    ->search($page->resourceType(), $categoryQuery, ['returnScalar' => 'id'])
                    ->getContent();
            }
        }

        // Get the resources from the facets query (only those within this category).
        $query = array_merge(
            $this->params()->fromQuery(),
            ['id' => $categoryResourceIds],
            ['site_id' => $this->currentSite()->id()]
        );
        $response = $this->api()->search($page->resourceType(), $query);
        $this->paginator($response->getTotalResults(), $this->params()->fromQuery('page'));
        $items = $response->getContent();

        $view = new ViewModel;
        $view->setTerminal(true);
        $view->setVariable('items', $items);
        $view->setVariable('query', $query);
        $view->setVariable('columns', $columns);
        $view->setVariable('sortings', $this->facetedBrowse()->getSortings($category));
        $view->setVariable('page', $page);
        return $view;
    }
}
