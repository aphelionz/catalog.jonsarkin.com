<?php declare(strict_types=1);

namespace CollectorSubmission\Form;

use Laminas\Form\Element;
use Laminas\Form\Form;
use Laminas\InputFilter\InputFilterProviderInterface;

class SubmitForm extends Form implements InputFilterProviderInterface
{
    public function init(): void
    {
        $this->add([
            'name' => 'collector_name',
            'type' => Element\Text::class,
            'options' => ['label' => 'Your Name'],
            'attributes' => ['required' => true, 'placeholder' => 'Full name'],
        ]);

        $this->add([
            'name' => 'email',
            'type' => Element\Email::class,
            'options' => ['label' => 'Email'],
            'attributes' => ['required' => true, 'placeholder' => 'you@example.com'],
        ]);

        $this->add([
            'name' => 'num_pieces',
            'type' => Element\Number::class,
            'options' => ['label' => 'Number of Pieces'],
            'attributes' => ['required' => true, 'min' => 1, 'value' => 1],
        ]);

        $this->add([
            'name' => 'how_acquired',
            'type' => Element\Select::class,
            'options' => [
                'label' => 'How Acquired',
                'value_options' => [
                    '' => 'Select one…',
                    'purchased' => 'Purchased from artist',
                    'gift' => 'Gift from artist',
                    'boltflash' => 'Boltflash (unsolicited mailing)',
                    'secondary' => 'Auction / secondary market',
                    'other' => 'Other',
                ],
            ],
            'attributes' => ['required' => true],
        ]);

        $this->add([
            'name' => 'date_acquired',
            'type' => Element\Text::class,
            'options' => ['label' => 'Approximate Date Acquired'],
            'attributes' => ['placeholder' => 'e.g. Summer 2015, 2010s'],
        ]);

        $this->add([
            'name' => 'description',
            'type' => Element\Textarea::class,
            'options' => ['label' => 'Description of Piece(s)'],
            'attributes' => ['rows' => 4, 'placeholder' => 'Medium, size, subject matter, any text or inscriptions…'],
        ]);

        $this->add([
            'name' => 'dimensions_height',
            'type' => Element\Text::class,
            'options' => ['label' => 'Height'],
            'attributes' => ['placeholder' => 'e.g. 12'],
        ]);

        $this->add([
            'name' => 'dimensions_width',
            'type' => Element\Text::class,
            'options' => ['label' => 'Width'],
            'attributes' => ['placeholder' => 'e.g. 16'],
        ]);

        $this->add([
            'name' => 'dimensions_unit',
            'type' => Element\Select::class,
            'options' => [
                'label' => 'Unit',
                'value_options' => [
                    'in' => 'inches',
                    'cm' => 'cm',
                ],
            ],
        ]);

        $this->add([
            'name' => 'photos',
            'type' => Element\File::class,
            'options' => ['label' => 'Photos'],
            'attributes' => [
                'required' => true,
                'multiple' => true,
                'accept' => 'image/jpeg,image/png,image/heic,.jpg,.jpeg,.png,.heic',
            ],
        ]);

        $this->add([
            'name' => 'exhibition_history',
            'type' => Element\Textarea::class,
            'options' => ['label' => 'Exhibition or Publication History'],
            'attributes' => ['rows' => 3, 'placeholder' => 'Any known exhibitions, publications, or provenance details…'],
        ]);

        $this->add([
            'name' => 'may_contact',
            'type' => Element\Checkbox::class,
            'options' => [
                'label' => 'May we contact you for additional documentation?',
                'checked_value' => '1',
                'unchecked_value' => '0',
            ],
            'attributes' => ['value' => '1'],
        ]);

        $this->add([
            'name' => 'credit_preference',
            'type' => Element\Select::class,
            'options' => [
                'label' => 'How Would You Like to Be Credited?',
                'value_options' => [
                    'full_name' => 'Full name',
                    'private' => '"Private Collection" only',
                    'private_city' => '"Private Collection, [City]"',
                    'other' => 'Other (specify in description)',
                ],
            ],
        ]);

        // Honeypot — hidden via CSS in the template
        $this->add([
            'name' => 'website',
            'type' => Element\Text::class,
            'attributes' => ['tabindex' => '-1', 'autocomplete' => 'off'],
        ]);

        $this->add([
            'name' => 'csrf',
            'type' => Element\Csrf::class,
            'options' => ['csrf_options' => ['timeout' => 1800]],
        ]);

        $this->add([
            'name' => 'submit',
            'type' => Element\Submit::class,
            'attributes' => ['value' => 'Submit'],
        ]);
    }

    public function getInputFilterSpecification(): array
    {
        return [
            'collector_name' => [
                'required' => true,
                'filters' => [['name' => 'StringTrim']],
            ],
            'email' => [
                'required' => true,
                'validators' => [['name' => 'EmailAddress']],
            ],
            'num_pieces' => [
                'required' => true,
                'validators' => [
                    ['name' => 'Digits'],
                    ['name' => 'GreaterThan', 'options' => ['min' => 0]],
                ],
            ],
            'how_acquired' => [
                'required' => true,
                'validators' => [
                    ['name' => 'NotEmpty'],
                    ['name' => 'InArray', 'options' => [
                        'haystack' => ['purchased', 'gift', 'boltflash', 'secondary', 'other'],
                    ]],
                ],
            ],
            'date_acquired' => ['required' => false],
            'description' => ['required' => false],
            'dimensions_height' => ['required' => false, 'filters' => [['name' => 'StringTrim']]],
            'dimensions_width' => ['required' => false, 'filters' => [['name' => 'StringTrim']]],
            'dimensions_unit' => [
                'required' => false,
                'validators' => [['name' => 'InArray', 'options' => ['haystack' => ['in', 'cm']]]],
            ],
            'exhibition_history' => ['required' => false],
            'may_contact' => ['required' => false],
            'credit_preference' => [
                'required' => false,
                'validators' => [
                    ['name' => 'InArray', 'options' => [
                        'haystack' => ['full_name', 'private', 'private_city', 'other'],
                    ]],
                ],
            ],
            'website' => ['required' => false],
        ];
    }
}
