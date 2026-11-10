"""Add titles without changing existing observations, photos or retry receipts."""

import hashlib
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from core.models import Photo, User
from diary.models import DiaryEditSubmission, DiaryEntry, DiarySubmission
from diary.services import create_entry, update_entry

pytestmark = pytest.mark.django_db(transaction=True)


def test_title_migration_preserves_existing_content_and_legacy_retries():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    before = [node for node in latest if node[0] != "diary"] + [
        ("diary", "0003_diaryeditsubmission")
    ]
    try:
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        user = apps.get_model("core", "User").objects.create(username="synthetic-title-upgrade")
        key, edit_key = uuid4(), uuid4()
        body = "Synthetic legacy note"
        record = apps.get_model("diary", "DiaryEntry").objects.create(
            date="2026-05-09",
            body=body,
            search_text=body.casefold(),
            author_id=user.pk,
            submission_key=key,
        )
        creation_hash = hashlib.sha256(
            b'{"version":1,"date":"2026-05-09","body":"Synthetic legacy note","photos":[]}'
        ).hexdigest()
        apps.get_model("diary", "DiarySubmission").objects.create(
            key=key, author_id=user.pk, entry_id=record.pk, payload_sha256=creation_hash
        )
        version = record.updated_at
        edit_values = {
            "pk": record.pk,
            "date": "2026-05-09",
            "body": body,
            "links": [],
            "remove_photo_ids": [],
            "expected_updated_at": (version - timedelta(seconds=1)).isoformat(),
            "photos": [],
            "version": 1,
        }
        edit_hash = hashlib.sha256(
            json.dumps(
                edit_values, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        apps.get_model("diary", "DiaryEditSubmission").objects.create(
            key=edit_key, author_id=user.pk, entry_id=record.pk, payload_sha256=edit_hash
        )
        photo = apps.get_model("core", "Photo").objects.create(
            entry_id=record.pk,
            original="synthetic/original.jpg",
            display="synthetic/display.webp",
            thumbnail="synthetic/thumbnail.webp",
            original_sha256="0" * 64,
            original_format="JPEG",
            original_bytes=1,
            width=1,
            height=1,
        )

        MigrationExecutor(connection).migrate(latest)
        upgraded = DiaryEntry.objects.get(pk=record.pk)
        actor = User.objects.get(pk=user.pk)
        assert upgraded.title == "" and upgraded.body == body
        assert upgraded.search_text == body.casefold() and upgraded.updated_at == version
        assert Photo.objects.get(pk=photo.pk).entry_id == upgraded.pk
        assert DiarySubmission.objects.get(key=key).payload_sha256 == creation_hash
        assert DiaryEditSubmission.objects.get(key=edit_key).payload_sha256 == edit_hash

        capture_values = {
            "author": actor,
            "date": upgraded.date,
            "body": body,
            "uploads": (),
            "submission_key": key,
        }
        first_retry = create_entry(**capture_values)
        assert not first_retry.created and first_retry.entry.updated_at == version
        retry_values = {
            "actor": actor,
            "pk": upgraded.pk,
            "date": upgraded.date,
            "body": body,
            "expected_updated_at": version - timedelta(seconds=1),
            "submission_key": edit_key,
        }
        assert update_entry(**retry_values).updated_at == version
        later = update_entry(
            actor=actor,
            pk=upgraded.pk,
            date=upgraded.date,
            body=body,
            title="Added after the upgrade",
            expected_updated_at=version,
        )
        assert create_entry(**capture_values).entry.title == later.title
        assert update_entry(**retry_values).title == later.title
        assert DiaryEntry.objects.count() == DiarySubmission.objects.count() == 1
        assert DiaryEditSubmission.objects.count() == 2
    finally:
        MigrationExecutor(connection).migrate(latest)
