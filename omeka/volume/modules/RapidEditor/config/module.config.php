<?php declare(strict_types=1);

namespace RapidEditor;

use Laminas\Router\Http\Literal;
use Laminas\Router\Http\Segment;
use RapidEditor\Service\Controller\EditorControllerFactory;

return [
    'router' => [
        'routes' => [
            'admin' => [
                'child_routes' => [
                    'rapid-editor' => [
                        'type' => Literal::class,
                        'options' => [
                            'route' => '/rapid-editor',
                            'defaults' => [
                                '__NAMESPACE__' => 'RapidEditor\\Controller',
                                'controller' => Controller\EditorController::class,
                                'action' => 'index',
                            ],
                        ],
                        'may_terminate' => true,
                        'child_routes' => [
                            'data' => [
                                'type' => Literal::class,
                                'options' => [
                                    'route' => '/data',
                                    'defaults' => ['action' => 'data'],
                                ],
                            ],
                            'patch' => [
                                'type' => Segment::class,
                                'options' => [
                                    'route' => '/patch/:id',
                                    'constraints' => ['id' => '\d+'],
                                    'defaults' => ['action' => 'patch'],
                                ],
                            ],
                        ],
                    ],
                ],
            ],
        ],
    ],
    'controllers' => [
        'factories' => [
            Controller\EditorController::class => EditorControllerFactory::class,
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
                'label' => 'Rapid Editor',
                'route' => 'admin/rapid-editor',
                'resource' => Controller\EditorController::class,
            ],
        ],
    ],
];
