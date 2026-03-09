<?php declare(strict_types=1);

namespace SiteLockdown\Form;

use Laminas\Form\Element;
use Laminas\Form\Form;

class ConfigForm extends Form
{
    public function init(): void
    {
        $this->add([
            'name' => 'password',
            'type' => Element\Password::class,
            'options' => [
                'label' => 'Password',
                'info' => 'The shared password visitors must enter. Leave blank to keep the current password.',
            ],
            'attributes' => [
                'id' => 'site-lockdown-password',
                'autocomplete' => 'new-password',
            ],
        ]);

        $this->add([
            'name' => 'cookie_duration',
            'type' => Element\Select::class,
            'options' => [
                'label' => 'Cookie duration',
                'info' => 'How long the password cookie lasts before the visitor must re-enter it.',
                'value_options' => [
                    '0' => 'Browser session',
                    '86400' => '24 hours',
                    '604800' => '7 days',
                    '2592000' => '30 days',
                ],
            ],
            'attributes' => [
                'id' => 'site-lockdown-cookie-duration',
            ],
        ]);
    }
}
