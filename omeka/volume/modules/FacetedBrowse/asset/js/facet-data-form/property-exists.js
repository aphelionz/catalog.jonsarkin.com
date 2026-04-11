FacetedBrowse.registerFacetAddEditHandler('property_exists', function() {
    // Nothing special needed on add/edit
});

FacetedBrowse.registerFacetSetHandler('property_exists', function() {
    const propertyId = $('#property-exists-property-id').val();
    if (!propertyId) {
        alert('You must select a property.');
        return;
    }
    return {
        property_id: propertyId,
        label_exists: $('#property-exists-label-exists').val() || 'Has value',
        label_not_exists: $('#property-exists-label-not-exists').val() || 'No value',
    };
});
