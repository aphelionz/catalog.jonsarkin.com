<?php declare(strict_types=1);

namespace SiteLockdown;

use Laminas\Router\Http\Literal;

return [
    'router' => [
        'routes' => [
            'robots-txt' => [
                'type' => Literal::class,
                'options' => [
                    'route' => '/robots.txt',
                    'defaults' => [
                        'controller' => Controller\RobotsController::class,
                        'action' => 'index',
                    ],
                ],
            ],
        ],
    ],
    'controllers' => [
        'invokables' => [
            Controller\RobotsController::class => Controller\RobotsController::class,
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
];
