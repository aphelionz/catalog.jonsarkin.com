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
            'prelaunch-signup' => [
                'type' => Literal::class,
                'options' => [
                    'route' => '/prelaunch-signup',
                    'defaults' => [
                        'controller' => Controller\SubscribeController::class,
                        'action' => 'index',
                    ],
                ],
            ],
            'sitemap-xml' => [
                'type' => Literal::class,
                'options' => [
                    'route' => '/sitemap.xml',
                    'defaults' => [
                        'controller' => Controller\SitemapController::class,
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
        'factories' => [
            Controller\SubscribeController::class => Service\Controller\SubscribeControllerFactory::class,
            Controller\SitemapController::class => Service\Controller\SitemapControllerFactory::class,
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
