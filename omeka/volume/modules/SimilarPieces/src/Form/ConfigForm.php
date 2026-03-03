<?php declare(strict_types=1);

namespace SimilarPieces\Form;

use Laminas\Form\Element;
use Laminas\Form\Form;

class ConfigForm extends Form
{
    public function init(): void
    {
        $this->add([
            'name' => 'base_url',
            'type' => Element\Text::class,
            'options' => [
                'label' => 'Similarity service base URL',
                'info' => 'Example: https://similar.jonsarkin.com',
            ],
            'attributes' => [
                'id' => 'similar-pieces-base-url',
                'placeholder' => 'https://similar.jonsarkin.com',
            ],
        ]);

        $this->add([
            'name' => 'enable_search_ui',
            'type' => Element\Checkbox::class,
            'options' => [
                'label' => 'Enable Search UI',
                'info' => 'Adds a public Similar search page when enabled.',
                'use_hidden_element' => true,
                'checked_value' => '1',
                'unchecked_value' => '0',
            ],
            'attributes' => [
                'id' => 'similar-pieces-enable-search-ui',
            ],
        ]);

        $this->add([
            'name' => 'debug',
            'type' => Element\Checkbox::class,
            'options' => [
                'label' => 'Enable debug logging',
                'info' => 'When enabled, stack traces are written to the log for troubleshooting.',
                'use_hidden_element' => true,
                'checked_value' => '1',
                'unchecked_value' => '0',
            ],
            'attributes' => [
                'id' => 'similar-pieces-debug',
            ],
        ]);
    }
}
