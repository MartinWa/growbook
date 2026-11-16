"""Cycle identity upgrades preserve populated associations and immutable retries."""

import hashlib
import json
from datetime import date
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from core.models import User
from core.writes import validated_write
from garden.models import Planting
from garden.queries import planting_history
from garden.services import create_planting

pytestmark = pytest.mark.django_db(transaction=True)


def test_populated_cycle_identity_upgrade_and_reverse_preserve_rows_and_retry_payloads():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    before = [
        ("garden", "0006_planting_event_replanting"),
        ("core", "0006_planting_category_photo"),
        ("diary", "0004_diaryentry_title"),
    ]
    try:
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        actor = apps.get_model("core", "User").objects.create(username="synthetic-identity-upgrade")
        crop = apps.get_model("garden", "Crop").objects.create(
            name="Trial", name_key="trial", is_archived=True
        )
        variety = apps.get_model("garden", "Variety").objects.create(
            crop=crop, name="Golden", name_key="golden", is_archived=True
        )
        area = apps.get_model("garden", "GrowingArea").objects.create(
            name="Bed", name_key="bed", is_archived=True
        )
        cycle = apps.get_model("garden", "PlantingCycle").objects.create(
            status="completed", started_on=date(2024, 3, 1), completed_on=date(2026, 9, 1)
        )
        planting = apps.get_model("garden", "Planting")
        unknown = planting.objects.create(
            crop=crop,
            area=area,
            cycle=cycle,
            batch_key=cycle.pk,
            start_year=2024,
            lifespan="persistent",
            end_year=2026,
            quantity=0,
            evaluation="Unknown result",
        )
        values = {
            "crop_id": crop.pk,
            "variety_id": variety.pk,
            "area_id": area.pk,
            "start_year": 2025,
            "lifespan": "persistent",
            "end_year": 2026,
            "label": "Golden trial",
            "quantity": 7,
            "notes": "",
            "evaluation": "Known result",
        }
        known = planting.objects.create(cycle=cycle, batch_key=cycle.pk, **values)
        key = uuid4()
        payload = {
            "version": 1,
            "kind": "planting",
            "pk": None,
            "expected_updated_at": None,
            "values": {**values, "batch_planting": unknown.pk},
            "photos": [],
        }
        fingerprint = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        apps.get_model("garden", "PlantingSubmission").objects.create(
            key=key, author=actor, kind="planting", planting=known, payload_sha256=fingerprint
        )
        event = apps.get_model("garden", "PlantingEvent").objects.create(
            planting=known, kind="sowing", actual_date=date(2025, 3, 1), note="Kept event"
        )
        apps.get_model("garden", "PlantingSubmission").objects.create(
            key=uuid4(), author=actor, kind="event", event=event, payload_sha256="1" * 64
        )
        apps.get_model("garden", "CycleSubmission").objects.create(
            key=uuid4(), author=actor, kind="cycle", cycle=cycle, payload_sha256="2" * 64
        )
        apps.get_model("garden", "PlantingSubmission").objects.create(
            key=uuid4(), author=actor, kind="planting", payload_sha256="3" * 64
        )
        category = apps.get_model("garden", "PlantingCategory").objects.create(
            name="Trial category", name_key="trial category"
        )
        apps.get_model("garden", "PlantingCategoryLink").objects.create(
            category=category, cycle=cycle
        )
        entry_key = uuid4()
        entry = apps.get_model("diary", "DiaryEntry").objects.create(
            author=actor, date=date(2025, 3, 1), body="Kept observation", submission_key=entry_key
        )
        apps.get_model("diary", "DiarySubmission").objects.create(
            key=entry_key, author=actor, entry=entry, payload_sha256="4" * 64
        )
        apps.get_model("diary", "DiaryLink").objects.create(entry=entry, planting=known)
        apps.get_model("core", "Photo").objects.create(
            planting=known,
            original="synthetic/original.jpg",
            display="synthetic/display.webp",
            thumbnail="synthetic/thumbnail.webp",
            original_sha256="5" * 64,
            original_format="JPEG",
            original_bytes=1,
            width=1,
            height=1,
            caption="Kept caption",
            position=3,
        )
        preserved = (
            ("garden", "PlantingCycle"),
            ("garden", "PlantingEvent"),
            ("garden", "PlantingCategoryLink"),
            ("garden", "PlantingSubmission"),
            ("garden", "CycleSubmission"),
            ("diary", "DiaryEntry"),
            ("diary", "DiarySubmission"),
            ("diary", "DiaryLink"),
            ("core", "Photo"),
        )
        snapshots = {
            model: list(apps.get_model(*model).objects.order_by("pk").values())
            for model in preserved
        }
        members = list(planting.objects.order_by("pk").values())
        for member in members:
            assert member.pop("batch_key") == member["cycle_id"] == cycle.pk

        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        upgraded = executor.loader.project_state(latest).apps
        assert list(Planting.objects.order_by("pk").values()) == members
        for model, snapshot in snapshots.items():
            assert list(upgraded.get_model(*model).objects.order_by("pk").values()) == snapshot
        assert set(planting_history(year=2026, area=area.pk)) == set(Planting.objects.all())
        assert not planting_history(year=2027).exists()
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, "garden_planting")
        assert "planting_cycle_matches_batch" not in constraints
        assert constraints["planting_batch_variety_unique"]["columns"] == ["cycle_id", "variety_id"]
        assert constraints["planting_batch_unknown_unique"]["columns"] == ["cycle_id"]
        assert not any("batch_key" in item["columns"] for item in constraints.values())
        for variety_id in (None, variety.pk):
            with pytest.raises(IntegrityError), transaction.atomic(), validated_write():
                Planting.objects.create(
                    crop_id=crop.pk, cycle_id=cycle.pk, variety_id=variety_id, start_year=2026
                )
        retry = create_planting(
            actor=User.objects.get(pk=actor.pk),
            crop=crop.pk,
            variety=variety.pk,
            area=area.pk,
            start_year=2025,
            lifespan="persistent",
            end_year=2026,
            label="Golden trial",
            quantity=7,
            evaluation="Known result",
            batch_planting=unknown.pk,
            submission_key=key,
        )
        assert retry.replayed and retry.record.pk == known.pk and retry.record.cycle_id == cycle.pk

        executor = MigrationExecutor(connection)
        executor.migrate(before)
        restored = executor.loader.project_state(before).apps.get_model("garden", "Planting")
        assert list(restored.objects.order_by("pk").values()) == [
            {**member, "batch_key": cycle.pk} for member in members
        ]
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        assert list(Planting.objects.order_by("pk").values()) == members
    finally:
        MigrationExecutor(connection).migrate(latest)
