<?php declare(strict_types=1);

namespace ShieldedRegistryAssets;

use Laminas\Router\Http\Literal;

return [
    'router' => [
        'routes' => [
            'admin' => [
                'child_routes' => [
                    'shielded-registry' => [
                        'type' => Literal::class,
                        'options' => [
                            'route' => '/shielded-registry',
                            'defaults' => [
                                '__NAMESPACE__' => 'ShieldedRegistryAssets\\Controller\\Admin',
                                'controller' => Controller\Admin\CeremonyController::class,
                                'action' => 'index',
                            ],
                        ],
                        'may_terminate' => true,
                        'child_routes' => [
                            'check-dns' => [
                                'type' => Literal::class,
                                'options' => [
                                    'route' => '/check-dns',
                                    'defaults' => [
                                        'action' => 'checkDns',
                                    ],
                                ],
                            ],
                        ],
                    ],
                ],
            ],
            'sra-well-known' => [
                'type' => Literal::class,
                'options' => [
                    'route' => '/.well-known/sra-authority.json',
                    'defaults' => [
                        '__NAMESPACE__' => 'ShieldedRegistryAssets\\Controller\\Admin',
                        'controller' => Controller\Admin\CeremonyController::class,
                        'action' => 'wellKnown',
                    ],
                ],
            ],
        ],
    ],
    'controllers' => [
        'factories' => [
            Controller\Admin\CeremonyController::class => Service\Controller\CeremonyControllerFactory::class,
        ],
    ],
    'form_elements' => [
        'invokables' => [
            Form\CeremonyConfigForm::class => Form\CeremonyConfigForm::class,
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
                'label' => 'Shielded Registry',
                'route' => 'admin/shielded-registry',
                'resource' => Controller\Admin\CeremonyController::class,
                'privilege' => 'index',
            ],
        ],
    ],
];
