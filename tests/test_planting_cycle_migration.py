"""Upgrade existing batches without changing member identities or inventing dates."""

from datetime import date
from uuid import uuid4

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)


def test_existing_cycle_status_migration_preserves_mixed_batch_and_unknown_dates(monkeypatch):
    from django.utils import timezone

    monkeypatch.setattr(timezone, "localdate", lambda: date(2026, 11, 13))
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    before = [("garden", "0004_planting_batches")]
    try:
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        crop = apps.get_model("garden", "Crop").objects.create(name="Trial", name_key="trial")
        variety_model = apps.get_model("garden", "Variety")
        planting = apps.get_model("garden", "Planting")
        cases = [
            (2025, "annual", None, "completed"),
            (2026, "annual", None, "ongoing"),
            (2027, "annual", None, "planned"),
            (2024, "persistent", 2025, "completed"),
            (2024, "persistent", 2026, "ongoing"),
            (2024, "persistent", None, "ongoing"),
            (2027, "persistent", None, "planned"),
        ]
        originals = []
        for index, (year, lifespan, end, status) in enumerate(cases):
            member = planting.objects.create(
                crop=crop,
                start_year=year,
                lifespan=lifespan,
                end_year=end,
                label=f"Trial {index}",
                quantity=0,
                evaluation="Kept",
            )
            originals.append((member, status))
        mixed_keys = [uuid4(), uuid4()]
        for key, years in zip(mixed_keys, [(2025, 2026, 2027), (2025, 2027)], strict=True):
            for index, year in enumerate(years):
                variety = variety_model.objects.create(
                    crop=crop, name=f"V {key} {index}", name_key=f"v {key} {index}"
                )
                planting.objects.create(crop=crop, variety=variety, batch_key=key, start_year=year)
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        apps = executor.loader.project_state(latest).apps
        cycle = apps.get_model("garden", "PlantingCycle")
        upgraded = apps.get_model("garden", "Planting")
        for old, status in originals:
            member = upgraded.objects.get(pk=old.pk)
            assert member.cycle_id == old.batch_key == member.batch_key
            assert (
                member.start_year,
                member.end_year,
                member.quantity,
                member.label,
                member.evaluation,
            ) == (old.start_year, old.end_year, 0, old.label, "Kept")
            shared = cycle.objects.get(pk=member.cycle_id)
            assert shared.status == status
            assert shared.started_on is None and shared.completed_on is None
        assert cycle.objects.get(pk=mixed_keys[0]).status == "ongoing"
        assert cycle.objects.get(pk=mixed_keys[1]).status == "planned"
        assert cycle.objects.count() == len(cases) + 2
    finally:
        MigrationExecutor(connection).migrate(latest)
