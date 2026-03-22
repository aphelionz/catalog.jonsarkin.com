<?php
return [
    'service_manager' => [
        'factories' => [
            'IccThumbnailer\Thumbnailer' => IccThumbnailer\ThumbnailerFactory::class,
        ],
        'aliases' => [
            'Omeka\File\Thumbnailer' => 'IccThumbnailer\Thumbnailer',
        ],
    ],
    'controllers' => [
        'invokables' => [
            'IccThumbnailer\Controller\Admin\Index' => IccThumbnailer\Controller\Admin\IndexController::class,
        ],
    ],
    'navigation' => [
        'AdminModule' => [
            [
                'label' => 'ICC Thumbnailer',
                'route' => 'admin/icc-thumbnailer',
                'resource' => 'Omeka\Controller\Admin\Module',
            ],
        ],
    ],
    'router' => [
        'routes' => [
            'admin' => [
                'child_routes' => [
                    'icc-thumbnailer' => [
                        'type' => \Laminas\Router\Http\Literal::class,
                        'may_terminate' => true,
                        'options' => [
                            'route' => '/icc-thumbnailer',
                            'defaults' => [
                                '__NAMESPACE__' => 'IccThumbnailer\Controller\Admin',
                                'controller' => 'Index',
                                'action' => 'index',
                            ],
                        ],
                        'child_routes' => [
                            'regenerate' => [
                                'type' => \Laminas\Router\Http\Literal::class,
                                'options' => [
                                    'route' => '/regenerate',
                                    'defaults' => [
                                        'action' => 'regenerate',
                                    ],
                                ],
                            ],
                        ],
                    ],
                ],
            ],
        ],
    ],
    'view_manager' => [
        'template_path_stack' => [
            dirname(__DIR__) . '/view',
        ],
    ],
];
