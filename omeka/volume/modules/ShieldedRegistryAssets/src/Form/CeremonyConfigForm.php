<?php declare(strict_types=1);

namespace ShieldedRegistryAssets\Form;

use Laminas\Form\Element;
use Laminas\Form\Form;
use Laminas\InputFilter\InputFilterProviderInterface;

/**
 * Form for the 3-step ceremony wizard.
 *
 * Step 1: ra_name, party_mode
 * Step 2: threshold_m/n, participant_labels (multi only), pk_r
 * Step 3: domain, ra_scope, contact
 */
class CeremonyConfigForm extends Form implements InputFilterProviderInterface
{
    public function init(): void
    {
        // --- Step 1: Estate Info ---

        $this->add([
            'name' => 'ra_name',
            'type' => Element\Text::class,
            'options' => [
                'label' => 'Name of the Estate',
            ],
            'attributes' => [
                'placeholder' => 'e.g. Estate of Jon Sarkin',
            ],
        ]);

        $this->add([
            'name' => 'party_mode',
            'type' => Element\Radio::class,
            'options' => [
                'label' => 'Who is managing this registry?',
                'value_options' => [
                    'solo' => "It's just me",
                    'multi' => "I'm working with multiple people",
                ],
            ],
        ]);

        // --- Step 2: Key Generation ---

        $this->add([
            'name' => 'threshold_m',
            'type' => Element\Number::class,
            'options' => [
                'label' => 'Signing Threshold (M)',
            ],
            'attributes' => [
                'min' => 1,
                'max' => 255,
                'step' => 1,
                'placeholder' => '2',
            ],
        ]);

        $this->add([
            'name' => 'threshold_n',
            'type' => Element\Number::class,
            'options' => [
                'label' => 'Total Participants (N)',
            ],
            'attributes' => [
                'min' => 1,
                'max' => 255,
                'step' => 1,
                'placeholder' => '3',
            ],
        ]);

        $this->add([
            'name' => 'participant_labels',
            'type' => Element\Textarea::class,
            'options' => [
                'label' => 'Participant Labels',
            ],
            'attributes' => [
                'placeholder' => "Operational share (estate director)\nCold storage (safe deposit)\nRecovery holder (legal counsel)",
                'rows' => 4,
            ],
        ]);

        $this->add([
            'name' => 'pk_r',
            'type' => Element\Text::class,
            'options' => [
                'label' => 'Registry Authority Public Key (PK_R)',
            ],
            'attributes' => [
                'placeholder' => '64-character hex-encoded Ed25519 public key',
                'pattern' => '[0-9a-fA-F]{64}',
            ],
        ]);

        // --- Step 3: Identity Binding ---

        $this->add([
            'name' => 'domain',
            'type' => Element\Text::class,
            'options' => [
                'label' => 'Domain',
            ],
            'attributes' => [
                'placeholder' => 'e.g. catalog.jonsarkin.com',
            ],
        ]);

        $this->add([
            'name' => 'ra_scope',
            'type' => Element\Textarea::class,
            'options' => [
                'label' => 'Scope of Authority',
            ],
            'attributes' => [
                'placeholder' => 'e.g. Complete works of Jon Sarkin, all media, 1988–present',
                'rows' => 3,
            ],
        ]);

        $this->add([
            'name' => 'contact',
            'type' => Element\Email::class,
            'options' => [
                'label' => 'Contact Email (optional)',
            ],
            'attributes' => [
                'placeholder' => 'registry@example.com',
            ],
        ]);

        // --- Legacy (kept for data compat) ---

        $this->add([
            'name' => 'genesis_txid',
            'type' => Element\Text::class,
            'options' => [
                'label' => 'Genesis Declaration Transaction ID',
            ],
            'attributes' => [
                'placeholder' => '64-character hex txid from Zcash',
                'pattern' => '[0-9a-fA-F]{64}',
            ],
        ]);

        // --- Submit ---

        $this->add([
            'name' => 'submit',
            'type' => Element\Submit::class,
            'attributes' => [
                'value' => 'Save',
            ],
        ]);
    }

    public function getInputFilterSpecification(): array
    {
        return [
            'ra_name' => ['required' => false],
            'party_mode' => ['required' => false],
            'ra_scope' => ['required' => false],
            'threshold_m' => ['required' => false],
            'threshold_n' => ['required' => false],
            'participant_labels' => ['required' => false],
            'pk_r' => ['required' => false],
            'domain' => ['required' => false],
            'contact' => ['required' => false],
            'genesis_txid' => ['required' => false],
        ];
    }
}
