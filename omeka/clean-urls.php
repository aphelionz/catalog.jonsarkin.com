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
if (preg_match('#^/(item|item-set|media|page|search|faceted-browse|submit|asset)(/|$|\?)#', $path)) {
    $_SERVER['REQUEST_URI'] = '/s/catalog' . $uri;
}
// Root URL → serve the catalog site homepage directly
if ($path === '/' || $path === '') {
    $_SERVER['REQUEST_URI'] = '/s/catalog';
}

// Rewrite /s/catalog/ out of HTML responses so links use clean URLs.
// Only for text/html content (not JSON API responses, admin pages, etc.).
ob_start(function ($buffer) {
    // Skip non-HTML responses (API, admin assets, etc.)
    foreach (headers_list() as $header) {
        if (stripos($header, 'content-type:') === 0 && stripos($header, 'text/html') === false) {
            return $buffer;
        }
    }
    $buffer = str_replace('/s/catalog/', '/', $buffer);
    // Handle HTML-entity-encoded slashes (Omeka's escapeHtml encodes / as &#x2F;)
    $buffer = str_replace('&#x2F;s&#x2F;catalog&#x2F;', '&#x2F;', $buffer);
    // Handle homepage link: "/s/catalog" (no trailing slash) in href/action attributes
    $buffer = str_replace('"/s/catalog"', '"/"', $buffer);
    $buffer = str_replace("'/s/catalog'", "'/'", $buffer);
    return $buffer;
}, 0, PHP_OUTPUT_HANDLER_REMOVABLE);
