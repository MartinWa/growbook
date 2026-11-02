from uuid import uuid4

import pytest
from django.contrib import admin
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory

from core.writes import validated_write
from garden.admin import CropAdmin, GrowingAreaAdmin, VarietyAdmin
from garden.models import Crop, GrowingArea, RegisterSubmission, Variety
from garden.names import normalize_name
from garden.services import (
    RegisterConflict,
    archive_area,
    archive_crop,
    archive_variety,
    create_area,
    create_crop,
    create_variety,
    delete_area,
    delete_crop,
    delete_variety,
    edit_area,
    edit_crop,
    edit_variety,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    ("first", "duplicate"),
    [
        ("Ärtor", "  ÄRTOR  "),
        ("Åland", "A\u030aland"),
        ("Sweet\t pea", "sweet\u00a0\nPEA"),
        ("Straße", "STRASSE"),
        ("ΐ", "ι\u0308\u0301"),
    ],
)
def test_crop_unicode_whitespace_casefold_duplicates(user, first, duplicate):
    stored = create_crop(actor=user, name=first).record
    assert stored.name == first.strip()
    with pytest.raises(ValidationError) as rejected:
        create_crop(actor=user, name=duplicate)
    assert "name" in rejected.value.message_dict
    assert Crop.objects.count() == 1


def test_name_display_is_preserved_and_only_the_comparison_key_collapses_whitespace(user):
    name = "  A\u030a  Bean\t Mix  "
    crop = create_crop(actor=user, name=name).record
    assert crop.name == name.strip()
    assert crop.name_key == "å bean mix"
    assert normalize_name(" Tomato ") == ("Tomato", "tomato")
    create_crop(actor=user, name="Tomato")
    create_crop(actor=user, name="Tomatoes")
    create_crop(actor=user, name="Ｔｏｍａｔｏ")  # NFC is deliberate; not compatibility folding.
    assert Crop.objects.count() == 4


@pytest.mark.parametrize("name", ["", " \n\t\u00a0", None, "x" * 101])
def test_required_bounded_crop_name(user, name):
    with pytest.raises(ValidationError):
        create_crop(actor=user, name=name)
    assert not Crop.objects.exists()
    assert not RegisterSubmission.objects.exists()


def test_variety_names_are_per_crop_and_reclassification_revalidates_duplicates(user):
    first_crop = create_crop(actor=user, name="Tomato").record
    second_crop = create_crop(actor=user, name="Bean").record
    first = create_variety(
        actor=user, crop=first_crop, name="Golden", source="Synthetic source"
    ).record
    create_variety(actor=user, crop=second_crop, name="GOLDEN")
    with pytest.raises(ValidationError):
        create_variety(actor=user, crop=first_crop, name=" golden ")
    with pytest.raises(ValidationError):
        edit_variety(actor=user, pk=first.pk, crop=second_crop, name="Golden")
    first.refresh_from_db()
    assert first.crop_id == first_crop.pk and first.source == "Synthetic source"
    changed = edit_variety(
        actor=user, pk=first.pk, crop=second_crop, name="Golden Two", experiences="Good in trial"
    ).record
    assert changed.crop_id == second_crop.pk
    assert changed.experiences == "Good in trial"


def test_area_top_level_and_sibling_uniqueness_have_distinct_scopes(user):
    root = create_area(actor=user, name="Bed").record
    other = create_area(actor=user, name="Greenhouse").record
    child = create_area(actor=user, name="BED", parent=root).record
    create_area(actor=user, name="Bed", parent=other)
    with pytest.raises(ValidationError):
        create_area(actor=user, name=" bed ")
    with pytest.raises(ValidationError):
        create_area(actor=user, name="bed", parent=root)
    with pytest.raises(ValidationError):
        edit_area(actor=user, pk=child.pk, name="Bed", parent=other)
    with pytest.raises(ValidationError):
        edit_area(actor=user, pk=child.pk, name="Bed")
    child.refresh_from_db()
    assert child.parent_id == root.pk
    renamed = edit_area(actor=user, pk=child.pk, name="New Bed", parent=other).record
    assert renamed.parent_id == other.pk
    assert GrowingArea.objects.count() == 4


@pytest.mark.parametrize("target", ["self", "child", "grandchild"])
def test_area_reparenting_rejects_cycles(user, target):
    root = create_area(actor=user, name="Root").record
    child = create_area(actor=user, name="Child", parent=root).record
    grandchild = create_area(actor=user, name="Grandchild", parent=child).record
    parent = {"self": root, "child": child, "grandchild": grandchild}[target]
    with pytest.raises(ValidationError) as rejected:
        edit_area(actor=user, pk=root.pk, name="Root", parent=parent)
    assert "parent" in rejected.value.message_dict
    root.refresh_from_db()
    assert root.parent_id is None


def test_missing_deleted_and_unsaved_parent_references_are_rejected(user):
    for parent in (99999, GrowingArea(name="Unsaved"), True):
        with pytest.raises(ValidationError):
            create_area(actor=user, name="Invalid", parent=parent)
    for crop in (99999, Crop(name="Unsaved"), None, True):
        with pytest.raises(ValidationError):
            create_variety(actor=user, name="Invalid", crop=crop)
    assert not GrowingArea.objects.exists() and not Variety.objects.exists()


@pytest.mark.parametrize("length,width", [(None, None), (1, None), (None, 120), (150, 40)])
def test_area_dimensions_are_optional_independent_and_descriptive(user, length, width):
    area = create_area(
        actor=user,
        name="Irregular area",
        area_type="Any user-defined type",
        length_cm=length,
        width_cm=width,
        description="Not a rectangular layout",
        notes="Synthetic",
    ).record
    assert (area.length_cm, area.width_cm) == (length, width)
    assert area.area_type == "Any user-defined type"


@pytest.mark.parametrize("field", ["length_cm", "width_cm"])
@pytest.mark.parametrize("value", [0, -1, 1.5, True, "12"])
def test_area_dimensions_reject_nonpositive_or_noninteger_values(user, field, value):
    with pytest.raises(ValidationError):
        create_area(actor=user, name="Invalid", **{field: value})
    assert not GrowingArea.objects.exists()


def test_archive_is_non_cascading_reserves_names_and_keeps_existing_associations(user):
    crop = create_crop(actor=user, name="Tomato").record
    variety = create_variety(actor=user, crop=crop, name="Golden").record
    parent = create_area(actor=user, name="Greenhouse").record
    child = create_area(actor=user, name="Bed", parent=parent).record
    archive_crop(actor=user, pk=crop.pk)
    archive_area(actor=user, pk=parent.pk)
    child.refresh_from_db()
    variety.refresh_from_db()
    assert not child.is_archived and not variety.is_archived
    with pytest.raises(ValidationError):
        create_crop(actor=user, name="TOMATO")
    with pytest.raises(ValidationError):
        create_area(actor=user, name="GREENHOUSE")
    with pytest.raises(ValidationError):
        create_variety(actor=user, crop=crop, name="New")
    with pytest.raises(ValidationError):
        create_area(actor=user, name="New", parent=parent)
    assert (
        edit_variety(actor=user, pk=variety.pk, crop=crop, name="Renamed").record.crop_id == crop.pk
    )
    assert (
        edit_area(actor=user, pk=child.pk, parent=parent, name="Renamed").record.parent_id
        == parent.pk
    )
    archive_variety(actor=user, pk=variety.pk)
    assert archive_crop(actor=user, pk=crop.pk, archived=False).is_archived is False
    assert archive_area(actor=user, pk=parent.pk, archived=False).is_archived is False
    assert archive_variety(actor=user, pk=variety.pk, archived=False).is_archived is False


def test_deletion_protects_varieties_and_child_areas_and_keeps_archive_as_an_option(user):
    crop = create_crop(actor=user, name="Tomato").record
    variety = create_variety(actor=user, crop=crop, name="Golden").record
    parent = create_area(actor=user, name="Root").record
    child = create_area(actor=user, name="Child", parent=parent).record
    with pytest.raises(ValidationError):
        delete_crop(actor=user, pk=crop.pk)
    with pytest.raises(ValidationError):
        delete_area(actor=user, pk=parent.pk)
    archive_area(actor=user, pk=child.pk)
    with pytest.raises(ValidationError):
        delete_area(actor=user, pk=parent.pk)
    delete_variety(actor=user, pk=variety.pk)
    delete_crop(actor=user, pk=crop.pk)
    delete_area(actor=user, pk=child.pk)
    delete_area(actor=user, pk=parent.pk)
    assert (
        not Crop.objects.exists()
        and not Variety.objects.exists()
        and not GrowingArea.objects.exists()
    )
    assert RegisterSubmission.objects.count() == 4
    assert not RegisterSubmission.objects.exclude(crop=None, variety=None, area=None).exists()


def test_relationship_writes_reject_wrong_model_objects_even_when_ids_overlap(user):
    crop = create_crop(actor=user, name="Tomato").record
    area = create_area(actor=user, name="Bed").record
    with pytest.raises(ValidationError):
        create_variety(actor=user, crop=GrowingArea(pk=crop.pk, name="Wrong kind"), name="No")
    with pytest.raises(ValidationError):
        create_area(actor=user, parent=Crop(pk=area.pk, name="Wrong kind"), name="No")
    assert not Variety.objects.exists() and GrowingArea.objects.count() == 1


def test_edits_and_delete_reject_stale_versions_but_creation_retries_use_receipts(user):
    key = uuid4()
    first = create_crop(actor=user, name="Tomato", notes="Original", submission_key=key).record
    old_version = first.updated_at
    edited = edit_crop(
        actor=user, pk=first.pk, name="Renamed", notes="New notes", expected_updated_at=old_version
    ).record
    assert edited.created_at == first.created_at and edited.updated_at > old_version
    with pytest.raises(RegisterConflict):
        edit_crop(actor=user, pk=first.pk, name="Lost update", expected_updated_at=old_version)
    with pytest.raises(RegisterConflict):
        delete_crop(actor=user, pk=first.pk, expected_updated_at=old_version)
    retry = create_crop(actor=user, name="Tomato", notes="Original", submission_key=key)
    assert retry.replayed and retry.record.name == "Renamed"
    with pytest.raises(RegisterConflict):
        create_crop(actor=user, name="Changed", submission_key=key)
    delete_crop(actor=user, pk=first.pk)
    with pytest.raises(RegisterConflict):
        create_crop(actor=user, name="Tomato", notes="Original", submission_key=key)
    assert not Crop.objects.exists()


@pytest.mark.parametrize("model", [Crop, Variety, GrowingArea])
@pytest.mark.parametrize(
    "operation", ["save", "update", "bulk_create", "bulk_update", "delete", "queryset_delete"]
)
def test_direct_register_orm_writes_are_rejected(model, operation):
    record = model(pk=99999, name="Bypass")
    with pytest.raises(RuntimeError, match="validated domain"):
        if operation == "save":
            record.save()
        elif operation == "update":
            model.objects.update(name="Bypass")
        elif operation == "bulk_create":
            model.objects.bulk_create([])
        elif operation == "bulk_update":
            model.objects.bulk_update([record], ["name"])
        elif operation == "delete":
            record.delete()
        else:
            model.objects.all().delete()


def test_database_constraints_independently_enforce_local_integrity(user):
    crop = create_crop(actor=user, name="Tomato").record
    area = create_area(actor=user, name="Bed").record
    child = create_area(actor=user, name="Child", parent=area).record
    variety = create_variety(actor=user, crop=crop, name="Golden").record
    for record in (
        Crop(name="Duplicate", name_key=crop.name_key),
        Crop(name="", name_key=""),
        Variety(crop=crop, name="Duplicate", name_key=variety.name_key),
        GrowingArea(name="Duplicate root", name_key=area.name_key),
        GrowingArea(parent=area, name="Duplicate sibling", name_key=child.name_key),
        GrowingArea(name="Invalid length", name_key="length", length_cm=0),
        GrowingArea(name="Invalid width", name_key="width", width_cm=0),
    ):
        with pytest.raises(IntegrityError), transaction.atomic(), validated_write():
            record.save()  # Deliberately skip validation to prove the database constraint.
    with pytest.raises(IntegrityError), transaction.atomic(), validated_write():
        GrowingArea.objects.filter(pk=area.pk).update(parent_id=area.pk)


def test_register_admin_is_read_only_and_has_no_bulk_mutation_actions(user):
    request = RequestFactory().get("/operator/")
    user.is_staff = user.is_superuser = True
    request.user = user
    for model, model_admin in (
        (Crop, CropAdmin),
        (Variety, VarietyAdmin),
        (GrowingArea, GrowingAreaAdmin),
    ):
        instance = model_admin(model, admin.site)
        assert not instance.has_add_permission(request)
        assert not instance.has_change_permission(request)
        assert not instance.has_delete_permission(request)
        assert instance.actions is None
        assert "name_key" in instance.get_readonly_fields(request)


def test_register_operations_reject_anonymous_inactive_and_stale_active_accounts(user):
    with pytest.raises(PermissionDenied):
        create_crop(actor=AnonymousUser(), name="No")
    type(user).objects.filter(pk=user.pk).update(is_active=False)
    assert user.is_active  # Re-read shared account state, not only this Python object.
    with pytest.raises(PermissionDenied):
        create_crop(actor=user, name="No")
    assert not Crop.objects.exists()


def test_register_operations_reject_nontext_values_and_invalid_version_tokens(user):
    from datetime import datetime

    for values in ({"notes": 123}, {"notes": None}):
        with pytest.raises(ValidationError):
            create_crop(actor=user, name="Invalid", **values)
    with pytest.raises(ValidationError):
        create_area(actor=user, name=object())
    crop = create_crop(actor=user, name="Tomato").record
    for token in ("not-a-date", datetime(2026, 5, 9)):
        with pytest.raises(ValidationError):
            edit_crop(actor=user, pk=crop.pk, name="No change", expected_updated_at=token)
    assert Crop.objects.count() == 1 and not GrowingArea.objects.exists()


def test_all_domain_operations_reject_existing_atomic_wrapper(user):
    crop = create_crop(actor=user, name="Tomato").record
    for operation in (
        lambda: create_crop(actor=user, name="Bean"),
        lambda: create_area(actor=user, name="Bed"),
        lambda: create_variety(actor=user, crop=crop, name="Golden"),
        lambda: archive_crop(actor=user, pk=crop.pk),
        lambda: delete_crop(actor=user, pk=crop.pk),
    ):
        with transaction.atomic(), pytest.raises(RuntimeError, match="outside"):
            operation()
