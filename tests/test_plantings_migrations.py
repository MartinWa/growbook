"""Upgrade the register schema with synthetic rows; never touch the live database."""

from uuid import uuid4

import pytest
from django.db import connection
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
