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
    // The Thumbnailer alias is set by the IccThumbnailer module.
    'service_manager' => [
        'aliases' => [
            'Omeka\File\Store' => 'Omeka\File\Store\Local',
        ],
    ],
];
