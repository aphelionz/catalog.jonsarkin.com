<?php declare(strict_types=1);

namespace SimilarPieces;

use Laminas\Router\Http\Segment;

return [
    'router' => [
        'routes' => [
            'similar-pieces' => [
                'type' => Segment::class,
                'options' => [
                    'route' => '/similar/:item_id',
                    'constraints' => [
                        'item_id' => '\\d+',
                    ],
                    'defaults' => [
                        '__NAMESPACE__' => 'SimilarPieces\\Controller',
                        'controller' => Controller\SimilarController::class,
                        'action' => 'index',
                    ],
                ],
                'may_terminate' => true,
                'child_routes' => [
                    'json' => [
                        'type' => 'Literal',
                        'options' => [
                            'route' => '/json',
                            'defaults' => [
                                'action' => 'json',
                            ],
                        ],
                    ],
                ],
            ],
            'iconography' => [
                'type' => Segment::class,
                'options' => [
                    'route' => '/iconography/:item_id/json',
                    'constraints' => [
                        'item_id' => '\\d+',
                    ],
                    'defaults' => [
                        '__NAMESPACE__' => 'SimilarPieces\\Controller',
                        'controller' => Controller\SimilarController::class,
                        'action' => 'iconography',
                    ],
                ],
            ],
            'iconography-batch' => [
                'type' => 'Literal',
                'options' => [
                    'route' => '/iconography/batch/json',
                    'defaults' => [
                        '__NAMESPACE__' => 'SimilarPieces\\Controller',
                        'controller' => Controller\SimilarController::class,
                        'action' => 'iconographyBatch',
                    ],
                ],
            ],
            'lexical-profile' => [
                'type' => Segment::class,
                'options' => [
                    'route' => '/lexical-profile/:item_id/json',
                    'constraints' => [
                        'item_id' => '\\d+',
                    ],
                    'defaults' => [
                        '__NAMESPACE__' => 'SimilarPieces\\Controller',
                        'controller' => Controller\SimilarController::class,
                        'action' => 'lexicalProfile',
                    ],
                ],
            ],
            'similar-search' => [
                'type' => Segment::class,
                'options' => [
                    'route' => '/similar/search',
                    'defaults' => [
                        '__NAMESPACE__' => 'SimilarPieces\\Controller',
                        'controller' => Controller\SearchController::class,
                        'action' => 'index',
                    ],
                ],
            ],
        ],
    ],
    'controllers' => [
        'factories' => [
            Controller\SimilarController::class => Service\Controller\SimilarControllerFactory::class,
            Controller\SearchController::class => Service\Controller\SearchControllerFactory::class,
        ],
    ],
    'form_elements' => [
        'invokables' => [
            Form\ConfigForm::class => Form\ConfigForm::class,
        ],
    ],
    'view_manager' => [
        'template_path_stack' => [
            __DIR__ . '/../view',
        ],
    ],
    'similar_pieces' => [
        'base_url' => 'http://clip-api:8000',
        'timeout' => 3,
        'default_per_page' => 24,
        'enable_item_link' => true,
        'enable_search_ui' => false,
        'debug' => false,
    ],
];
