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
        ],
    ],
    'controllers' => [
        'invokables' => [
            Controller\RobotsController::class => Controller\RobotsController::class,
        ],
        'factories' => [
            Controller\SubscribeController::class => Service\Controller\SubscribeControllerFactory::class,
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
    'site_lockdown' => [
        'shopify_shop_domain' => getenv('SHOPIFY_SHOP_DOMAIN') ?: 'jonsarkin.myshopify.com',
        'shopify_admin_api_token' => getenv('SHOPIFY_ADMIN_API_TOKEN') ?: '',
        'shopify_api_version' => '2025-01',
        'signup_tag' => 'prelaunch-signup',
    ],
];
