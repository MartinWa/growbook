import os
import subprocess
import sys
from datetime import date
from uuid import uuid4

import pytest
from django.conf import settings as django_settings
from django.contrib.auth.hashers import PBKDF2PasswordHasher
from PIL import Image

from core.backups import create_backup, open_snapshot, restore_backup, verify_backup
from core.media import private_path
from diary.services import create_entry
from garden.services import (
    archive_area,
    archive_crop,
    archive_variety,
    create_area,
    create_crop,
    create_planting,
    create_planting_event,
    create_variety,
    delete_planting,
    edit_register_photo,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_clean_restore_preserves_all_photo_owners_registers_archives_and_new_validated_writes(
    user, image_upload, tmp_path
):
    hasher = PBKDF2PasswordHasher()
    user.password = hasher.encode("Synthetic-password-42", hasher.salt())
    user.save(update_fields=["password"])
    config = tmp_path / "operator.env"
    config.write_text("GROWBOOK_SECRET_KEY=" + "synthetic-backup-key-" * 4 + "\n", encoding="utf-8")
    crop = create_crop(actor=user, name="Tomato", notes="Synthetic crop notes").record
    variety = create_variety(
        actor=user,
        crop=crop,
        name="Golden",
        category="Trial",
        source="Synthetic seed source",
        description="Synthetic description",
        experiences="Synthetic experiences",
        uploads=[image_upload(format="HEIF")],
    ).record
    parent = create_area(actor=user, name="Greenhouse", description="Synthetic description").record
    child = create_area(
        actor=user,
        name="Bed",
        parent=parent,
        length_cm=120,
        notes="Synthetic area notes",
        uploads=[image_upload(format="PNG")],
    ).record
    entry = create_entry(
        author=user,
        date=date(2026, 5, 9),
        body="Synthetic diary",
        uploads=[image_upload()],
        submission_key=uuid4(),
        links=[crop, variety, child],
    ).entry
    planting_key, event_key = uuid4(), uuid4()
    planting = create_planting(
        actor=user,
        crop=crop,
        variety=variety,
        area=child,
        start_year=2024,
        lifespan="persistent",
        end_year=2026,
        quantity=0,
        label="Synthetic persistent batch",
        evaluation="Synthetic cumulative results",
        uploads=[image_upload(format="WEBP")],
        submission_key=planting_key,
    ).record
    event = create_planting_event(
        actor=user,
        planting=planting,
        kind="sowing",
        planned_date=date(2023, 12, 20),
        actual_date=date(2024, 1, 2),
        note="Synthetic overwintered sowing",
        submission_key=event_key,
    ).record
    planting.refresh_from_db()
    edit_register_photo(
        actor=user,
        pk=planting.photos.get().pk,
        caption="Synthetic evaluation caption",
        position=4,
        expected_updated_at=planting.updated_at,
    )
    early_plan = create_planting(
        actor=user, crop=crop, start_year=2027, label="Synthetic crop-only plan"
    ).record
    ongoing = create_planting(
        actor=user,
        crop=crop,
        start_year=2025,
        area=child,
        lifespan="persistent",
        label="Synthetic ongoing batch",
    ).record
    deleted_key = uuid4()
    deleted = create_planting(
        actor=user,
        crop=crop,
        start_year=2027,
        label="Synthetic removed plan",
        submission_key=deleted_key,
    ).record
    delete_planting(actor=user, pk=deleted.pk, expected_updated_at=deleted.updated_at)
    archive_crop(actor=user, pk=crop.pk)
    archive_variety(actor=user, pk=variety.pk)
    archive_area(actor=user, pk=parent.pk)
    photos = [variety.photos.get(), child.photos.get(), entry.photos.get(), planting.photos.get()]
    backup, restored = tmp_path / "backup", tmp_path / "clean-instance"
    manifest = create_backup(backup, [config])
    assert verify_backup(backup) == manifest
    assert len(manifest["files"]) == 6
    restore_backup(backup, restored)
    snapshot = open_snapshot(restored / "db.sqlite3")
    try:
        assert snapshot.execute(
            "SELECT name, name_key, notes, is_archived FROM garden_crop"
        ).fetchone() == ("Tomato", "tomato", "Synthetic crop notes", 1)
        assert snapshot.execute(
            "SELECT crop_id, name, source, experiences, is_archived FROM garden_variety"
        ).fetchone() == (crop.pk, "Golden", "Synthetic seed source", "Synthetic experiences", 1)
        assert snapshot.execute(
            "SELECT parent_id, length_cm, width_cm, notes, is_archived "
            "FROM garden_growingarea WHERE id=?",
            [child.pk],
        ).fetchone() == (parent.pk, 120, None, "Synthetic area notes", 0)
        assert snapshot.execute("SELECT COUNT(*) FROM garden_registersubmission").fetchone()[0] == 4
        assert set(
            snapshot.execute(
                "SELECT area_id, crop_id, variety_id, planting_id FROM diary_diarylink"
            ).fetchall()
        ) == {
            (child.pk, None, None, None),
            (None, crop.pk, None, None),
            (None, None, variety.pk, None),
        }
        assert snapshot.execute(
            "SELECT crop_id, variety_id, area_id, start_year, lifespan, end_year, "
            "quantity, evaluation FROM garden_planting WHERE id=?",
            [planting.pk],
        ).fetchone() == (
            crop.pk,
            variety.pk,
            child.pk,
            2024,
            "persistent",
            2026,
            0,
            "Synthetic cumulative results",
        )
        assert snapshot.execute(
            "SELECT planting_id, kind, planned_date, actual_date, note "
            "FROM garden_plantingevent WHERE id=?",
            [event.pk],
        ).fetchone() == (
            planting.pk,
            "sowing",
            "2023-12-20",
            "2024-01-02",
            "Synthetic overwintered sowing",
        )
        assert snapshot.execute("SELECT COUNT(*) FROM garden_plantingsubmission").fetchone()[0] == 5
        assert snapshot.execute(
            "SELECT variety_id, area_id, quantity FROM garden_planting WHERE id=?", [early_plan.pk]
        ).fetchone() == (None, None, None)
        assert snapshot.execute(
            "SELECT end_year, lifespan FROM garden_planting WHERE id=?", [ongoing.pk]
        ).fetchone() == (None, "persistent")
        assert snapshot.execute(
            "SELECT planting_id, event_id FROM garden_plantingsubmission WHERE key=?",
            [deleted_key.hex],
        ).fetchone() == (None, None)
        owner_rows = snapshot.execute(
            "SELECT entry_id, area_id, variety_id, planting_id FROM core_photo ORDER BY id"
        ).fetchall()
        assert owner_rows == [
            (None, None, variety.pk, None),
            (None, child.pk, None, None),
            (entry.pk, None, None, None),
            (None, None, None, planting.pk),
        ]
    finally:
        snapshot.close()
    for photo in photos:
        assert (restored / "media" / photo.original.name).read_bytes() == private_path(
            photo.original.name
        ).read_bytes()
        for field in (photo.display, photo.thumbnail):
            with Image.open(restored / "media" / field.name) as derivative:
                assert derivative.format == "WEBP" and not derivative.getexif()
    script = """
import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'growbook.settings.development'
import django
django.setup()
from django.contrib.auth import authenticate
from django.test import Client
from core.models import Photo
from diary.models import DiaryEntry
from diary.services import create_entry
from garden.models import Crop, Variety, GrowingArea, Planting, PlantingEvent, PlantingSubmission
from garden.queries import plantings_in_year
from garden.services import create_area, create_crop, create_variety, create_planting, edit_area
from django.core.exceptions import ValidationError
user = authenticate(username='gardener', password='Synthetic-password-42')
assert user is not None
client = Client(HTTP_HOST='localhost')
for photo in Photo.objects.all():
    assert client.get(photo.get_absolute_url()).status_code == 302
    assert client.get(photo.original_url).status_code == 302
client.force_login(user)
for model in (Crop, Variety, GrowingArea, DiaryEntry, Planting):
    for record in model.objects.all():
        assert client.get(record.get_absolute_url()).status_code == 200
for photo in Photo.objects.all():
    assert client.get(photo.get_absolute_url()).status_code == 200
    response = client.get(photo.display_url)
    assert response.status_code == 200 and len(b''.join(response.streaming_content)) > 0
parent = GrowingArea.objects.get(name='Greenhouse')
child = GrowingArea.objects.get(name='Bed')
assert parent.is_archived and not child.is_archived
edit_area(actor=user, pk=child.pk, parent=parent, name=child.name, notes='Restored write',
          expected_updated_at=child.updated_at)
crop = create_crop(actor=user, name='New restored crop').record
create_variety(actor=user, crop=crop, name='New restored variety')
create_area(actor=user, name='New restored bed')
from datetime import date
from django.core.files.uploadedfile import SimpleUploadedFile
from core.media import private_path
entry = DiaryEntry.objects.get()
diary_photo = entry.photos.get()
variety = Variety.objects.get(name='Golden')
replayed_diary = create_entry(
    author=user, date=entry.date, body=entry.body,
    uploads=[SimpleUploadedFile(
        'synthetic.jpg', private_path(diary_photo.original.name).read_bytes()
    )],
    submission_key=entry.submission_key,
    links=[('area', child.pk), ('variety', variety.pk), ('crop', variety.crop_id)],
)
assert not replayed_diary.created and replayed_diary.entry.pk == entry.pk
assert entry.links.count() == 3
from garden.services import create_planting_event, RegisterConflict
planting = Planting.objects.get(label='Synthetic persistent batch')
ongoing = Planting.objects.get(label='Synthetic ongoing batch')
early = Planting.objects.get(label='Synthetic crop-only plan')
assert early.variety_id is None and early.area_id is None and early.quantity is None
assert planting.quantity == 0 and planting.evaluation == 'Synthetic cumulative results'
assert set(plantings_in_year(2026)) == {planting, ongoing}
assert set(plantings_in_year(2027)) == {early, ongoing}
assert list(plantings_in_year(2028)) == [ongoing]
assert PlantingEvent.objects.get().actual_date.isoformat() == '2024-01-02'
assert PlantingSubmission.objects.count() == 5
photo = planting.photos.get()
assert photo.caption == 'Synthetic evaluation caption' and photo.position == 4
replayed = create_planting(
    actor=user, crop=planting.crop_id, variety=planting.variety_id, area=planting.area_id,
    start_year=2024, lifespan='persistent', end_year=2026, quantity=0,
    label='Synthetic persistent batch', evaluation='Synthetic cumulative results',
    uploads=[SimpleUploadedFile('synthetic.webp', private_path(photo.original.name).read_bytes())],
    submission_key=PlantingSubmission.objects.get(planting=planting).key,
)
assert replayed.replayed and replayed.record.pk == planting.pk
replayed_event = create_planting_event(
    actor=user, planting=planting.pk, kind='sowing', planned_date=date(2023, 12, 20),
    actual_date=date(2024, 1, 2), note='Synthetic overwintered sowing',
    submission_key=PlantingSubmission.objects.get(kind='event').key,
)
assert replayed_event.replayed
tombstone = PlantingSubmission.objects.get(planting=None, event=None)
try:
    create_planting(actor=user, crop=planting.crop_id, start_year=2027,
                    label='Synthetic removed plan', submission_key=tombstone.key)
except RegisterConflict:
    pass
else:
    raise AssertionError('deleted planting was recreated after restore')
assert Planting.objects.count() == 3 and Photo.objects.count() == 4
for model in (Planting, Crop, Variety, GrowingArea):
    for record in model.objects.all():
        anonymous = Client(HTTP_HOST='localhost')
        assert anonymous.get(record.get_absolute_url()).status_code == 302
from garden.services import edit_variety
variety = Variety.objects.get(pk=planting.variety_id)
try:
    edit_variety(actor=user, pk=variety.pk, crop=crop, name='Invalid reclassification',
                 expected_updated_at=variety.updated_at)
except ValidationError:
    pass
else:
    raise AssertionError('referenced variety was reclassified after restore')
create_planting(actor=user, crop=crop, start_year=2027, label='Restored early plan')
from django.db import connection
with connection.cursor() as cursor:
    cursor.execute('PRAGMA journal_mode')
    assert cursor.fetchone()[0] == 'wal'
print('clean instance preserved new owners, archives, authorization and validated writes')
"""
    environment = os.environ.copy()
    environment["GROWBOOK_DATA_DIR"] = str(restored)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=django_settings.BASE_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert "clean instance preserved new owners" in completed.stdout
