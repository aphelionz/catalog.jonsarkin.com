<?php
/**
 * Regenerate thumbnails for all media using the active thumbnailer.
 *
 * Usage: docker compose exec -T omeka php /var/www/html/scripts/regenerate-thumbnails.php
 *
 * This re-creates large, medium, and square derivatives from the original files
 * using whatever thumbnailer is configured (IccThumbnailer preserves ICC profiles).
 */

require '/var/www/html/bootstrap.php';
$app = \Omeka\Mvc\Application::init(require '/var/www/html/application/config/application.config.php');
$services = $app->getServiceManager();
$em = $services->get('Omeka\EntityManager');
$store = $services->get('Omeka\File\Store');
$thumbnailer = $services->get('Omeka\File\Thumbnailer');
$tempFileFactory = $services->get('Omeka\File\TempFileFactory');

// Get thumbnail config
$config = $services->get('Config');
$thumbTypes = $config['thumbnails']['types'];
$thumbnailerOptions = $config['thumbnails']['thumbnailer_options'] ?? [];
$thumbnailer->setOptions($thumbnailerOptions);

$query = $em->createQuery(
    'SELECT m FROM Omeka\Entity\Media m WHERE m.hasOriginal = true AND m.mediaType LIKE :type ORDER BY m.id ASC'
);
$query->setParameter('type', 'image/%');

$media = $query->getResult();
$total = count($media);
$done = 0;
$errors = 0;

fprintf(STDERR, "Regenerating thumbnails for %d media items...\n", $total);

foreach ($media as $m) {
    $id = $m->getId();
    $filename = $m->getFilename();
    $storagePath = sprintf('original/%s', $filename);

    // Download original to temp file
    $localPath = $store->getLocalPath($storagePath);
    if (!file_exists($localPath)) {
        fprintf(STDERR, "  [%d] SKIP - original not found: %s\n", $id, $filename);
        $errors++;
        $done++;
        continue;
    }

    // Create a TempFile from the original
    $tempFile = $tempFileFactory->build();
    copy($localPath, $tempFile->getTempPath());

    $thumbnailer->setSource($tempFile);

    $ok = true;
    foreach ($thumbTypes as $type => $typeConfig) {
        try {
            $thumbPath = $thumbnailer->create(
                $typeConfig['strategy'],
                $typeConfig['constraint'],
                $typeConfig['options'] ?? []
            );
            // Move to storage
            $destPath = sprintf('%s/%s', $type, $filename);
            $destLocalPath = $store->getLocalPath($destPath);

            // Ensure directory exists
            $dir = dirname($destLocalPath);
            if (!is_dir($dir)) {
                mkdir($dir, 0755, true);
            }

            rename($thumbPath, $destLocalPath);
        } catch (\Exception $e) {
            fprintf(STDERR, "  [%d] ERROR on %s: %s\n", $id, $type, $e->getMessage());
            $ok = false;
            $errors++;
        }
    }

    $tempFile->delete();

    $done++;
    if ($done % 100 === 0 || $done === $total) {
        fprintf(STDERR, "  Progress: %d/%d (errors: %d)\n", $done, $total, $errors);
    }
}

fprintf(STDERR, "Done. %d/%d processed, %d errors.\n", $done, $total, $errors);
