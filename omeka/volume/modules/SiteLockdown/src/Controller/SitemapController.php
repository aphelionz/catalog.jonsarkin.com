<?php declare(strict_types=1);

namespace SiteLockdown\Controller;

use Doctrine\DBAL\Connection;
use Laminas\Mvc\Controller\AbstractActionController;

/**
 * /sitemap.xml — XML sitemap for search engine crawlers.
 *
 * Includes all public artworks, exhibitions, site pages, and the
 * well-known top-level routes (homepage, browse, search, submit).
 *
 * URLs use the clean form (no /s/catalog/ prefix) so they match what
 * users see and what gets indexed.
 *
 * Cached at edge for an hour; regenerated each origin request.
 */
class SitemapController extends AbstractActionController
{
    private const BASE_URL = 'https://catalog.jonsarkin.com';

    private Connection $conn;

    public function __construct(Connection $conn)
    {
        $this->conn = $conn;
    }

    public function indexAction()
    {
        $today = date('Y-m-d');
        $urls = [];

        // ── Top-level routes ──────────────────────────────────────────
        $urls[] = $this->urlEntry('/',                      $today, 'weekly',  '1.0');
        $urls[] = $this->urlEntry('/faceted-browse/2',      $today, 'daily',   '0.9');
        $urls[] = $this->urlEntry('/exhibitions',           $today, 'monthly', '0.8');
        $urls[] = $this->urlEntry('/visual-search',         $today, 'monthly', '0.5');
        $urls[] = $this->urlEntry('/submit',                $today, 'yearly',  '0.5');
        $urls[] = $this->urlEntry('/search',                $today, 'monthly', '0.5');

        // ── Public site pages ─────────────────────────────────────────
        $pages = $this->conn->executeQuery(
            "SELECT slug, modified FROM site_page WHERE site_id = (SELECT id FROM site WHERE slug = 'catalog') AND is_public = 1"
        )->fetchAllAssociative();
        foreach ($pages as $p) {
            $urls[] = $this->urlEntry(
                '/page/' . $p['slug'],
                $this->lastmodOf($p['modified']),
                'monthly',
                '0.7'
            );
        }

        // ── Public artwork items (template 2) ─────────────────────────
        $artworks = $this->conn->executeQuery(
            'SELECT id, modified FROM resource WHERE resource_template_id = 2 AND is_public = 1'
        )->fetchAllAssociative();
        foreach ($artworks as $a) {
            $urls[] = $this->urlEntry(
                '/item/' . (int) $a['id'],
                $this->lastmodOf($a['modified']),
                'monthly',
                '0.6'
            );
        }

        // ── Public exhibition items (template 4) ──────────────────────
        $exhibitions = $this->conn->executeQuery(
            'SELECT id, modified FROM resource WHERE resource_template_id = 4 AND is_public = 1'
        )->fetchAllAssociative();
        foreach ($exhibitions as $e) {
            $urls[] = $this->urlEntry(
                '/item/' . (int) $e['id'],
                $this->lastmodOf($e['modified']),
                'monthly',
                '0.7'
            );
        }

        $body = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
              . "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n"
              . implode('', $urls)
              . "</urlset>\n";

        $response = $this->getResponse();
        $headers  = $response->getHeaders();
        $headers->addHeaderLine('Content-Type', 'application/xml; charset=UTF-8');
        $headers->addHeaderLine('Cache-Control', 'public, max-age=3600');
        $response->setContent($body);
        return $response;
    }

    private function urlEntry(string $path, string $lastmod, string $changefreq, string $priority): string
    {
        $loc = htmlspecialchars(self::BASE_URL . $path, ENT_XML1, 'UTF-8');
        return "  <url>\n"
             . "    <loc>{$loc}</loc>\n"
             . "    <lastmod>{$lastmod}</lastmod>\n"
             . "    <changefreq>{$changefreq}</changefreq>\n"
             . "    <priority>{$priority}</priority>\n"
             . "  </url>\n";
    }

    private function lastmodOf(?string $modifiedDt): string
    {
        if (!$modifiedDt) {
            return date('Y-m-d');
        }
        // resource.modified is "YYYY-MM-DD HH:MM:SS"
        return substr($modifiedDt, 0, 10);
    }
}
