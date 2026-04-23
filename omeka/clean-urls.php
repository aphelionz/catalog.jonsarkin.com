<?php
/**
 * Clean URL support — loaded via auto_prepend_file before Omeka's index.php.
 *
 * Inbound:  rewrites REQUEST_URI so Omeka's router sees /s/catalog/...
 * Outbound: output buffer strips /s/catalog/ from HTML responses.
 */
$uri  = $_SERVER['REQUEST_URI'] ?? '';
$path = parse_url($uri, PHP_URL_PATH);

// Only rewrite known site routes; admin/api/files are untouched.
// Search needs the /index controller prefix: /search → /s/catalog/index/search
if (preg_match('#^/(search)(/|$|\?)#', $path)) {
    $_SERVER['REQUEST_URI'] = '/s/catalog/index' . $uri;
} elseif (preg_match('#^/(item|item-set|media|page|faceted-browse|submit|visual-search|asset|exhibitions)(/|$|\?)#', $path)) {
    $_SERVER['REQUEST_URI'] = '/s/catalog' . $uri;
}
// Root URL → serve the catalog site homepage directly
if ($path === '/' || $path === '') {
    $_SERVER['REQUEST_URI'] = '/s/catalog';
}

// Rewrite /s/catalog/ out of HTML responses so links use clean URLs.
// Skip admin and API pages — they use /s/catalog/ internally (e.g. /admin/site/s/catalog/).
$isAdmin = strpos($path, '/admin') === 0 || strpos($path, '/api') === 0;
if (!$isAdmin) {
    ob_start(function ($buffer) {
        // Skip non-HTML responses (JSON, assets, etc.)
        foreach (headers_list() as $header) {
            if (stripos($header, 'content-type:') === 0 && stripos($header, 'text/html') === false) {
                return $buffer;
            }
        }
        $buffer = str_replace('/s/catalog/', '/', $buffer);
        // Collapse /index/search → /search (router includes the controller name)
        $buffer = str_replace('/index/search', '/search', $buffer);
        // Handle HTML-entity-encoded slashes (Omeka's escapeHtml encodes / as &#x2F;)
        $buffer = str_replace('&#x2F;s&#x2F;catalog&#x2F;', '&#x2F;', $buffer);
        // Handle JSON-escaped slashes (inline JS variables from $this->url())
        $buffer = str_replace('\/s\/catalog\/', '\/', $buffer);
        // Handle homepage link: "/s/catalog" (no trailing slash) in href/action attributes
        $buffer = str_replace('"/s/catalog"', '"/"', $buffer);
        $buffer = str_replace("'/s/catalog'", "'/'", $buffer);
        return $buffer;
    }, 0, PHP_OUTPUT_HANDLER_REMOVABLE);
}
