"""Private category image integrity, retries, migration and clean recovery."""

import io
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import (
    IntegrityError,
    OperationalError,
    close_old_connections,
    connection,
    transaction,
)
from django.db.migrations.executor import MigrationExecutor
from django.urls import reverse
from PIL import Image

from core.backups import create_backup, open_snapshot, restore_backup, verify_backup
from core.media import private_path
from core.models import Photo
from core.writes import validated_write
from garden.models import CycleSubmission, PlantingCategory
from garden.services import RegisterConflict, create_category, create_planting
from garden.services._shared import _planting_fingerprint
from garden.services.cycles import (
    create_planting_category,
    delete_planting_category,
    edit_planting_category,
)

pytestmark = pytest.mark.django_db(transaction=True)


def transparent_upload(format="PNG"):
    buffer = io.BytesIO()
    with Image.new("RGBA", (16, 16), (0, 0, 0, 0)) as source:
        source.putpixel((8, 8), (20, 110, 50, 255))
        source.save(buffer, format=format)
    return SimpleUploadedFile("synthetic-image", buffer.getvalue(), "image/" + format.lower())


@pytest.mark.parametrize("format", ["JPEG", "PNG", "WEBP", "HEIF"])
def test_image_replacement_removal_and_original_receipt_replay(user, image_upload, format):
    category = create_planting_category(
        actor=user, name="Summer", uploads=[image_upload(format=format)]
    ).record
    original = category.photos.get()
    version, key = category.updated_at, uuid4()
    values = dict(
        actor=user, pk=category.pk, name="Flowers", expected_updated_at=version, submission_key=key
    )
    saved = edit_planting_category(**values, uploads=[transparent_upload()]).record
    replacement = saved.photos.get()
    assert replacement.owner == saved and not private_path(original.original.name).exists()
    with Image.open(private_path(replacement.display.name)) as derivative:
        assert derivative.getpixel((0, 0))[3] == 0 and not derivative.getexif()
    assert edit_planting_category(**values, uploads=[transparent_upload()]).replayed
    with pytest.raises(RegisterConflict):
        edit_planting_category(**values, uploads=[image_upload()])
    with pytest.raises(RegisterConflict):
        edit_planting_category(actor=user, pk=saved.pk, name="Stale", expected_updated_at=version)
    removed = edit_planting_category(
        actor=user,
        pk=saved.pk,
        name=saved.name,
        remove_image=True,
        expected_updated_at=saved.updated_at,
    ).record
    assert not removed.photos.exists() and not private_path(replacement.original.name).exists()


def test_name_only_receipts_stay_compatible_and_replay_before_mutable_state(user, image_upload):
    key = uuid4()
    category = create_planting_category(actor=user, name="Summer", submission_key=key).record
    receipt = CycleSubmission.objects.get(pk=key)
    assert receipt.payload_sha256 == _planting_fingerprint(
        "category", None, None, {"name": "Summer"}
    )
    category = edit_planting_category(
        actor=user,
        pk=category.pk,
        name="Renamed",
        uploads=[image_upload()],
        expected_updated_at=category.updated_at,
    ).record
    replay = create_planting_category(actor=user, name="Summer", submission_key=key)
    assert replay.replayed and replay.record.name == "Renamed" and replay.record.photos.count() == 1
    delete_planting_category(actor=user, pk=category.pk, expected_updated_at=category.updated_at)
    with pytest.raises(RegisterConflict):
        create_planting_category(actor=user, name="Summer", submission_key=key)


def test_failed_replacement_rolls_back_and_same_submission_can_retry(
    user, image_upload, monkeypatch
):
    category = create_planting_category(actor=user, name="Summer", uploads=[image_upload()]).record
    original = category.photos.get()
    key = uuid4()
    values = dict(
        actor=user,
        pk=category.pk,
        name="Replacement",
        expected_updated_at=category.updated_at,
        submission_key=key,
    )
    save = CycleSubmission.save

    def fail(self, *args, **kwargs):
        raise OperationalError("Synthetic receipt failure")

    monkeypatch.setattr(CycleSubmission, "save", fail)
    with pytest.raises(OperationalError):
        edit_planting_category(**values, uploads=[transparent_upload()])
    category.refresh_from_db()
    assert category.name == "Summer" and category.photos.get().pk == original.pk
    assert private_path(original.original.name).exists()
    assert len(list(settings.MEDIA_ROOT.glob("photos/*/*/*"))) == 1
    monkeypatch.setattr(CycleSubmission, "save", save)
    assert (
        edit_planting_category(**values, uploads=[transparent_upload()]).record.photos.count() == 1
    )


@pytest.mark.parametrize("failure", ["count", "bytes", "pixels", "invalid"])
def test_image_limits_reject_without_partial_rows(user, image_upload, settings, failure):
    uploads = [image_upload()]
    if failure == "count":
        uploads *= 2
    elif failure == "bytes":
        settings.PHOTO_MAX_FILE_BYTES = 10
    elif failure == "pixels":
        settings.PHOTO_MAX_PIXELS = 10
    else:
        uploads = [SimpleUploadedFile("bad.png", b"invalid image", "image/png")]
    with pytest.raises(ValidationError):
        create_planting_category(actor=user, name="Failed", uploads=uploads)
    assert not PlantingCategory.objects.exists() and not Photo.objects.exists()


def test_overlapping_image_edits_reject_stale_write_and_keep_one_bundle(user, image_upload):
    category = create_planting_category(actor=user, name="Summer", uploads=[image_upload()]).record
    barrier = Barrier(2)

    def save(name):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return edit_planting_category(
                actor=user,
                pk=category.pk,
                name=name,
                uploads=[transparent_upload()],
                expected_updated_at=category.updated_at,
            )
        except RegisterConflict:
            return None
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["First", "Second"]))
    assert sum(result is not None for result in results) == 1
    assert category.photos.count() == 1
    assert len(list(settings.MEDIA_ROOT.glob("photos/*/*/*"))) == 1


def test_category_images_authorized_and_reused_on_card_heading(client, user):
    category = create_planting_category(
        actor=user, name="Summer", uploads=[transparent_upload("WEBP")]
    ).record
    photo = category.photos.get()
    for url in (
        category.get_absolute_url(),
        reverse("garden:planting-category-manage"),
        photo.get_absolute_url(),
        photo.display_url,
        photo.thumbnail_url,
        photo.original_url,
    ):
        assert client.get(url).status_code == 302
    client.force_login(user)
    for query in ({"year": 2025}, {"all_years": "1"}):
        page = client.get(reverse("garden:planting-list"), query)
        cards = page.context["category_cards"]
        assert [card["url"].split("?")[0] for card in cards[:3]] == [
            reverse("garden:planting-status", args=[value])
            for value in ("planned", "ongoing", "completed")
        ]
        assert cards[3]["image"] == photo and photo.display_url in page.text
    assert photo.display_url in client.get(category.get_absolute_url()).text
    assert client.get(photo.original_url)["Content-Disposition"].startswith("attachment;")


def test_database_requires_exactly_one_owner_and_one_category_image(user, image_upload):
    category = create_planting_category(actor=user, name="Summer", uploads=[image_upload()]).record
    other = create_planting_category(actor=user, name="Other").record
    crop_category = create_category(actor=user, name="Crop category").record
    photo = category.photos.get()
    values = {
        field.name: getattr(photo, field.name)
        for field in Photo._meta.fields
        if field.name not in ("id", "uploaded_at")
    }
    for changes in (
        {},
        {"planting_category": None},
        {"planting_category": other, "category": crop_category},
    ):
        with pytest.raises(IntegrityError), transaction.atomic(), validated_write():
            Photo.objects.create(**{**values, **changes})


def test_migration_preserves_existing_photo_ownership(user, image_upload):
    from garden.services import create_area

    area = create_area(actor=user, name="Bed", uploads=[image_upload()]).record
    photo = area.photos.get()
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    before = [("core", "0005_remove_photo_photo_exactly_one_owner_photo_category_and_more")]
    try:
        executor.migrate(before)
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        photo.refresh_from_db()
        assert photo.area_id == area.pk and photo.planting_category_id is None
        assert private_path(photo.original.name).exists()
    finally:
        MigrationExecutor(connection).migrate(latest)


def test_clean_restore_preserves_membership_original_transparency_and_new_writes(
    user, planting_catalog, tmp_path
):
    crop, variety, _, area = planting_catalog
    category = create_planting_category(
        actor=user, name="Summer", uploads=[transparent_upload()]
    ).record
    planting = create_planting(
        actor=user,
        crop=crop,
        variety=variety,
        area=area,
        start_year=2025,
        category_ids=[category.pk],
    ).record
    photo = category.photos.get()
    config = tmp_path / "operator.env"
    config.write_text("GROWBOOK_SECRET_KEY=" + "synthetic-key-" * 5)
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    create_backup(backup, [config])
    verify_backup(backup)
    restore_backup(backup, restored)
    with open_snapshot(restored / "db.sqlite3") as snapshot:
        assert snapshot.execute(
            "SELECT category_id, cycle_id FROM garden_plantingcategorylink"
        ).fetchone() == (category.pk, planting.cycle_id.hex)
        assert snapshot.execute(
            "SELECT planting_category_id, original_sha256 FROM core_photo"
        ).fetchone() == (category.pk, photo.original_sha256)
    assert (restored / "media" / photo.original.name).read_bytes() == private_path(
        photo.original.name
    ).read_bytes()
    with Image.open(restored / "media" / photo.display.name) as derivative:
        assert derivative.getpixel((0, 0))[3] == 0 and not derivative.getexif()
    script = """
import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'growbook.settings.development'
import django
django.setup()
from core.models import User
from garden.models import PlantingCategory
from garden.services.cycles import edit_planting_category
category = PlantingCategory.objects.get(name='Summer')
result = edit_planting_category(
    actor=User.objects.get(username='gardener'), pk=category.pk,
    name='Restored', expected_updated_at=category.updated_at,
)
assert result.record.photos.count() == 1
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=settings.BASE_DIR,
        env={**os.environ, "GROWBOOK_DATA_DIR": str(restored)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    delete_planting_category(actor=user, pk=category.pk, expected_updated_at=category.updated_at)
    planting.refresh_from_db()
    assert not planting.cycle.categories.exists() and not Photo.objects.filter(pk=photo.pk).exists()
    assert not private_path(photo.original.name).exists()
