"""Restore combined capture receipts and replay against a clean SQLite/WAL instance."""

import os
import subprocess
import sys
from datetime import date
from uuid import uuid4

import pytest
from django.conf import settings as django_settings

from core.backups import create_backup, restore_backup
from diary.actions import CycleStatusAction, NewPlantingAction, PlantingEventAction
from diary.services import create_entry
from garden.services import create_crop

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("kind", ["sowing", "replanting"])
def test_clean_restore_preserves_combined_actions_photos_and_receipt_replay(
    user, image_upload, tmp_path, kind
):
    crop = create_crop(actor=user, name="Tomato").record

    def save(action, **kwargs):
        return create_entry(
            author=user,
            date=date(2026, 1, 2),
            body="Synthetic combined observation",
            uploads=kwargs.pop("uploads", ()),
            submission_key=uuid4(),
            action=action,
            **kwargs,
        ).entry

    created = save(
        NewPlantingAction(crop=crop.pk, lifespan="persistent"),
        uploads=[image_upload()],
        planting_uploads=[image_upload(format="PNG")],
    )
    planting = created.links.get().planting
    save(PlantingEventAction(planting=planting.pk, kind=kind))
    planting.refresh_from_db()
    cycle = planting.cycle
    cycle_version, member_version = cycle.updated_at.isoformat(), planting.updated_at.isoformat()
    save(
        CycleStatusAction(
            planting=planting.pk,
            status="completed",
            expected_updated_at=cycle.updated_at,
            started_on=cycle.started_on,
            completed_on=date(2026, 1, 2),
            evaluations={planting.pk: "Synthetic restored evaluation"},
            expected_member_versions={planting.pk: planting.updated_at},
        )
    )
    config = tmp_path / "operator.env"
    config.write_text("GROWBOOK_SECRET_KEY=" + "synthetic-recovery-key-" * 4 + "\n")
    backup, restored = tmp_path / "backup", tmp_path / "clean-instance"
    create_backup(backup, [config])
    restore_backup(backup, restored)
    script = """
import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'growbook.settings.development'
import django
django.setup()
from datetime import date, datetime
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from core.models import User, Photo
from core.media import private_path
from diary.models import DiaryEntry, DiarySubmission
from diary.actions import NewPlantingAction, PlantingEventAction, CycleStatusAction
from diary.services import create_entry
from garden.models import Planting, PlantingEvent
user = User.objects.get(username='gardener')
planting = Planting.objects.get()
assert planting.cycle.status == 'completed' and planting.end_year == 2026
assert planting.evaluation == 'Synthetic restored evaluation'
assert PlantingEvent.objects.get().actual_date == date(2026, 1, 2)
assert PlantingEvent.objects.get().kind == os.environ['TEST_EVENT_KIND']
assert DiaryEntry.objects.count() == DiarySubmission.objects.count() == 3
assert Photo.objects.count() == 2
entries = list(DiaryEntry.objects.order_by('id'))
def upload(photo):
    return SimpleUploadedFile('synthetic.upload', private_path(photo.original.name).read_bytes())
common = dict(author=user, date=date(2026, 1, 2), body='Synthetic combined observation')
assert not create_entry(**common, submission_key=entries[0].submission_key,
    action=NewPlantingAction(crop=planting.crop_id, lifespan='persistent'),
    uploads=[upload(entries[0].photos.get())],
    planting_uploads=[upload(planting.photos.get())]).created
assert not create_entry(**common, submission_key=entries[1].submission_key,
    action=PlantingEventAction(planting=planting.pk, kind=os.environ['TEST_EVENT_KIND']),
    uploads=[]).created
assert not create_entry(**common, submission_key=entries[2].submission_key, uploads=[],
    action=CycleStatusAction(planting=planting.pk, status='completed',
        expected_updated_at=datetime.fromisoformat(os.environ['TEST_CYCLE_VERSION']),
        started_on=date(2026, 1, 2), completed_on=date(2026, 1, 2),
        evaluations={planting.pk: 'Synthetic restored evaluation'},
        expected_member_versions={planting.pk:
            datetime.fromisoformat(os.environ['TEST_MEMBER_VERSION'])})).created
assert Planting.objects.count() == PlantingEvent.objects.count() == 1
with connection.cursor() as cursor:
    cursor.execute('PRAGMA journal_mode')
    assert cursor.fetchone()[0] == 'wal'
"""
    environment = os.environ.copy()
    environment.update(
        GROWBOOK_DATA_DIR=str(restored),
        TEST_CYCLE_VERSION=cycle_version,
        TEST_MEMBER_VERSION=member_version,
        TEST_EVENT_KIND=kind,
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=django_settings.BASE_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
