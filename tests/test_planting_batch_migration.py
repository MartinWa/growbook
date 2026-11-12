"""Planting batch upgrades preserve record identity and immutable save receipts."""

import hashlib
import json
from uuid import uuid4

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from core.models import User
from garden.models import Planting
from garden.services import create_planting

pytestmark = pytest.mark.django_db(transaction=True)


def test_batch_migration_preserves_old_ids_fields_and_immutable_receipts():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    before = [("garden", "0003_cropcategory_cropcategorylink_and_more")]
    try:
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        user = apps.get_model("core", "User").objects.create(username="synthetic-batch-upgrade")
        crop = apps.get_model("garden", "Crop").objects.create(name="Tomato", name_key="tomato")
        planting = apps.get_model("garden", "Planting")
        first = planting.objects.create(crop=crop, start_year=2027, label="Old batch", quantity=0)
        second = planting.objects.create(crop=crop, start_year=2027, label="Old batch")
        planting.objects.bulk_create(
            [
                planting(crop=crop, start_year=2027, label=f"Synthetic planting {n}")
                for n in range(501)
            ]
        )
        key = uuid4()
        # The pre-batch receipt format must remain replayable after the migration.
        legacy_payload = {
            "version": 1,
            "kind": "planting",
            "pk": None,
            "expected_updated_at": None,
            "values": {
                "crop_id": crop.pk,
                "variety_id": None,
                "area_id": None,
                "start_year": 2027,
                "lifespan": "annual",
                "end_year": None,
                "label": "Old batch",
                "quantity": 0,
                "notes": "",
                "evaluation": "",
            },
            "photos": [],
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                legacy_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        apps.get_model("garden", "PlantingSubmission").objects.create(
            key=key,
            author=user,
            planting=first,
            kind="planting",
            payload_sha256=fingerprint,
        )
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        upgraded = Planting.objects.get(pk=first.pk)
        assert upgraded.label == "Old batch" and upgraded.quantity == 0
        assert upgraded.batch_key != Planting.objects.get(pk=second.pk).batch_key
        assert Planting.objects.values("batch_key").distinct().count() == 503
        assert not Planting.objects.filter(batch_key__isnull=True).exists()
        receipt = (
            executor.loader.project_state(latest)
            .apps.get_model("garden", "PlantingSubmission")
            .objects.get(key=key)
        )
        assert receipt.planting_id == first.pk and receipt.payload_sha256 == fingerprint
        replay = create_planting(
            actor=User.objects.get(pk=user.pk),
            crop=crop.pk,
            start_year=2027,
            label="Old batch",
            quantity=0,
            submission_key=key,
        )
        assert replay.replayed and replay.record.pk == first.pk
    finally:
        MigrationExecutor(connection).migrate(latest)
