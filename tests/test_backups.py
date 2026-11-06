import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from django.conf import settings as django_settings
from django.contrib.auth.hashers import PBKDF2PasswordHasher
from django.db import connection
from PIL import Image

from core.backups import BackupError, create_backup, open_snapshot, restore_backup, verify_backup
from core.cleanup import cleanup_orphans
from core.media import private_path
from diary.models import DiaryEntry
from diary.services import create_entry, update_entry

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def recovery_config(tmp_path):
    path = tmp_path / "operator.env"
    path.write_text("GROWBOOK_SECRET_KEY=" + "synthetic-backup-key-" * 4 + "\n", encoding="utf-8")
    return path


def capture(user, uploads=(), body="Synthetic backup note"):
    return create_entry(
        author=user, date=date(2026, 5, 9), body=body, uploads=uploads, submission_key=uuid4()
    ).entry


def test_consistent_backup_and_clean_restore(
    user, image_upload, apple_hdr_upload, tmp_path, recovery_config
):
    entry = capture(user, [image_upload(format="HEIF"), image_upload(), apple_hdr_upload()])
    original_paths = {photo.original.name: photo.original_sha256 for photo in entry.photos.all()}
    backup = tmp_path / "complete-backup"
    manifest = create_backup(backup, [recovery_config])
    assert manifest["derivatives"] == "regenerate"
    assert len(manifest["files"]) == 5
    assert all(not item["path"].endswith(".webp") for item in manifest["files"])
    assert verify_backup(backup) == manifest
    restored = tmp_path / "clean-instance"
    assert restore_backup(backup, restored) == manifest
    snapshot = open_snapshot(restored / "db.sqlite3")
    try:
        assert snapshot.execute("SELECT body, author_id FROM diary_diaryentry").fetchone() == (
            entry.body,
            user.pk,
        )
        password = snapshot.execute(
            "SELECT password FROM core_user WHERE id=?", [user.pk]
        ).fetchone()[0]
        assert password == user.password
        assert (
            snapshot.execute("SELECT entry_id FROM diary_diarysubmission").fetchone()[0] == entry.pk
        )
        photos = snapshot.execute(
            "SELECT original, original_sha256, display, thumbnail FROM core_photo"
        ).fetchall()
    finally:
        snapshot.close()
    assert dict((row[0], row[1]) for row in photos) == original_paths
    for original, _, display, thumbnail in photos:
        assert (restored / "media" / original).read_bytes() == private_path(original).read_bytes()
        for relative in (display, thumbnail):
            with Image.open(restored / "media" / relative) as image:
                assert image.format == "WEBP"
                assert not image.getexif()
    assert (
        restored / "recovered-configuration" / "001-operator.env"
    ).read_bytes() == recovery_config.read_bytes()


def test_restored_instance_authenticates_and_reads_photos_in_fresh_process(
    user,
    image_upload,
    tmp_path,
    recovery_config,
):
    hasher = PBKDF2PasswordHasher()
    user.password = hasher.encode("Synthetic-password-42", hasher.salt())
    user.save(update_fields=["password"])
    entry = capture(user, [image_upload()])
    edit_payload = {
        "pk": entry.pk,
        "date": entry.date.isoformat(),
        "body": "Edited synthetic recovery note",
        "submission_key": str(uuid4()),
        "expected_updated_at": entry.updated_at.isoformat(),
    }
    update_entry(
        **{**edit_payload, "date": entry.date, "expected_updated_at": entry.updated_at},
        actor=user,
    )
    backup = tmp_path / "backup"
    restored = tmp_path / "new-instance"
    create_backup(backup, [recovery_config])
    restore_backup(backup, restored)
    script = """
import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'growbook.settings.development'
import django
django.setup()
from django.test import Client
from core.models import Photo
from diary.models import DiaryEditSubmission, DiaryEntry
from diary.services import update_entry
from django.utils.dateparse import parse_date, parse_datetime
from django.contrib.auth import authenticate
user = authenticate(username='gardener', password='Synthetic-password-42')
assert user is not None
client = Client(HTTP_HOST='localhost')
client.force_login(user)
entry = DiaryEntry.objects.get()
before = entry.updated_at
assert DiaryEditSubmission.objects.get(key=edit_payload['submission_key']).entry_id == entry.pk
retry = update_entry(
    **{**edit_payload, 'date': parse_date(edit_payload['date']),
       'expected_updated_at': parse_datetime(edit_payload['expected_updated_at'])}, actor=user,
)
assert retry.updated_at == before and retry.body == 'Edited synthetic recovery note'
assert retry.photos.count() == 1 and DiaryEditSubmission.objects.count() == 1
assert client.get(entry.get_absolute_url()).status_code == 200
response = client.get(Photo.objects.get().display_url)
assert response.status_code == 200
assert len(b''.join(response.streaming_content)) > 0
print('clean instance authenticated and rendered original relationships/photos')
"""
    script = "edit_payload = " + repr(edit_payload) + "\n" + script
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
    assert "clean instance authenticated" in completed.stdout
    assert entry.pk > 0


def test_backup_uses_sqlite_api_not_a_live_database_copy(
    user, tmp_path, recovery_config, monkeypatch
):
    from core import backups

    capture(user)
    real_copy = backups.copy_private

    def inspect_copy(source, destination):
        assert Path(source) != Path(connection.settings_dict["NAME"])
        return real_copy(source, destination)

    monkeypatch.setattr(backups, "copy_private", inspect_copy)
    create_backup(tmp_path / "backup", [recovery_config])


def test_snapshot_and_file_copy_happen_outside_write_transaction(
    user,
    image_upload,
    tmp_path,
    recovery_config,
    monkeypatch,
):
    from core import backups

    capture(user, [image_upload()])
    real_copy = backups.copy_private

    def inspect_copy(source, destination):
        assert not connection.in_atomic_block
        return real_copy(source, destination)

    monkeypatch.setattr(backups, "copy_private", inspect_copy)
    create_backup(tmp_path / "backup", [recovery_config])


@pytest.mark.parametrize("target", ["database", "original", "configuration"])
def test_corrupt_backup_is_rejected_before_restore(
    user, image_upload, tmp_path, recovery_config, target
):
    capture(user, [image_upload()])
    backup = tmp_path / "backup"
    manifest = create_backup(backup, [recovery_config])
    relative = next(item["path"] for item in manifest["files"] if item["kind"] == target)
    with (backup / relative).open("ab") as stream:
        stream.write(b"synthetic corruption")
    with pytest.raises(BackupError):
        verify_backup(backup)
    destination = tmp_path / "restored"
    with pytest.raises(BackupError):
        restore_backup(backup, destination)
    assert not destination.exists()


def test_missing_original_prevents_backup_publication(
    user, image_upload, tmp_path, recovery_config
):
    photo = capture(user, [image_upload()]).photos.get()
    private_path(photo.original.name).unlink()
    destination = tmp_path / "backup"
    with pytest.raises(BackupError):
        create_backup(destination, [recovery_config])
    assert not destination.exists()
    assert not list(tmp_path.glob(".backup-pending-*"))


def test_existing_backup_or_instance_is_never_overwritten(user, tmp_path, recovery_config):
    capture(user)
    backup = tmp_path / "backup"
    create_backup(backup, [recovery_config])
    with pytest.raises(BackupError):
        create_backup(backup, [recovery_config])
    destination = tmp_path / "existing"
    destination.mkdir()
    marker = destination / "keep.txt"
    marker.write_text("Do not overwrite", encoding="utf-8")
    with pytest.raises(BackupError):
        restore_backup(backup, destination)
    assert marker.read_text(encoding="utf-8") == "Do not overwrite"


def test_backup_cannot_be_published_inside_live_media(settings, recovery_config):
    with pytest.raises(BackupError, match="outside live media"):
        create_backup(settings.MEDIA_ROOT / "photos" / "2000" / "01" / "backup", [recovery_config])


@pytest.mark.parametrize("item", [None, {}, {"path": None}, {"bytes": True}])
def test_malformed_manifest_item_is_reported_as_backup_error(user, tmp_path, recovery_config, item):
    capture(user)
    backup = tmp_path / "backup"
    create_backup(backup, [recovery_config])
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0] = item
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BackupError):
        verify_backup(backup)


def test_restore_cannot_modify_completed_backup_directory(user, tmp_path, recovery_config):
    capture(user)
    backup = tmp_path / "backup"
    create_backup(backup, [recovery_config])
    with pytest.raises(BackupError, match="outside"):
        restore_backup(backup, backup / "new-instance")
    verify_backup(backup)


def test_configuration_is_required(tmp_path):
    with pytest.raises(BackupError):
        create_backup(tmp_path / "backup", [])


def test_manifest_path_traversal_is_rejected(user, tmp_path, recovery_config):
    capture(user)
    backup = tmp_path / "backup"
    create_backup(backup, [recovery_config])
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "../escape.sqlite3"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BackupError):
        verify_backup(backup)


def test_orphan_cleanup_is_bounded_dry_run_by_default_and_keeps_references(
    user,
    image_upload,
    settings,
):
    photo = capture(user, [image_upload()]).photos.get()
    root = settings.MEDIA_ROOT
    orphan = root / "photos" / "2000" / "01" / "synthetic-orphan"
    orphan.mkdir(parents=True)
    (orphan / "original.jpg").write_bytes(b"orphan")
    os.utime(orphan, (0, 0))
    abandoned = root / ".staging" / "capture-abandoned"
    abandoned.mkdir()
    os.utime(abandoned, (0, 0))
    candidates = cleanup_orphans(minimum_age=1)
    assert len(candidates) == 2
    assert orphan.exists() and abandoned.exists()
    assert len(cleanup_orphans(apply=True, minimum_age=1, limit=1)) == 1
    assert len(cleanup_orphans(apply=True, minimum_age=1)) == 1
    assert not orphan.exists() and not abandoned.exists()
    assert private_path(photo.original.name).exists()
    assert private_path(photo.display.name).exists()
    assert DiaryEntry.objects.get().photos.count() == 1
