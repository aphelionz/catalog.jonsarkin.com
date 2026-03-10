<?php declare(strict_types=1);

namespace SimpleUpload;

use Laminas\Router\Http\Literal;

return [
    'router' => [
        'routes' => [
            'admin' => [
                'child_routes' => [
                    'simple-upload' => [
                        'type' => Literal::class,
                        'options' => [
                            'route' => '/simple-upload',
                            'defaults' => [
                                '__NAMESPACE__' => 'SimpleUpload\\Controller',
                                'controller' => Controller\IndexController::class,
                                'action' => 'index',
                            ],
                        ],
                    ],
                ],
            ],
        ],
    ],
    'controllers' => [
        'factories' => [
            Controller\IndexController::class => Service\Controller\IndexControllerFactory::class,
        ],
    ],
    'view_manager' => [
        'template_path_stack' => [
            __DIR__ . '/../view',
        ],
    ],
    'navigation' => [
        'AdminModule' => [
            [
                'label' => 'Simple Upload',
                'route' => 'admin/simple-upload',
                'resource' => Controller\IndexController::class,
                'privilege' => 'index',
                'class' => 'o-icon-install',
            ],
        ],
    ],
];
