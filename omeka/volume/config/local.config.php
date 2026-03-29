<?php
return [
    // OK to leave null inside a container; Omeka can call php directly.
    'cli' => [
        'phpcli_path' => null,
    ],

    // Transactional email via Gmail SMTP (App Password auth).
    'mail' => [
        'transport' => [
            'type' => 'smtp',
            'options' => [
                'host' => 'smtp.gmail.com',
                'port' => 587,
                'connection_class' => 'login',
                'connection_config' => [
                    'username' => getenv('SMTP_USER') ?: 'mark@fishcitystudios.com',
                    'password' => getenv('SMTP_PASS') ?: '',
                    'ssl' => 'tls',
                ],
            ],
        ],
    ],

    // Persist uploads/derivatives in the bind-mounted volume.
    'file_store' => [
        'local' => [
            'base_path' => '/var/www/html/volume/files',
        ],
    ],

    // IccThumbnailer wraps ImageMagick to preserve ICC profiles + HDR gain maps.
    // Must register factory here because Omeka's module manager doesn't merge
    // third-party module service_manager configs into the global config.
    // The closure lazy-loads the class files since the module autoloader isn't
    // registered when local.config.php is first parsed.
    'service_manager' => [
        'factories' => [
            'IccThumbnailer\Thumbnailer' => function ($container) {
                $moduleDir = OMEKA_PATH . '/modules/IccThumbnailer/src';
                require_once $moduleDir . '/Thumbnailer.php';
                return new \IccThumbnailer\Thumbnailer(
                    $container->get('Omeka\Cli'),
                    $container->get('Omeka\File\TempFileFactory')
                );
            },
        ],
        'aliases' => [
            'Omeka\File\Store' => 'Omeka\File\Store\Local',
            'Omeka\File\Thumbnailer' => 'IccThumbnailer\Thumbnailer',
        ],
    ],
];
