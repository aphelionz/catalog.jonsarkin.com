<?php
namespace FacetedBrowse\FacetType;

use FacetedBrowse\Api\Representation\FacetedBrowseFacetRepresentation;
use Laminas\Form\Element as LaminasElement;
use Laminas\ServiceManager\ServiceLocatorInterface;
use Laminas\View\Renderer\PhpRenderer;
use Omeka\Form\Element as OmekaElement;

class PropertyExists implements FacetTypeInterface
{
    protected $formElements;

    public function __construct(ServiceLocatorInterface $formElements)
    {
        $this->formElements = $formElements;
    }

    public function getLabel(): string
    {
        return 'Property exists'; // @translate
    }

    public function getResourceTypes(): array
    {
        return ['items', 'item_sets', 'media'];
    }

    public function getMaxFacets(): ?int
    {
        return null;
    }

    public function prepareDataForm(PhpRenderer $view): void
    {
        $view->headScript()->appendFile($view->assetUrl('js/facet-data-form/property-exists.js', 'FacetedBrowse'));
    }

    public function renderDataForm(PhpRenderer $view, array $data): string
    {
        $propertyId = $this->formElements->get(OmekaElement\PropertySelect::class);
        $propertyId->setName('property_id');
        $propertyId->setOptions([
            'label' => 'Property', // @translate
            'empty_option' => '',
        ]);
        $propertyId->setAttributes([
            'id' => 'property-exists-property-id',
            'value' => $data['property_id'] ?? null,
            'data-placeholder' => '[Select a property]', // @translate
        ]);

        $labelExists = $this->formElements->get(LaminasElement\Text::class);
        $labelExists->setName('label_exists');
        $labelExists->setOptions([
            'label' => 'Label for "exists"', // @translate
            'info' => 'The label shown when the property has a value (e.g., "Available for purchase").', // @translate
        ]);
        $labelExists->setAttributes([
            'id' => 'property-exists-label-exists',
            'value' => $data['label_exists'] ?? 'Has value',
        ]);

        $labelNotExists = $this->formElements->get(LaminasElement\Text::class);
        $labelNotExists->setName('label_not_exists');
        $labelNotExists->setOptions([
            'label' => 'Label for "not exists"', // @translate
            'info' => 'The label shown when the property has no value (e.g., "Not listed").', // @translate
        ]);
        $labelNotExists->setAttributes([
            'id' => 'property-exists-label-not-exists',
            'value' => $data['label_not_exists'] ?? 'No value',
        ]);

        return $view->partial('common/faceted-browse/facet-data-form/property-exists', [
            'elementPropertyId' => $propertyId,
            'elementLabelExists' => $labelExists,
            'elementLabelNotExists' => $labelNotExists,
        ]);
    }

    public function prepareFacet(PhpRenderer $view): void
    {
        $view->headScript()->appendFile($view->assetUrl('js/facet-render/property-exists.js', 'FacetedBrowse'));
    }

    public function renderFacet(PhpRenderer $view, FacetedBrowseFacetRepresentation $facet): string
    {
        return $view->partial('common/faceted-browse/facet-render/property-exists', [
            'facet' => $facet,
        ]);
    }
}
