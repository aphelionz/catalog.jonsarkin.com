<?php
namespace IccThumbnailer\Job;

use Omeka\Job\AbstractJob;

class RegenerateThumbnails extends AbstractJob
{
    public function perform()
    {
        $services = $this->getServiceLocator();
        $em = $services->get('Omeka\EntityManager');
        $store = $services->get('Omeka\File\Store');
        $thumbnailer = $services->get('Omeka\File\Thumbnailer');
        $tempFileFactory = $services->get('Omeka\File\TempFileFactory');
        $logger = $services->get('Omeka\Logger');

        $config = $services->get('Config');
        $thumbTypes = $config['thumbnails']['types'];
        $thumbnailer->setOptions($config['thumbnails']['thumbnailer_options'] ?? []);

        $query = $em->createQuery(
            'SELECT m FROM Omeka\Entity\Media m WHERE m.hasOriginal = true AND m.mediaType LIKE :type ORDER BY m.id ASC'
        );
        $query->setParameter('type', 'image/%');

        $media = $query->getResult();
        $total = count($media);
        $done = 0;
        $errors = 0;

        $logger->info(sprintf('IccThumbnailer: regenerating thumbnails for %d media items', $total));

        foreach ($media as $m) {
            if ($this->shouldStop()) {
                $logger->info(sprintf('IccThumbnailer: stopped at %d/%d', $done, $total));
                return;
            }

            $id = $m->getId();
            $filename = $m->getFilename();
            // Omeka hardcodes 'jpg' for all thumbnail URLs (MediaRepresentation::thumbnailUrl)
            $thumbFilename = $m->getStorageId() . '.jpg';
            $localPath = $store->getLocalPath(sprintf('original/%s', $filename));

            if (!file_exists($localPath)) {
                $logger->warn(sprintf('IccThumbnailer: [%d] original not found: %s', $id, $filename));
                $errors++;
                $done++;
                continue;
            }

            $tempFile = $tempFileFactory->build();
            copy($localPath, $tempFile->getTempPath());
            $thumbnailer->setSource($tempFile);

            foreach ($thumbTypes as $type => $typeConfig) {
                try {
                    $thumbPath = $thumbnailer->create(
                        $typeConfig['strategy'],
                        $typeConfig['constraint'],
                        $typeConfig['options'] ?? []
                    );
                    $destLocalPath = $store->getLocalPath(sprintf('%s/%s', $type, $thumbFilename));
                    $dir = dirname($destLocalPath);
                    if (!is_dir($dir)) {
                        mkdir($dir, 0755, true);
                    }
                    rename($thumbPath, $destLocalPath);
                } catch (\Exception $e) {
                    $logger->err(sprintf('IccThumbnailer: [%d] %s error: %s', $id, $type, $e->getMessage()));
                    $errors++;
                }
            }

            $tempFile->delete();
            $done++;

            if ($done % 100 === 0) {
                $logger->info(sprintf('IccThumbnailer: progress %d/%d (errors: %d)', $done, $total, $errors));
            }
        }

        $logger->info(sprintf('IccThumbnailer: done. %d/%d processed, %d errors', $done, $total, $errors));
    }
}
