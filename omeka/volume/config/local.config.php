<?php
return [
    // OK to leave null inside a container; Omeka can call php directly.
    'cli' => [
        'phpcli_path' => null,
    ],

    // Persist uploads/derivatives in the bind-mounted volume.
    'file_store' => [
        'local' => [
            'base_path' => '/var/www/html/volume/files',
        ],
    ],

    // ImageMagick is the typical choice in container builds.
    'service_manager' => [
        'aliases' => [
            'Omeka\File\Store' => 'Omeka\File\Store\Local',
            'Omeka\File\Thumbnailer' => 'Omeka\File\Thumbnailer\ImageMagick',
        ],
    ],
];
