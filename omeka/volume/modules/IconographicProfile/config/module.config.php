<?php declare(strict_types=1);

namespace IconographicProfile;

use Laminas\Router\Http\Segment;

return [
    'router' => [
        'routes' => [
            'iconography' => [
                'type' => Segment::class,
                'options' => [
                    'route' => '/iconography/:item_id/json',
                    'constraints' => [
                        'item_id' => '\\d+',
                    ],
                    'defaults' => [
                        '__NAMESPACE__' => 'IconographicProfile\\Controller',
                        'controller' => Controller\IconographyController::class,
                        'action' => 'item',
                    ],
                ],
            ],
            'iconography-batch' => [
                'type' => 'Literal',
                'options' => [
                    'route' => '/iconography/batch/json',
                    'defaults' => [
                        '__NAMESPACE__' => 'IconographicProfile\\Controller',
                        'controller' => Controller\IconographyController::class,
                        'action' => 'batch',
                    ],
                ],
            ],
        ],
    ],
    'controllers' => [
        'factories' => [
            Controller\IconographyController::class => Service\Controller\IconographyControllerFactory::class,
        ],
    ],
];
