"""Upgrade from the diary-only schema without touching a development/live database."""

from uuid import uuid4

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)


def test_register_migration_preserves_existing_diary_photo_owner_and_receipt():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    before = [("core", "0002_initial"), ("diary", "0001_initial"), ("garden", None)]
    try:
        executor.migrate(before)
        apps = executor.loader.project_state(
            [("core", "0002_initial"), ("diary", "0001_initial")]
        ).apps
        user = apps.get_model("core", "User").objects.create(username="synthetic-upgrade")
        key = uuid4()
        entry = apps.get_model("diary", "DiaryEntry").objects.create(
            date="2026-05-09", body="Original diary content", author_id=user.pk, submission_key=key
        )
        apps.get_model("diary", "DiarySubmission").objects.create(
            key=key, entry_id=entry.pk, author_id=user.pk, payload_sha256="0" * 64
        )
        photo = apps.get_model("core", "Photo").objects.create(
            entry_id=entry.pk,
            original="synthetic/original.jpg",
            display="synthetic/display.webp",
            thumbnail="synthetic/thumbnail.webp",
            original_sha256="0" * 64,
            original_format="JPEG",
            original_bytes=1,
            width=1,
            height=1,
        )
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        apps = executor.loader.project_state(latest).apps
        upgraded = apps.get_model("core", "Photo").objects.get(pk=photo.pk)
        assert (
            upgraded.entry_id == entry.pk
            and upgraded.area_id is None
            and upgraded.variety_id is None
        )
        assert upgraded.original.name == "synthetic/original.jpg"
        assert (
            apps.get_model("diary", "DiaryEntry").objects.get(pk=entry.pk).body
            == "Original diary content"
        )
        assert apps.get_model("diary", "DiarySubmission").objects.get(key=key).entry_id == entry.pk
        assert not apps.get_model("garden", "Crop").objects.exists()
        with connection.cursor() as cursor:
            assert connection.introspection.get_constraints(cursor, "core_photo")[
                "photo_exactly_one_owner"
            ]["check"]
    finally:
        MigrationExecutor(connection).migrate(latest)
