<?php declare(strict_types=1);

namespace MotifTagger\Form;

use Laminas\Form\Element;
use Laminas\Form\Form;

class ConfigForm extends Form
{
    public function init(): void
    {
        $this->add([
            'name' => 'clip_api_url',
            'type' => Element\Text::class,
            'options' => [
                'label' => 'CLIP API base URL',
                'info' => 'Internal Docker hostname for the clip-api service (e.g. http://clip-api:8000)',
            ],
            'attributes' => [
                'id' => 'motiftagger-clip-api-url',
                'placeholder' => 'http://clip-api:8000',
            ],
        ]);

        $this->add([
            'name' => 'default_limit',
            'type' => Element\Number::class,
            'options' => [
                'label' => 'Default result limit',
                'info' => 'Maximum number of results to return from similarity search',
            ],
            'attributes' => [
                'id' => 'motiftagger-default-limit',
                'min' => 1,
                'max' => 500,
            ],
        ]);

        $this->add([
            'name' => 'default_threshold',
            'type' => Element\Number::class,
            'options' => [
                'label' => 'Default similarity threshold',
                'info' => 'Results below this threshold are hidden by default (0.0 to 1.0)',
            ],
            'attributes' => [
                'id' => 'motiftagger-default-threshold',
                'min' => 0,
                'max' => 1,
                'step' => 0.01,
            ],
        ]);

        $this->add([
            'name' => 'motif_property_id',
            'type' => Element\Number::class,
            'options' => [
                'label' => 'Motif property ID',
                'info' => 'Omeka property ID for dcterms:subject (default: 3)',
            ],
            'attributes' => [
                'id' => 'motiftagger-motif-property-id',
                'min' => 1,
            ],
        ]);

        $this->add([
            'name' => 'motif_vocab_label',
            'type' => Element\Text::class,
            'options' => [
                'label' => 'Motif vocabulary label',
                'info' => 'Label of the Custom Vocab containing motif terms',
            ],
            'attributes' => [
                'id' => 'motiftagger-motif-vocab-label',
                'placeholder' => 'Motifs',
            ],
        ]);
    }
}
