"""Planting choices preserve current associations without allowing new parent assignments."""

import pytest

from core.writes import validated_write
from garden.forms import PlantingForm
from garden.models import GrowingArea, Planting

pytestmark = pytest.mark.django_db


def test_leaf_area_choices_preserve_existing_parent_assignments(planting_catalog):
    crop, _, _, area = planting_catalog
    with validated_write():
        existing = Planting.objects.create(crop=crop, area=area, start_year=2027)
        GrowingArea.objects.create(name="Child", name_key="child", parent=area, is_archived=True)
    assert not PlantingForm().fields["area"].queryset.filter(pk=area.pk).exists()
    assert PlantingForm(record=existing).fields["area"].queryset.filter(pk=area.pk).exists()


def test_known_batch_member_requires_variety_until_it_is_alone(planting_catalog):
    crop, first, _, _ = planting_catalog
    with validated_write():
        unknown = Planting.objects.create(crop=crop, start_year=2027)
        known = Planting.objects.create(
            crop=crop, variety=first, start_year=2027, batch_key=unknown.batch_key
        )
    assert not PlantingForm(record=unknown).fields["variety"].required
    assert PlantingForm(record=known).fields["variety"].required
    with validated_write():
        unknown.delete()
    assert not PlantingForm(record=known).fields["variety"].required


def test_name_metadata_survives_bound_choices_and_unavailable_selections(planting_catalog):
    crop, first, _, _ = planting_catalog
    form = PlantingForm({"crop": crop.pk, "variety": first.pk, "area": 2**62})
    assert 'data-record-name="Tomato"' in str(form["crop"])
    assert 'data-record-name="Golden"' in str(form["variety"])
    assert f'value="{2**62}" selected' in str(form["area"])
