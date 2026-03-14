<?php declare(strict_types=1);

namespace EnrichItem;

use Laminas\Router\Http\Segment;

return [
    'router' => [
        'routes' => [
            'admin' => [
                'child_routes' => [
                    'enrich-item' => [
                        'type' => Segment::class,
                        'options' => [
                            'route' => '/enrich/:item_id',
                            'constraints' => [
                                'item_id' => '\\d+',
                            ],
                            'defaults' => [
                                '__NAMESPACE__' => 'EnrichItem\\Controller',
                                'controller' => Controller\EnrichController::class,
                                'action' => 'analyze',
                            ],
                        ],
                        'may_terminate' => true,
                        'child_routes' => [
                            'apply' => [
                                'type' => 'Literal',
                                'options' => [
                                    'route' => '/apply',
                                    'defaults' => [
                                        'action' => 'apply',
                                    ],
                                ],
                            ],
                            'ingest' => [
                                'type' => 'Literal',
                                'options' => [
                                    'route' => '/ingest',
                                    'defaults' => [
                                        'action' => 'ingest',
                                    ],
                                ],
                            ],
                        ],
                    ],
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
                            'run' => [
                                'type' => 'Literal',
                                'options' => [
                                    'route' => '/run',
                                    'defaults' => [
                                        'action' => 'runBatch',
                                    ],
                                ],
                            ],
                            'apply-cache' => [
                                'type' => 'Literal',
                                'options' => [
                                    'route' => '/apply-cache',
                                    'defaults' => [
                                        'action' => 'applyCache',
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
                'label' => 'Enrich Queue',
                'route' => 'admin/enrich-queue',
                'resource' => Controller\EnrichController::class,
            ],
        ],
    ],
];
