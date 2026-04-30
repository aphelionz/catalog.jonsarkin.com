<?php declare(strict_types=1);

namespace Exhibitions;

return [
    'router' => [
        'routes' => [
            // Site-child route (consistent with CollectorSubmission/submit).
            // `exhibitions` is added to omeka/clean-urls.php whitelist so the
            // clean URL /exhibitions maps to /s/catalog/exhibitions before routing.
            'site' => [
                'child_routes' => [
                    'exhibitions' => [
                        'type' => \Laminas\Router\Http\Literal::class,
                        'options' => [
                            'route' => '/exhibitions',
                            'defaults' => [
                                '__NAMESPACE__' => 'Exhibitions\\Controller',
                                'controller' => Controller\ExhibitionsController::class,
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
            Controller\ExhibitionsController::class => Service\Controller\ExhibitionsControllerFactory::class,
        ],
    ],
    'view_manager' => [
        'template_path_stack' => [
            __DIR__ . '/../view',
        ],
    ],
    'exhibitions' => [
        // Resource template ID for Exhibition items
        'resource_template_id' => 4,
        // Omeka property IDs used on Exhibition items
        'property_id_title' => 1,
        'property_id_date' => 7,
        // dcterms:available — ISO YYYY-MM-DD start date used for sort
        'property_id_start_date' => 22,
        'property_id_type' => 8,
        'property_id_description' => 4,
        'property_id_venue' => 230,
        'property_id_organizer' => 1202,
        // bibo:presentedAt — artwork→exhibition reverse lookup property
        'property_id_presented_at' => 74,
    ],
];
