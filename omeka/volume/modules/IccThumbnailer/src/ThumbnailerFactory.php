<?php
namespace IccThumbnailer;

use Interop\Container\ContainerInterface;
use Laminas\ServiceManager\Factory\FactoryInterface;

class ThumbnailerFactory implements FactoryInterface
{
    public function __invoke(ContainerInterface $services, $requestedName, ?array $options = null)
    {
        return new Thumbnailer(
            $services->get('Omeka\Cli'),
            $services->get('Omeka\File\TempFileFactory')
        );
    }
}
