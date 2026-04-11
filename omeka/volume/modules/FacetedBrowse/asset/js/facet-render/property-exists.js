FacetedBrowse.registerFacetApplyStateHandler('property_exists', function(facet, facetState) {
    const thisFacet = $(facet);
    facetState = facetState ?? [];
    facetState.forEach(function(value) {
        thisFacet.find(`input.value[data-type="${value}"]`)
            .prop('checked', true)
            .addClass('selected');
    });
});

$(document).ready(function() {

const container = $('#container');

container.on('click', '.facet[data-facet-type="property_exists"] input.value', function(e) {
    const thisValue = $(this);
    const facet = thisValue.closest('.facet');
    const facetData = facet.data('facetData');
    const propertyId = facetData.property_id;

    // Single-select: deselect others
    facet.find('.value').not(thisValue).removeClass('selected').prop('checked', false);

    // Toggle: clicking already-selected deselects (clears filter)
    if (thisValue.hasClass('selected')) {
        thisValue.removeClass('selected').prop('checked', false);
        FacetedBrowse.setFacetState(facet.data('facetId'), [], '');
        FacetedBrowse.triggerStateChange();
        return;
    }

    thisValue.addClass('selected');

    const type = thisValue.data('type'); // 'ex' or 'nex'
    const query = `property[0][joiner]=and&property[0][property]=${propertyId}&property[0][type]=${type}`;
    FacetedBrowse.setFacetState(facet.data('facetId'), [type], query);
    FacetedBrowse.triggerStateChange();
});

});
