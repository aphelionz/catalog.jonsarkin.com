<?php declare(strict_types=1);

namespace MotifTagger;

return [
    'router' => [
        'routes' => [
            'admin' => [
                'child_routes' => [
                    'motif-tagger' => [
                        'type' => 'Literal',
                        'options' => [
                            'route' => '/motif-tagger',
                            'defaults' => [
                                '__NAMESPACE__' => 'MotifTagger\\Controller',
                                'controller' => Controller\MotifTaggerController::class,
                                'action' => 'index',
                            ],
                        ],
                        'may_terminate' => true,
                        'child_routes' => [
                            'search' => [
                                'type' => 'Literal',
                                'options' => [
                                    'route' => '/search',
                                    'defaults' => [
                                        'action' => 'search',
                                    ],
                                ],
                            ],
                            'tag' => [
                                'type' => 'Literal',
                                'options' => [
                                    'route' => '/tag',
                                    'defaults' => [
                                        'action' => 'tag',
                                    ],
                                ],
                            ],
                            'add-term' => [
                                'type' => 'Literal',
                                'options' => [
                                    'route' => '/add-term',
                                    'defaults' => [
                                        'action' => 'addTerm',
                                    ],
                                ],
                            ],
                            'ingest-clip' => [
                                'type' => 'Literal',
                                'options' => [
                                    'route' => '/ingest-clip',
                                    'defaults' => [
                                        'action' => 'ingestClip',
                                    ],
                                ],
                            ],
                            'ingest-dino' => [
                                'type' => 'Literal',
                                'options' => [
                                    'route' => '/ingest-dino',
                                    'defaults' => [
                                        'action' => 'ingestDino',
                                    ],
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
            Controller\MotifTaggerController::class => Service\Controller\MotifTaggerControllerFactory::class,
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
    'motif_tagger' => [
        'clip_api_url' => 'http://clip-api:8000',
        'qdrant_url' => 'http://qdrant:6333',
        'default_limit' => 100,
        'default_threshold' => 0.5,
        'motif_property_id' => 3,
        'motif_vocab_label' => 'Motifs',
    ],
    'navigation' => [
        'AdminModule' => [
            [
                'label' => 'Motif Tagger',
                'route' => 'admin/motif-tagger',
                'resource' => Controller\MotifTaggerController::class,
            ],
        ],
    ],
];
