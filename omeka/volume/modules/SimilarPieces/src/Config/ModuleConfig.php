<?php declare(strict_types=1);

namespace SimilarPieces\Config;

use Psr\Container\ContainerInterface;

class ModuleConfig
{
    public static function resolve(ContainerInterface $container): array
    {
        $config = $container->get('Config');
        $moduleConfig = $config['similar_pieces'] ?? [];

        $baseUrl = $moduleConfig['base_url'] ?? 'https://similar.jonsarkin.com';
        $debug = $moduleConfig['debug'] ?? false;
        $enableSearchUi = $moduleConfig['enable_search_ui'] ?? false;

        if ($container->has('Omeka\\Settings')) {
            $settings = $container->get('Omeka\\Settings');

            $storedBaseUrl = $settings->get('similar_pieces_base_url', null);
            if (is_string($storedBaseUrl) && trim($storedBaseUrl) !== '') {
                $baseUrl = $storedBaseUrl;
            }

            $storedDebug = $settings->get('similar_pieces_debug', null);
            if ($storedDebug !== null) {
                $debug = (bool) $storedDebug;
            }

            $storedSearchUi = $settings->get('similar_pieces_enable_search_ui', null);
            if ($storedSearchUi !== null) {
                $enableSearchUi = (bool) $storedSearchUi;
            }
        }

        $moduleConfig['base_url'] = self::normalizeBaseUrl((string) $baseUrl, 'https://similar.jonsarkin.com');
        $moduleConfig['debug'] = (bool) $debug;
        $moduleConfig['enable_search_ui'] = (bool) $enableSearchUi;

        return $moduleConfig;
    }

    public static function normalizeBaseUrl(string $baseUrl, string $fallback): string
    {
        $baseUrl = trim($baseUrl);
        if ($baseUrl === '') {
            $baseUrl = $fallback;
        }

        if ($baseUrl !== '' && !preg_match('~^https?://~i', $baseUrl)) {
            $baseUrl = 'https://' . $baseUrl;
        }

        return rtrim($baseUrl, '/');
    }
}
