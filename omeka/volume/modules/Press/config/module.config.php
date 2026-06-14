<?php declare(strict_types=1);

namespace Press;

return [
    'router' => [
        'routes' => [
            // Site-child route (consistent with Exhibitions/CollectorSubmission).
            // `press` is added to omeka/clean-urls.php whitelist so the clean URL
            // /press maps to /s/catalog/press before routing.
            'site' => [
                'child_routes' => [
                    'press' => [
                        'type' => \Laminas\Router\Http\Literal::class,
                        'options' => [
                            'route' => '/press',
                            'defaults' => [
                                '__NAMESPACE__' => 'Press\\Controller',
                                'controller' => Controller\PressController::class,
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
            Controller\PressController::class => Service\Controller\PressControllerFactory::class,
        ],
    ],
    'view_manager' => [
        'template_path_stack' => [
            __DIR__ . '/../view',
        ],
    ],
    'press' => [
        // Press items reuse the Writing resource template (id 3).
        'resource_template_id' => 3,
        // Curated "Press" item set — only members appear on the wall.
        // Resolved by title (env-agnostic; item-set ids differ across local/prod).
        'item_set_title' => 'Press',
        // Omeka property IDs on the press items
        'property_id_title' => 1,
        'property_id_date' => 7,          // dcterms:date (ISO YYYY-MM-DD), backfilled from creditText
        'property_id_publisher' => 5,     // dcterms:publisher (normalized outlet name)
        // Outlet (dcterms:publisher value) → logo slug in asset/img/press-logos/{slug}.png.
        // Only outlets listed here render a real logo; everything else falls back
        // to _placeholder.png. Keys MUST match the normalized dcterms:publisher text.
        'outlet_logos' => [
            'ARTnews' => 'artnews',
            'AXNS Collective' => 'axns-collective',
            'Art Finder' => 'art-finder',
            'Art New England' => 'art-new-england',
            'ArtThrob' => 'artthrob',
            'Artaissance' => 'artaissance',
            'BBC' => 'bbc',
            'BookBrowse' => 'bookbrowse',
            'Cracked' => 'cracked',
            'DMU Literary Review' => 'dmu-literary-review',
            'Discovery Channel' => 'discovery',
            'Four Corners' => 'four-corners',
            'GQ' => 'gq',
            'Gloucester Daily Times' => 'gloucester-daily-times',
            'Harvard CHS Research Bulletin' => 'harvard-chs-research-bulletin',
            'Huffington Post' => 'huffington-post',
            'Insight Magazine' => 'insight-magazine',
            'Kirkus Reviews' => 'kirkus',
            'Main Streets & Back Roads of New England' => 'main-streets-back-roads',
            'MediaBistro' => 'mediabistro',
            'NPR' => 'npr',
            'Neurology Now' => 'neurology-now',
            'New Scientist' => 'new-scientist',
            'New York Post' => 'new-york-post',
            'PBS NewsHour' => 'pbs-newshour',
            'Peabody Essex Museum' => 'peabody-essex-museum',
            'Popular Science' => 'popular-science',
            'Princeton Day School' => 'princeton-day-school',
            'Psychology Today' => 'psychology-today',
            "Reader's Digest" => 'readers-digest',
            'Star Tribune' => 'star-tribune',
            'The Book of Zines' => 'book-of-zines',
            'The Boston Globe' => 'boston-globe',
            'The Daily Telegraph' => 'daily-telegraph',
            'The Guardian' => 'guardian',
            'The Lancet' => 'the-lancet',
            'The New York Times' => 'new-york-times',
            'The Pennsylvania Gazette' => 'penn-gazette',
            'The Pingry Review' => 'pingry-review',
            'The Pingry School' => 'pingry-school',
            'The Star-Ledger' => 'star-ledger',
            'This American Life' => 'this-american-life',
            "Today's Chiropractic" => 'todays-chiropractic',
            'Town Online' => 'town-online',
            'Vanity Fair' => 'vanity-fair',
            'Wine & Bowties' => 'wine-and-bowties',
            'Zap2it' => 'zap2it',
            // Added from the jonsarkin.com/pages/press list
            'ABC Australia' => 'abc-australia',
            'ABC News' => 'abc-news',
            'Adrants' => 'adrants',
            'Arts & Health' => 'arts-and-health',
            'Boston Herald' => 'boston-herald',
            'Boston.com' => 'boston-com',
            'Capture the Extraordinary' => 'capture-the-extraordinary',
            'TakePart' => 'takepart',
            'Cambridge University Press' => 'cambridge-university-press',
            'Cape Ann Museum' => 'cape-ann-museum',
            'Digiday' => 'digiday',
            'Getty Images' => 'getty-images',
            'Good Morning Gloucester' => 'good-morning-gloucester',
            'Greg Cook' => 'greg-cook',
            'Guster' => 'guster',
            'IMDb' => 'imdb',
            'Juniper Rag' => 'juniper-rag',
            'Landry & Arcari' => 'landry-and-arcari',
            'MoMA' => 'moma',
            'New Jersey Stage' => 'new-jersey-stage',
            'Palate & Palette' => 'palate-and-palette',
            'Practical Neurology' => 'practical-neurology',
            'Raw Vision' => 'raw-vision',
            'Rock Pop Gallery' => 'rock-pop-gallery',
            'Salem News' => 'salem-news',
            'Ville de Paris' => 'ville-de-paris',
            'Worcester Telegram' => 'worcester-telegram',
            'YouTube' => 'youtube',
            'Yorokobu' => 'yorokobu',
        ],
    ],
];
