from datetime import date, datetime
from uuid import uuid4

import pytest
from django.contrib import admin
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory

from core.writes import validated_write
from garden.admin import PlantingAdmin, PlantingEventAdmin
from garden.models import Crop, GrowingArea, Planting, PlantingEvent, PlantingSubmission, Variety
from garden.queries import planting_history, plantings_in_year
from garden.services import (
    RegisterConflict,
    archive_area,
    archive_crop,
    archive_variety,
    create_area,
    create_crop,
    create_planting,
    create_planting_event,
    create_variety,
    delete_area,
    delete_crop,
    delete_planting,
    delete_planting_event,
    delete_variety,
    edit_area,
    edit_planting,
    edit_planting_event,
    edit_variety,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def catalog(user):
    crop = create_crop(actor=user, name="Tomato").record
    other_crop = create_crop(actor=user, name="Bean").record
    variety = create_variety(actor=user, crop=crop, name="Golden").record
    other_variety = create_variety(actor=user, crop=other_crop, name="Golden").record
    root = create_area(actor=user, name="Greenhouse").record
    child = create_area(actor=user, name="Bed", parent=root).record
    grandchild = create_area(actor=user, name="Pot", parent=child).record
    other_area = create_area(actor=user, name="Border").record
    return {
        "crop": crop,
        "other_crop": other_crop,
        "variety": variety,
        "other_variety": other_variety,
        "root": root,
        "child": child,
        "grandchild": grandchild,
        "other_area": other_area,
    }


def edit_values(record, **overrides):
    return {
        "pk": record.pk,
        "crop": record.crop_id,
        "variety": record.variety_id,
        "area": record.area_id,
        "start_year": record.start_year,
        "lifespan": record.lifespan,
        "end_year": record.end_year,
        "label": record.label,
        "quantity": record.quantity,
        "notes": record.notes,
        "evaluation": record.evaluation,
        **overrides,
    }


def test_early_plan_requires_only_crop_and_year_and_preserves_unknown_vs_zero(user, catalog):
    first = create_planting(actor=user, crop=catalog["crop"], start_year=2027).record
    assert first.variety_id is None and first.area_id is None and first.quantity is None
    assert first.lifespan == Planting.Lifespan.ANNUAL and first.end_year is None
    second = create_planting(actor=user, crop=catalog["crop"], start_year=2027, quantity=0).record
    assert second.pk != first.pk and second.quantity == 0
    selected = edit_planting(
        actor=user,
        **edit_values(first, variety=catalog["variety"], area=catalog["child"], quantity=4),
    ).record
    assert (selected.variety_id, selected.area_id, selected.quantity) == (
        catalog["variety"].pk,
        catalog["child"].pk,
        4,
    )
    assert selected.created_at == first.created_at


@pytest.mark.parametrize("year", [1, 1999, 2026, 2099, 9999])
def test_past_current_future_calendar_years_are_allowed(user, catalog, year):
    record = create_planting(actor=user, crop=catalog["crop"], start_year=year).record
    assert list(plantings_in_year(year)) == [record]


@pytest.mark.parametrize("year", [None, 0, -1, 10000, True, 2026.5, "2026"])
def test_year_is_required_bounded_and_integer_on_non_ui_writer(user, catalog, year):
    with pytest.raises(ValidationError):
        create_planting(actor=user, crop=catalog["crop"], start_year=year)
    assert not Planting.objects.exists() and not PlantingSubmission.objects.exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"lifespan": "unknown"},
        {"lifespan": None},
        {"end_year": 2026},
        {"lifespan": "persistent", "end_year": 2025},
        {"lifespan": "persistent", "end_year": 0},
        {"lifespan": "persistent", "end_year": 10000},
        {"lifespan": "persistent", "end_year": True},
        {"quantity": -1},
        {"quantity": 1.5},
        {"quantity": True},
        {"quantity": "1"},
        {"notes": None},
        {"evaluation": 123},
        {"label": "x" * 151},
    ],
)
def test_local_planting_rules_reject_invalid_complete_save(user, catalog, overrides):
    with pytest.raises(ValidationError):
        create_planting(actor=user, crop=catalog["crop"], start_year=2026, **overrides)
    assert not Planting.objects.exists() and not PlantingSubmission.objects.exists()


@pytest.mark.parametrize("field", ["crop", "variety", "area"])
@pytest.mark.parametrize("value", [99999, True, "1"])
def test_writer_rejects_missing_wrongly_typed_associations(user, catalog, field, value):
    values = {"crop": catalog["crop"], "start_year": 2026, field: value}
    with pytest.raises(ValidationError):
        create_planting(actor=user, **values)
    assert not Planting.objects.exists()


def test_wrong_model_and_unsaved_fk_objects_do_not_alias_overlapping_ids(user, catalog):
    for field, value in (
        ("crop", GrowingArea(pk=catalog["crop"].pk)),
        ("variety", Crop(pk=catalog["variety"].pk)),
        ("area", Variety(pk=catalog["root"].pk)),
        ("crop", Crop()),
        ("variety", Variety()),
        ("area", GrowingArea()),
        ("crop", None),
    ):
        with pytest.raises(ValidationError):
            create_planting(
                actor=user, **{"crop": catalog["crop"], "start_year": 2026, field: value}
            )
    assert not Planting.objects.exists()


def test_every_planting_writer_rejects_crop_variety_mismatch_and_never_infers_crop(user, catalog):
    with pytest.raises(ValidationError) as rejected:
        create_planting(
            actor=user, crop=catalog["crop"], start_year=2026, variety=catalog["other_variety"]
        )
    assert "variety" in rejected.value.message_dict
    record = create_planting(
        actor=user, crop=catalog["crop"], start_year=2026, variety=catalog["variety"]
    ).record
    with pytest.raises(ValidationError):
        edit_planting(actor=user, **edit_values(record, crop=catalog["other_crop"]))
    record.refresh_from_db()
    assert record.crop_id == catalog["crop"].pk
    changed = edit_planting(
        actor=user, **edit_values(record, crop=catalog["other_crop"], variety=None)
    ).record
    assert changed.crop_id == catalog["other_crop"].pk and changed.variety_id is None


def test_model_validation_uses_current_not_cached_taxonomy(user, catalog):
    variety = catalog["variety"]
    edit_variety(actor=user, pk=variety.pk, name="Reclassified", crop=catalog["other_crop"])
    # The caller's model object still points at the former crop.
    record = Planting(crop=catalog["crop"], start_year=2026, variety=variety)
    with pytest.raises(ValidationError) as rejected:
        record.full_clean()
    assert "variety" in rejected.value.message_dict
    with pytest.raises(ValidationError):
        create_planting(actor=user, crop=catalog["crop"], start_year=2026, variety=variety)


def test_multiple_identical_assigned_batches_are_not_collapsed(user, catalog):
    records = [
        create_planting(
            actor=user,
            crop=catalog["crop"],
            variety=catalog["variety"],
            area=catalog["child"],
            start_year=2026,
        ).record
        for _ in range(3)
    ]
    assert len({record.pk for record in records}) == 3
    assert set(planting_history(year=2026, crop=catalog["crop"], area=catalog["root"])) == set(
        records
    )


def test_history_paths_distinguish_same_named_siblings_in_different_parents(user, catalog):
    from garden.queries import planting_rows

    other_bed = create_area(actor=user, name="Bed", parent=catalog["other_area"]).record
    first = create_planting(
        actor=user, crop=catalog["crop"], start_year=2026, area=catalog["child"]
    ).record
    second = create_planting(
        actor=user, crop=catalog["other_crop"], start_year=2026, area=other_bed
    ).record
    rows = planting_rows(planting_history(year=2026))
    assert {row.pk: row.area_path for row in rows} == {
        first.pk: "Greenhouse / Bed",
        second.pk: "Border / Bed",
    }
    assert list(planting_history(area=catalog["root"])) == [first]
    assert list(planting_history(area=catalog["other_area"])) == [second]


def test_year_membership_is_inclusive_and_never_duplicates_persistent_records(user, catalog):
    annual = create_planting(actor=user, crop=catalog["crop"], start_year=2026).record
    finite = create_planting(
        actor=user, crop=catalog["crop"], start_year=2025, lifespan="persistent", end_year=2027
    ).record
    ongoing = create_planting(
        actor=user, crop=catalog["crop"], start_year=2026, lifespan="persistent"
    ).record
    single = create_planting(
        actor=user, crop=catalog["crop"], start_year=2026, lifespan="persistent", end_year=2026
    ).record
    assert not plantings_in_year(2024).exists()
    assert set(plantings_in_year(2025)) == {finite}
    assert set(plantings_in_year(2026)) == {annual, finite, ongoing, single}
    assert set(plantings_in_year(2027)) == {finite, ongoing}
    assert set(plantings_in_year(2028)) == {ongoing}
    assert list(plantings_in_year(9999)) == [ongoing]
    assert Planting.objects.count() == 4
    for invalid in (0, 10000, True, "2026"):
        with pytest.raises(ValueError):
            plantings_in_year(invalid)


def test_ending_persistent_planting_keeps_cumulative_evaluation_and_events(user, catalog):
    record = create_planting(
        actor=user,
        crop=catalog["crop"],
        start_year=2024,
        lifespan="persistent",
        evaluation="Synthetic cumulative results",
    ).record
    event = create_planting_event(
        actor=user,
        planting=record,
        kind="sowing",
        planned_date=date(2023, 12, 20),
        actual_date=date(2024, 1, 2),
    ).record
    ended = edit_planting(actor=user, **edit_values(record, end_year=2026)).record
    assert ended.evaluation == record.evaluation
    assert ended.events.get().pk == event.pk
    assert list(plantings_in_year(2026)) == [ended] and not plantings_in_year(2027).exists()
    assert ended.pk == record.pk and ended.created_at == record.created_at


def test_descendants_archives_unknown_varieties_and_current_area_corrections(user, catalog):
    rows = {}
    for name, area in (
        ("own", "root"),
        ("child", "child"),
        ("grandchild", "grandchild"),
        ("other", "other_area"),
    ):
        rows[name] = create_planting(
            actor=user,
            crop=catalog["crop"],
            start_year=2026,
            area=catalog[area],
            variety=catalog["variety"] if name == "grandchild" else None,
        ).record
    unassigned = create_planting(actor=user, crop=catalog["crop"], start_year=2026).record
    archive_area(actor=user, pk=catalog["child"].pk)
    archive_crop(actor=user, pk=catalog["crop"].pk)
    archive_variety(actor=user, pk=catalog["variety"].pk)
    assert set(planting_history(year=2026, area=catalog["root"])) == {
        rows["own"],
        rows["child"],
        rows["grandchild"],
    }
    assert set(planting_history(year=2026, unassigned=True)) == {unassigned}
    assert set(planting_history(year=2026, unknown_variety=True)) == {
        rows["own"],
        rows["child"],
        rows["other"],
        unassigned,
    }
    assert list(planting_history(variety=catalog["variety"])) == [rows["grandchild"]]
    assert set(planting_history(crop=catalog["crop"])) == {*rows.values(), unassigned}
    corrected = edit_planting(
        actor=user, **edit_values(rows["child"], area=catalog["other_area"])
    ).record
    assert corrected.pk not in set(
        planting_history(area=catalog["root"]).values_list("pk", flat=True)
    )
    edit_area(actor=user, pk=catalog["grandchild"].pk, name="Pot", parent=catalog["other_area"])
    assert list(planting_history(area=catalog["root"])) == [rows["own"]]
    assert set(planting_history(year=2026, area=catalog["other_area"])) == {
        corrected,
        rows["grandchild"],
        rows["other"],
    }


def test_archived_associations_are_retained_not_offered_for_new_writes(user, catalog):
    record = create_planting(
        actor=user,
        crop=catalog["crop"],
        variety=catalog["variety"],
        area=catalog["child"],
        start_year=2026,
        lifespan="persistent",
    ).record
    archive_variety(actor=user, pk=catalog["variety"].pk)
    archive_area(actor=user, pk=catalog["child"].pk)
    archive_crop(actor=user, pk=catalog["crop"].pk)
    updated = edit_planting(actor=user, **edit_values(record, notes="Still present")).record
    assert list(plantings_in_year(2027)) == [updated]
    for values in (
        {"crop": catalog["crop"]},
        {"crop": catalog["other_crop"], "area": catalog["child"]},
        {"crop": catalog["crop"], "variety": catalog["variety"]},
    ):
        with pytest.raises(ValidationError):
            create_planting(actor=user, start_year=2026, **values)
    # An active variety under an archived crop is not a new selection either.
    another = create_variety(actor=user, crop=catalog["other_crop"], name="Second").record
    crop_only = create_planting(actor=user, crop=catalog["other_crop"], start_year=2026).record
    archive_crop(actor=user, pk=catalog["other_crop"].pk)
    with pytest.raises(ValidationError):
        edit_planting(actor=user, **edit_values(crop_only, variety=another))


def test_referenced_variety_crop_changes_and_register_deletion_are_protected(user, catalog):
    record = create_planting(
        actor=user,
        crop=catalog["crop"],
        variety=catalog["variety"],
        area=catalog["other_area"],
        start_year=2020,
        lifespan="persistent",
        end_year=2021,
    ).record
    with pytest.raises(ValidationError) as rejected:
        edit_variety(
            actor=user, pk=catalog["variety"].pk, crop=catalog["other_crop"], name="New name"
        )
    assert "crop" in rejected.value.message_dict
    for writer, pk in (
        (delete_crop, catalog["crop"].pk),
        (delete_variety, catalog["variety"].pk),
        (delete_area, catalog["other_area"].pk),
    ):
        with pytest.raises(ValidationError):
            writer(actor=user, pk=pk)
    edit_variety(actor=user, pk=catalog["variety"].pk, crop=catalog["crop"], name="Renamed")
    # Explicitly remove the reference first, not automatic planting reclassification.
    edit_planting(actor=user, **edit_values(record, variety=None))
    edit_variety(actor=user, pk=catalog["variety"].pk, crop=catalog["other_crop"], name="Renamed")
    assert list(planting_history(crop=catalog["crop"]))[0].variety_id is None


def test_crop_only_planting_also_prevents_deleting_its_crop(user, catalog):
    create_planting(actor=user, crop=catalog["other_crop"], start_year=2027)
    delete_variety(actor=user, pk=catalog["other_variety"].pk)
    with pytest.raises(ValidationError):
        delete_crop(actor=user, pk=catalog["other_crop"].pk)


def test_calendar_date_event_kinds_stay_separate_from_growing_year(user, catalog):
    record = create_planting(actor=user, crop=catalog["crop"], start_year=1).record
    for value in (date(1, 1, 1), date(9999, 12, 31)):
        event = create_planting_event(
            actor=user, planting=record, kind="other", actual_date=value
        ).record
        assert event.actual_date == value
    assert list(plantings_in_year(1)) == [record]
    assert not plantings_in_year(9999).exists()


@pytest.mark.parametrize("kind", PlantingEvent.Kind.values)
@pytest.mark.parametrize("dates", ["planned", "actual", "both"])
def test_events_allow_all_kinds_and_independent_dates(user, catalog, kind, dates):
    record = create_planting(actor=user, crop=catalog["crop"], start_year=2027).record
    values = {
        "planned_date": date(2026, 12, 31) if dates != "actual" else None,
        "actual_date": date(2028, 1, 1) if dates != "planned" else None,
    }
    event = create_planting_event(
        actor=user, planting=record, kind=kind, note="Synthetic", **values
    ).record
    assert event.kind == kind and event.note == "Synthetic"
    assert (event.planned_date, event.actual_date) == (
        values["planned_date"],
        values["actual_date"],
    )
    assert not plantings_in_year(2026).exists() and list(plantings_in_year(2027)) == [record]


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"kind": "invalid", "actual_date": date(2026, 1, 1)},
        {"planned_date": "2026-01-01"},
        {"actual_date": datetime(2026, 1, 1)},
        {"actual_date": date(2026, 1, 1), "note": None},
    ],
)
def test_event_validation_rejects_undated_unknown_and_non_date_writes(user, catalog, values):
    record = create_planting(actor=user, crop=catalog["crop"], start_year=2026).record
    with pytest.raises(ValidationError):
        create_planting_event(actor=user, planting=record, **{"kind": "sowing", **values})
    assert not PlantingEvent.objects.exists()
    assert PlantingSubmission.objects.count() == 1


def test_events_repeat_edit_with_versions_and_cannot_be_moved_to_another_planting(user, catalog):
    record = create_planting(actor=user, crop=catalog["crop"], start_year=2026).record
    another = create_planting(actor=user, crop=catalog["crop"], start_year=2026).record
    first = create_planting_event(
        actor=user, planting=record, kind="sowing", actual_date=date(2026, 3, 1)
    ).record
    second = create_planting_event(
        actor=user, planting=record, kind="sowing", actual_date=date(2026, 4, 1)
    ).record
    assert record.events.count() == 2
    key = uuid4()
    values = {
        "actor": user,
        "pk": first.pk,
        "planting": record.pk,
        "kind": "sowing",
        "actual_date": date(2026, 3, 2),
        "expected_updated_at": first.updated_at,
        "submission_key": key,
    }
    edited = edit_planting_event(**values).record
    assert edited.actual_date == date(2026, 3, 2) and edited.created_at == first.created_at
    assert edit_planting_event(**values).replayed
    with pytest.raises(RegisterConflict):
        edit_planting_event(**{**values, "submission_key": uuid4()})
    with pytest.raises(RegisterConflict):
        delete_planting_event(actor=user, pk=first.pk, expected_updated_at=first.updated_at)
    with pytest.raises(ValidationError):
        edit_planting_event(
            actor=user, pk=first.pk, planting=another, kind="other", actual_date=date(2026, 5, 1)
        )
    delete_planting_event(actor=user, pk=first.pk, expected_updated_at=edited.updated_at)
    assert list(record.events.all()) == [second]
    with pytest.raises(RegisterConflict):
        edit_planting_event(**values)


def test_event_save_receipts_and_planting_deletion_tombstones(user, catalog):
    key, event_key = uuid4(), uuid4()
    values = {"actor": user, "crop": catalog["crop"], "start_year": 2026, "submission_key": key}
    record = create_planting(**values).record
    event_values = {
        "actor": user,
        "planting": record.pk,
        "kind": "sowing",
        "planned_date": date(2025, 12, 1),
        "submission_key": event_key,
    }
    event = create_planting_event(**event_values).record
    assert create_planting_event(**event_values).replayed
    with pytest.raises(RegisterConflict):
        create_planting_event(**{**event_values, "actual_date": date(2026, 1, 1)})
    with pytest.raises(RegisterConflict):
        create_planting(**{**values, "submission_key": event_key})
    delete_planting(actor=user, pk=record.pk)
    assert not Planting.objects.exists() and not PlantingEvent.objects.exists()
    assert PlantingSubmission.objects.filter(key=key, planting=None).exists()
    assert PlantingSubmission.objects.filter(key=event_key, event=None).exists()
    with pytest.raises(RegisterConflict):
        create_planting(**values)
    with pytest.raises(RegisterConflict):
        create_planting_event(**event_values)
    assert not PlantingEvent.objects.filter(pk=event.pk).exists()


def test_planting_receipts_are_immutable_across_archives_edits_and_different_actors(
    user, catalog, django_user_model
):
    key = uuid4()
    original = {
        "actor": user,
        "crop": catalog["crop"].pk,
        "variety": catalog["variety"].pk,
        "area": catalog["child"].pk,
        "start_year": 2026,
        "submission_key": key,
    }
    record = create_planting(**original).record
    version = record.updated_at
    update = {
        "actor": user,
        **edit_values(record, label="First edit"),
        "submission_key": uuid4(),
        "expected_updated_at": version,
    }
    edited = edit_planting(**update).record
    edit_planting(
        actor=user,
        **edit_values(edited, crop=catalog["other_crop"], variety=None, area=catalog["other_area"]),
    )
    archive_crop(actor=user, pk=catalog["crop"].pk)
    archive_variety(actor=user, pk=catalog["variety"].pk)
    archive_area(actor=user, pk=catalog["child"].pk)
    assert create_planting(**original).replayed
    assert edit_planting(**update).replayed
    with pytest.raises(RegisterConflict):
        create_planting(**{**original, "start_year": 2027})
    with pytest.raises(RegisterConflict):
        edit_planting(actor=user, **edit_values(record), expected_updated_at=version)
    other = django_user_model.objects.create_user(username="other-gardener")
    with pytest.raises(RegisterConflict):
        create_planting(**{**original, "actor": other})
    assert Planting.objects.count() == 1


@pytest.mark.parametrize("token", ["not-a-version", datetime(2026, 5, 9)])
def test_planting_and_event_version_tokens_require_aware_datetimes(user, catalog, token):
    record = create_planting(actor=user, crop=catalog["crop"], start_year=2026).record
    event = create_planting_event(
        actor=user, planting=record, kind="other", actual_date=date(2026, 5, 9)
    ).record
    operations = [
        lambda: edit_planting(actor=user, **edit_values(record), expected_updated_at=token),
        lambda: delete_planting(actor=user, pk=record.pk, expected_updated_at=token),
        lambda: edit_planting_event(
            actor=user,
            pk=event.pk,
            planting=record,
            kind="other",
            actual_date=event.actual_date,
            expected_updated_at=token,
        ),
        lambda: delete_planting_event(actor=user, pk=event.pk, expected_updated_at=token),
    ]
    for operation in operations:
        with pytest.raises(ValidationError):
            operation()
    assert record.events.count() == 1


@pytest.mark.parametrize("model", [Planting, PlantingEvent, PlantingSubmission])
@pytest.mark.parametrize(
    "operation", ["save", "update", "bulk_create", "bulk_update", "delete", "queryset_delete"]
)
def test_new_domain_models_reject_unvalidated_orm_writes(model, operation):
    record = model(pk=99999)
    with pytest.raises(RuntimeError, match="validated domain"):
        if operation == "save":
            record.save()
        elif operation == "update":
            model.objects.update(pk=99999)
        elif operation == "bulk_create":
            model.objects.bulk_create([])
        elif operation == "bulk_update":
            model.objects.bulk_update([record], ["created_at"])
        elif operation == "delete":
            record.delete()
        else:
            model.objects.all().delete()


def test_database_constraints_enforce_year_lifespan_and_event_rules_independently(user, catalog):
    record = create_planting(actor=user, crop=catalog["crop"], start_year=2026).record
    event = create_planting_event(
        actor=user, planting=record, kind="other", actual_date=date(2026, 1, 1)
    ).record
    for changes in (
        {"start_year": 0},
        {"start_year": 10000},
        {"lifespan": "invalid"},
        {"end_year": 2027},
        {"lifespan": "persistent", "end_year": 2025},
        {"lifespan": "persistent", "end_year": 10000},
        {"quantity": -1},
    ):
        with pytest.raises(IntegrityError), transaction.atomic(), validated_write():
            Planting.objects.filter(pk=record.pk).update(**changes)
    for changes in ({"kind": "invalid"}, {"planned_date": None, "actual_date": None}):
        with pytest.raises(IntegrityError), transaction.atomic(), validated_write():
            PlantingEvent.objects.filter(pk=event.pk).update(**changes)
    for changes in (
        {"kind": "invalid"},
        {"event_id": event.pk},
    ):
        with pytest.raises(IntegrityError), transaction.atomic(), validated_write():
            PlantingSubmission.objects.filter(planting=record).update(**changes)


def test_planting_and_event_admin_are_read_only(user):
    user.is_staff = user.is_superuser = True
    request = RequestFactory().get("/operator/")
    request.user = user
    for model, admin_class in ((Planting, PlantingAdmin), (PlantingEvent, PlantingEventAdmin)):
        instance = admin_class(model, admin.site)
        assert not instance.has_add_permission(request)
        assert not instance.has_change_permission(request)
        assert not instance.has_delete_permission(request)
        assert instance.actions is None
        assert len(instance.get_readonly_fields(request)) == len(model._meta.fields)


def test_planting_operations_require_active_actor_outside_atomic_wrappers(user, catalog):
    record = create_planting(actor=user, crop=catalog["crop"], start_year=2026).record
    event = create_planting_event(
        actor=user, planting=record, kind="other", actual_date=date(2026, 1, 1)
    ).record
    operations = [
        lambda actor: create_planting(actor=actor, crop=catalog["crop"], start_year=2026),
        lambda actor: edit_planting(actor=actor, **edit_values(record)),
        lambda actor: delete_planting(actor=actor, pk=record.pk),
        lambda actor: create_planting_event(
            actor=actor, planting=record, kind="other", actual_date=date(2026, 1, 1)
        ),
        lambda actor: edit_planting_event(
            actor=actor, pk=event.pk, planting=record, kind="other", actual_date=date(2026, 1, 2)
        ),
        lambda actor: delete_planting_event(actor=actor, pk=event.pk),
    ]
    for operation in operations:
        with transaction.atomic(), pytest.raises(RuntimeError, match="outside"):
            operation(user)
        with pytest.raises(PermissionDenied):
            operation(AnonymousUser())
    type(user).objects.filter(pk=user.pk).update(is_active=False)
    for operation in operations:
        with pytest.raises(PermissionDenied):
            operation(user)
    assert Planting.objects.count() == 1 and PlantingEvent.objects.count() == 1
