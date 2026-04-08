<?php declare(strict_types=1);

namespace IconographicProfile\Service\Controller;

use IconographicProfile\Controller\MentionsController;
use Laminas\ServiceManager\Factory\FactoryInterface;
use Psr\Container\ContainerInterface;

class MentionsControllerFactory implements FactoryInterface
{
    public function __invoke(ContainerInterface $container, $requestedName, array $options = null)
    {
        $connection = $container->get('Omeka\Connection');
        return new MentionsController($connection);
    }
}
