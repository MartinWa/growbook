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
    create_variety,
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
    ).entry
    archive_crop(actor=user, pk=crop.pk)
    archive_variety(actor=user, pk=variety.pk)
    archive_area(actor=user, pk=parent.pk)
    photos = [variety.photos.get(), child.photos.get(), entry.photos.get()]
    backup, restored = tmp_path / "backup", tmp_path / "clean-instance"
    manifest = create_backup(backup, [config])
    assert verify_backup(backup) == manifest
    assert len(manifest["files"]) == 5
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
        owner_rows = snapshot.execute(
            "SELECT entry_id, area_id, variety_id FROM core_photo ORDER BY id"
        ).fetchall()
        assert owner_rows == [
            (None, None, variety.pk),
            (None, child.pk, None),
            (entry.pk, None, None),
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
from garden.models import Crop, Variety, GrowingArea
from garden.services import create_area, create_crop, create_variety, edit_area
user = authenticate(username='gardener', password='Synthetic-password-42')
assert user is not None
client = Client(HTTP_HOST='localhost')
for photo in Photo.objects.all():
    assert client.get(photo.get_absolute_url()).status_code == 302
    assert client.get(photo.original_url).status_code == 302
client.force_login(user)
for model in (Crop, Variety, GrowingArea, DiaryEntry):
    for record in model.objects.all():
        assert client.get(record.get_absolute_url()).status_code == 200
for photo in Photo.objects.all():
    assert client.get(photo.get_absolute_url()).status_code == 200
    response = client.get(photo.display_url)
    assert response.status_code == 200 and len(b''.join(response.streaming_content)) > 0
parent = GrowingArea.objects.get(name='Greenhouse')
child = GrowingArea.objects.get(name='Bed')
assert parent.is_archived and not child.is_archived
edit_area(actor=user, pk=child.pk, parent=parent, name=child.name, notes='Restored write')
crop = create_crop(actor=user, name='New restored crop').record
create_variety(actor=user, crop=crop, name='New restored variety')
create_area(actor=user, name='New restored bed')
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
