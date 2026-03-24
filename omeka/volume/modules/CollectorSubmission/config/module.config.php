<?php declare(strict_types=1);

namespace CollectorSubmission;

return [
    'router' => [
        'routes' => [
            'site' => [
                'child_routes' => [
                    'collector-submit' => [
                        'type' => 'Literal',
                        'options' => [
                            'route' => '/submit',
                            'defaults' => [
                                '__NAMESPACE__' => 'CollectorSubmission\\Controller',
                                'controller' => Controller\SubmitController::class,
                                'action' => 'index',
                            ],
                        ],
                    ],
                ],
            ],
            'admin' => [
                'child_routes' => [
                    'collector-submissions' => [
                        'type' => 'Literal',
                        'options' => [
                            'route' => '/collector-submissions',
                            'defaults' => [
                                '__NAMESPACE__' => 'CollectorSubmission\\Controller\\Admin',
                                'controller' => Controller\Admin\SubmissionController::class,
                                'action' => 'index',
                            ],
                        ],
                        'may_terminate' => true,
                        'child_routes' => [
                            'show' => [
                                'type' => 'Segment',
                                'options' => [
                                    'route' => '/:id',
                                    'constraints' => ['id' => '\\d+'],
                                    'defaults' => ['action' => 'show'],
                                ],
                            ],
                            'status' => [
                                'type' => 'Segment',
                                'options' => [
                                    'route' => '/:id/status',
                                    'constraints' => ['id' => '\\d+'],
                                    'defaults' => ['action' => 'status'],
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
            Controller\SubmitController::class => Service\Controller\SubmitControllerFactory::class,
            Controller\Admin\SubmissionController::class => Service\Controller\SubmissionControllerFactory::class,
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
                'label' => 'Collector Submissions',
                'route' => 'admin/collector-submissions',
                'resource' => Controller\Admin\SubmissionController::class,
            ],
        ],
    ],
    'collector_submission' => [
        'admin_email' => 'mark@mrh.io',
        'max_files' => 20,
        'max_file_size' => 15 * 1024 * 1024,
    ],
];
