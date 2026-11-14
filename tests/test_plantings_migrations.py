"""Upgrade the register schema with synthetic rows; never touch the live database."""

from datetime import date
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)


def test_planting_migration_preserves_existing_registers_all_old_owners_and_receipts():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    before = [
        ("core", "0003_register_photo_owners"),
        ("garden", "0001_initial"),
        ("diary", "0001_initial"),
    ]
    try:
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        user = apps.get_model("core", "User").objects.create(username="synthetic-upgrade")
        crop = apps.get_model("garden", "Crop").objects.create(
            name="Tomato", name_key="tomato", is_archived=True
        )
        variety = apps.get_model("garden", "Variety").objects.create(
            crop=crop, name="Golden", name_key="golden"
        )
        root = apps.get_model("garden", "GrowingArea").objects.create(
            name="Root", name_key="root", is_archived=True
        )
        child = apps.get_model("garden", "GrowingArea").objects.create(
            parent=root, name="Bed", name_key="bed", length_cm=120
        )
        diary_key, register_key = uuid4(), uuid4()
        entry = apps.get_model("diary", "DiaryEntry").objects.create(
            author=user, date="2026-05-09", body="Synthetic old entry", submission_key=diary_key
        )
        apps.get_model("diary", "DiarySubmission").objects.create(
            key=diary_key, author=user, entry=entry, payload_sha256="0" * 64
        )
        apps.get_model("garden", "RegisterSubmission").objects.create(
            key=register_key, author=user, area=child, kind="area", payload_sha256="1" * 64
        )
        original_photos = []
        for kind, owner in (("entry", entry), ("variety", variety), ("area", child)):
            original_photos.append(
                apps.get_model("core", "Photo").objects.create(
                    **{kind: owner},
                    original=f"synthetic/{kind}/original.jpg",
                    display=f"synthetic/{kind}/display.webp",
                    thumbnail=f"synthetic/{kind}/thumbnail.webp",
                    original_sha256="0" * 64,
                    original_format="JPEG",
                    original_bytes=1,
                    width=1,
                    height=1,
                )
            )
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        apps = executor.loader.project_state(latest).apps
        assert apps.get_model("garden", "Crop").objects.get(pk=crop.pk).is_archived
        stored_child = apps.get_model("garden", "GrowingArea").objects.get(pk=child.pk)
        assert stored_child.parent_id == root.pk and stored_child.width_cm is None
        assert (
            apps.get_model("garden", "RegisterSubmission").objects.get(key=register_key).area_id
            == child.pk
        )
        assert (
            apps.get_model("diary", "DiarySubmission").objects.get(key=diary_key).entry_id
            == entry.pk
        )
        for old in original_photos:
            photo = apps.get_model("core", "Photo").objects.get(pk=old.pk)
            assert photo.planting_id is None
            assert (photo.entry_id, photo.area_id, photo.variety_id) == (
                old.entry_id,
                old.area_id,
                old.variety_id,
            )
            assert photo.original.name == old.original.name
        assert not apps.get_model("garden", "Planting").objects.exists()
        assert not apps.get_model("garden", "PlantingEvent").objects.exists()
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, "core_photo")
            assert constraints["photo_exactly_one_owner"]["check"]
    finally:
        MigrationExecutor(connection).migrate(latest)


def test_replanting_migration_preserves_existing_events_and_receipts():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    before = [("garden", "0005_plantingcategory_plantingcycle_plantingcategorylink_and_more")]
    try:
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        user = apps.get_model("core", "User").objects.create(username="synthetic-event-upgrade")
        crop = apps.get_model("garden", "Crop").objects.create(name="Trial", name_key="trial")
        cycle = apps.get_model("garden", "PlantingCycle").objects.create()
        planting = apps.get_model("garden", "Planting").objects.create(
            crop=crop, start_year=2026, cycle=cycle, batch_key=cycle.pk
        )
        event_model = apps.get_model("garden", "PlantingEvent")
        originals = []
        for kind in ("sowing", "transplant", "harvest_start", "harvest_end", "other"):
            event = event_model.objects.create(
                planting=planting,
                kind=kind,
                planned_date=date(2026, 3, 1),
                actual_date=date(2026, 3, 2),
                note="Synthetic event",
            )
            receipt = apps.get_model("garden", "PlantingSubmission").objects.create(
                key=uuid4(), author=user, kind="event", event=event, payload_sha256="1" * 64
            )
            originals.append((event, receipt.pk))

        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        apps = executor.loader.project_state(latest).apps
        event_model = apps.get_model("garden", "PlantingEvent")
        for old, receipt_key in originals:
            stored = event_model.objects.get(pk=old.pk)
            assert (
                stored.kind,
                stored.planting_id,
                stored.planned_date,
                stored.actual_date,
                stored.note,
                stored.created_at,
                stored.updated_at,
            ) == (
                old.kind,
                old.planting_id,
                old.planned_date,
                old.actual_date,
                old.note,
                old.created_at,
                old.updated_at,
            )
            receipt = apps.get_model("garden", "PlantingSubmission").objects.get(pk=receipt_key)
            assert receipt.event_id == old.pk and receipt.payload_sha256 == "1" * 64
        replanting = event_model.objects.create(
            planting_id=planting.pk, kind="replanting", actual_date=date(2026, 4, 1)
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            event_model.objects.filter(pk=replanting.pk).update(kind="invalid")
        with pytest.raises(IntegrityError), transaction.atomic():
            event_model.objects.filter(pk=replanting.pk).update(actual_date=None)
    finally:
        MigrationExecutor(connection).migrate(latest)
