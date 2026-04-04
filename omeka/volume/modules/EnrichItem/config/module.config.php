<?php declare(strict_types=1);

namespace EnrichItem;

return [
    'router' => [
        'routes' => [
            'admin' => [
                'child_routes' => [
                    'enrich-queue' => [
                        'type' => 'Literal',
                        'options' => [
                            'route' => '/enrich-queue',
                            'defaults' => [
                                '__NAMESPACE__' => 'EnrichItem\\Controller',
                                'controller' => Controller\EnrichController::class,
                                'action' => 'queue',
                            ],
                        ],
                        'may_terminate' => true,
                        'child_routes' => [
                            'fields' => [
                                'type' => 'Literal',
                                'options' => [
                                    'route' => '/fields',
                                    'defaults' => [
                                        'action' => 'fields',
                                    ],
                                ],
                            ],
                            'save' => [
                                'type' => 'Literal',
                                'options' => [
                                    'route' => '/save',
                                    'defaults' => [
                                        'action' => 'saveInstructions',
                                    ],
                                ],
                            ],
                            'run' => [
                                'type' => 'Literal',
                                'options' => [
                                    'route' => '/run',
                                    'defaults' => [
                                        'action' => 'runBatch',
                                    ],
                                ],
                            ],
                            'preview' => [
                                'type' => 'Literal',
                                'options' => [
                                    'route' => '/preview',
                                    'defaults' => [
                                        'action' => 'preview',
                                    ],
                                ],
                            ],
                            'batch' => [
                                'type' => 'Literal',
                                'options' => [
                                    'route' => '/batch',
                                    'defaults' => [
                                        'action' => 'batchSubmit',
                                    ],
                                ],
                            ],
                            'batch-status' => [
                                'type' => 'Literal',
                                'options' => [
                                    'route' => '/batch-status',
                                    'defaults' => [
                                        'action' => 'batchStatus',
                                    ],
                                ],
                            ],
                            'batch-collect' => [
                                'type' => 'Literal',
                                'options' => [
                                    'route' => '/batch-collect',
                                    'defaults' => [
                                        'action' => 'batchCollect',
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
                        ],
                    ],
                ],
            ],
        ],
    ],
    'service_manager' => [
        'factories' => [
            Service\AnthropicClient::class => Service\AnthropicClientFactory::class,
            Service\EnrichmentCache::class => Service\EnrichmentCacheFactory::class,
            Service\FieldInstructions::class => Service\FieldInstructionsFactory::class,
        ],
    ],
    'controllers' => [
        'factories' => [
            Controller\EnrichController::class => Service\Controller\EnrichControllerFactory::class,
        ],
    ],
    'view_manager' => [
        'template_path_stack' => [
            __DIR__ . '/../view',
        ],
    ],
    'enrich_item' => [
        'clip_api_base_url' => 'http://clip-api:8000',
        'timeout' => 120,
        'resource_template_id' => 2,
        'default_model' => 'haiku',
    ],
    'navigation' => [
        'AdminModule' => [
            [
                'label' => 'Enrich Fields',
                'route' => 'admin/enrich-queue',
                'resource' => Controller\EnrichController::class,
            ],
        ],
    ],
];
