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
                            'read' => [
                                'type' => Segment::class,
                                'options' => [
                                    'route' => '/read/:id',
                                    'constraints' => ['id' => '\d+'],
                                    'defaults' => ['action' => 'read'],
                                ],
                            ],
                            'media' => [
                                'type' => Segment::class,
                                'options' => [
                                    'route' => '/media/:id',
                                    'constraints' => ['id' => '\d+'],
                                    'defaults' => ['action' => 'media'],
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
                            'create-set' => [
                                'type' => Literal::class,
                                'options' => [
                                    'route' => '/create-set',
                                    'defaults' => ['action' => 'createSet'],
                                ],
                            ],
                            'add-to-set' => [
                                'type' => Literal::class,
                                'options' => [
                                    'route' => '/add-to-set',
                                    'defaults' => ['action' => 'addToSet'],
                                ],
                            ],
                            'tournament-seed' => [
                                'type' => Literal::class,
                                'options' => [
                                    'route' => '/tournament-seed',
                                    'defaults' => ['action' => 'tournamentSeed'],
                                ],
                            ],
                            'suggest-motifs' => [
                                'type' => Segment::class,
                                'options' => [
                                    'route' => '/suggest-motifs/:id',
                                    'constraints' => ['id' => '\d+'],
                                    'defaults' => ['action' => 'suggestMotifs'],
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
