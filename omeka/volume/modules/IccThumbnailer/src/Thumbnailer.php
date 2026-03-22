<?php
namespace IccThumbnailer;

use Omeka\File\Thumbnailer\ImageMagick;

/**
 * ImageMagick thumbnailer that preserves ICC color profiles and Apple HDR
 * gain maps.
 *
 * The core Omeka thumbnailer uses -thumbnail which strips all metadata
 * including ICC profiles, losing HDR/wide-gamut color data. This version
 * uses -resize instead, preserving the embedded color profile. For images
 * with an Apple HDR gain map (Display P3 + MPF-embedded grayscale map),
 * the gain map is extracted, resized proportionally, and re-embedded using
 * Apple's MPF format so browsers render with full HDR.
 */
class Thumbnailer extends ImageMagick
{
    public function create($strategy, $constraint, array $options = [])
    {
        $mediaType = $this->sourceFile->getMediaType();
        $origPath = sprintf('%s[%s]', $this->source, $this->getOption('page', 0));
        if (strpos($mediaType, 'video/') === 0) {
            $origPath = 'mp4:' . $origPath;
        }

        switch ($strategy) {
            case 'square':
                $gravity = $options['gravity'] ?? 'center';
                $args = [
                    '-background white',
                    '+repage',
                    '-alpha remove',
                    '-resize ' . escapeshellarg(sprintf('%sx%s^', $constraint, $constraint)),
                    '-gravity ' . escapeshellarg($gravity),
                    '-crop ' . escapeshellarg(sprintf('%sx%s+0+0', $constraint, $constraint)),
                ];
                break;
            case 'default':
            default:
                $args = [
                    '-background white',
                    '+repage',
                    '-alpha remove',
                    '-resize ' . escapeshellarg(sprintf('%sx%s>', $constraint, $constraint)),
                ];
        }

        if ($this->getOption('autoOrient', true)) {
            array_unshift($args, '-auto-orient');
        }

        $tempFile = $this->tempFileFactory->build();
        $tempPath = sprintf('%s.%s', $tempFile->getTempPath(), 'jpg');
        $tempFile->delete();

        $commandArgs = [$this->convertPath];
        if ($mediaType == 'application/pdf') {
            $commandArgs[] = '-density 150';
            if ($this->getOption('pdfUseCropBox', true)) {
                $commandArgs[] = '-define pdf:use-cropbox=true';
            }
        }
        $commandArgs[] = escapeshellarg($origPath);
        $commandArgs = array_merge($commandArgs, $args);
        $commandArgs[] = '-quality 92';
        $commandArgs[] = escapeshellarg($tempPath);

        $command = implode(' ', $commandArgs);
        $cli = $this->cli;
        $output = $cli->execute($command);
        if (false === $output) {
            throw new \Omeka\File\Exception\CannotCreateThumbnailException;
        }

        // Re-embed HDR gain map if the original has one
        if ($mediaType === 'image/jpeg') {
            $this->reembedGainMap($this->source, $tempPath);
        }

        return $tempPath;
    }

    /**
     * If the original JPEG has an Apple HDR gain map, extract it, resize it,
     * and re-embed it into the thumbnail using the hdr_reassemble.py script.
     */
    private function reembedGainMap(string $originalPath, string $thumbnailPath): void
    {
        // Quick check: does the original contain an MPF (Multi-Picture Format) marker?
        // The MPF header appears in the first 64KB and indicates a gain map is present.
        $header = file_get_contents($originalPath, false, null, 0, 65536);
        if (strpos($header, "MPF\x00") === false) {
            return;
        }

        $script = dirname(__DIR__) . '/src/hdr_reassemble.py';
        if (!file_exists($script)) {
            return;
        }

        $outputPath = $thumbnailPath . '.hdr.tmp.jpg';
        $cmd = sprintf(
            'python3 %s %s %s %s 2>&1',
            escapeshellarg($script),
            escapeshellarg($originalPath),
            escapeshellarg($thumbnailPath),
            escapeshellarg($outputPath)
        );

        $result = shell_exec($cmd);
        if ($result !== null && file_exists($outputPath) && filesize($outputPath) > 0) {
            rename($outputPath, $thumbnailPath);
        } else {
            // Clean up on failure — SDR thumbnail is still valid
            @unlink($outputPath);
        }
    }
}
