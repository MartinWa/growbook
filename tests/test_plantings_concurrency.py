import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection

from core.backups import create_backup, verify_backup
from core.cleanup import cleanup_orphans
from core.models import Photo, User
from garden.models import Planting, PlantingEvent, Variety
from garden.services import (
    RegisterConflict,
    archive_crop,
    create_crop,
    create_planting,
    create_planting_event,
    create_variety,
    edit_planting,
    edit_variety,
)

pytestmark = pytest.mark.django_db(transaction=True)


def worker(callback):
    close_old_connections()
    try:
        return callback()
    finally:
        connection.close()


@pytest.fixture
def catalog(user):
    crop = create_crop(actor=user, name="Tomato").record
    other = create_crop(actor=user, name="Bean").record
    variety = create_variety(actor=user, crop=crop, name="Golden").record
    return crop, other, variety


@pytest.mark.parametrize("first_writer", ["planting", "taxonomy"])
def test_variety_reclassification_and_planting_creation_serialize_both_orderings(
    user, catalog, monkeypatch, first_writer
):
    from garden.services import plantings, registers

    crop, other, variety = catalog
    locked, release, started = threading.Event(), threading.Event(), threading.Event()
    validate = registers._validate
    validate_planting = plantings._validate_planting

    def pause_taxonomy(record, previous_parent_id=None):
        result = validate(record, previous_parent_id)
        if isinstance(record, Variety) and record.crop_id == other.pk:
            locked.set()
            assert release.wait(timeout=10)
        return result

    def pause_planting(record, previous):
        result = validate_planting(record, previous)
        locked.set()
        assert release.wait(timeout=10)
        return result

    def planting():
        return create_planting(
            actor=User.objects.get(pk=user.pk), crop=crop.pk, variety=variety.pk, start_year=2026
        )

    def taxonomy():
        return edit_variety(
            actor=User.objects.get(pk=user.pk),
            pk=variety.pk,
            crop=other.pk,
            name=variety.name,
            expected_updated_at=variety.updated_at,
        )

    first, second = (planting, taxonomy) if first_writer == "planting" else (taxonomy, planting)
    monkeypatch.setattr(
        plantings if first_writer == "planting" else registers,
        "_validate_planting" if first_writer == "planting" else "_validate",
        pause_planting if first_writer == "planting" else pause_taxonomy,
    )

    def blocked():
        started.set()
        with pytest.raises(ValidationError):
            second()

    with ThreadPoolExecutor(max_workers=2) as executor:
        accepted = executor.submit(worker, first)
        assert locked.wait(timeout=10)
        rejected = executor.submit(worker, blocked)
        assert started.wait(timeout=10) and not rejected.done()
        release.set()
        accepted.result(timeout=15)
        rejected.result(timeout=15)
    variety.refresh_from_db()
    if first_writer == "planting":
        assert variety.crop_id == crop.pk and Planting.objects.get().crop_id == crop.pk
    else:
        assert variety.crop_id == other.pk and not Planting.objects.exists()


def test_taxonomy_changes_during_image_processing_are_revalidated_without_files(
    user, catalog, image_upload, monkeypatch, settings
):
    from core import photo_storage

    crop, other, variety = catalog
    real_process = photo_storage.process_staged

    def reclassify(photo):
        assert not connection.in_atomic_block
        real_process(photo)
        edit_variety(
            actor=user,
            pk=variety.pk,
            crop=other.pk,
            name=variety.name,
            expected_updated_at=variety.updated_at,
        )

    monkeypatch.setattr(photo_storage, "process_staged", reclassify)
    with pytest.raises(ValidationError):
        create_planting(
            actor=user, crop=crop, variety=variety, start_year=2026, uploads=[image_upload()]
        )
    assert not Planting.objects.exists() and not Photo.objects.exists()
    assert not list(settings.MEDIA_ROOT.glob("photos/*/*/*"))


def test_planting_creation_waits_for_archive_then_rejects_new_association(
    user, catalog, monkeypatch
):
    from garden.services import registers

    crop, _, _ = catalog
    locked, release, started = threading.Event(), threading.Event(), threading.Event()
    validate = registers._validate

    def pause_archive(record, previous_parent_id=None):
        result = validate(record, previous_parent_id)
        if record.pk == crop.pk and record.is_archived:
            locked.set()
            assert release.wait(timeout=10)
        return result

    monkeypatch.setattr(registers, "_validate", pause_archive)

    def create():
        started.set()
        with pytest.raises(ValidationError):
            create_planting(actor=User.objects.get(pk=user.pk), crop=crop.pk, start_year=2026)

    with ThreadPoolExecutor(max_workers=2) as executor:
        archive = executor.submit(worker, lambda: archive_crop(actor=user, pk=crop.pk))
        assert locked.wait(timeout=10)
        save = executor.submit(worker, create)
        assert started.wait(timeout=10) and not save.done()
        release.set()
        archive.result(timeout=15)
        save.result(timeout=15)
    assert not Planting.objects.exists()


@pytest.mark.parametrize("with_photos", [False, True])
def test_simultaneous_identical_save_key_yields_one_planting(
    user, catalog, image_upload, with_photos
):
    crop, _, _ = catalog
    key, start = uuid4(), threading.Barrier(2)
    image = image_upload().read()

    def save():
        start.wait(timeout=10)
        return create_planting(
            actor=User.objects.get(pk=user.pk),
            crop=crop.pk,
            start_year=2026,
            submission_key=key,
            uploads=[SimpleUploadedFile("synthetic.jpg", image)] if with_photos else [],
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker, save) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert results[0].record.pk == results[1].record.pk
    assert sorted(result.replayed for result in results) == [False, True]
    assert Planting.objects.count() == 1 and Photo.objects.count() == int(with_photos)


def test_distinct_keys_preserve_simultaneous_identical_batches(user, catalog):
    crop, _, _ = catalog
    start = threading.Barrier(2)

    def save():
        start.wait(timeout=10)
        return create_planting(actor=User.objects.get(pk=user.pk), crop=crop.pk, start_year=2026)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker, save) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert results[0].record.pk != results[1].record.pk and Planting.objects.count() == 2


def test_overlapping_edits_reject_stale_version_instead_of_lost_update(user, catalog):
    crop, _, _ = catalog
    record = create_planting(actor=user, crop=crop, start_year=2026).record
    start = threading.Barrier(2)

    def save(label):
        start.wait(timeout=10)
        try:
            return edit_planting(
                actor=User.objects.get(pk=user.pk),
                pk=record.pk,
                crop=crop.pk,
                start_year=2026,
                label=label,
                expected_updated_at=record.updated_at,
            )
        except RegisterConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(worker, lambda label=label: save(label))
            for label in ("First", "Second")
        ]
        results = [future.result(timeout=15) for future in futures]
    assert sum(result is not None for result in results) == 1
    record.refresh_from_db()
    assert record.label in {"First", "Second"}


def test_event_receipt_serializes_simultaneous_retries(user, catalog):
    crop, _, _ = catalog
    record = create_planting(actor=user, crop=crop, start_year=2026).record
    key, start = uuid4(), threading.Barrier(2)

    def save():
        start.wait(timeout=10)
        return create_planting_event(
            actor=User.objects.get(pk=user.pk),
            planting=record.pk,
            kind="sowing",
            planned_date=date(2025, 12, 20),
            submission_key=key,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker, save) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert results[0].record.pk == results[1].record.pk
    assert PlantingEvent.objects.count() == 1
    assert sorted(result.replayed for result in results) == [False, True]


def test_backup_and_cleanup_wait_for_inflight_planting_photos(
    user, catalog, image_upload, tmp_path, monkeypatch
):
    from core import photo_storage

    crop, _, _ = catalog
    image = image_upload().read()
    published, release = threading.Event(), threading.Event()
    real_publish = photo_storage.publish_staged
    config = tmp_path / "synthetic.env"
    config.write_text("Synthetic recovery configuration", encoding="utf-8")

    def paused_publish(photo):
        result = real_publish(photo)
        published.set()
        assert release.wait(timeout=10)
        return result

    monkeypatch.setattr(photo_storage, "publish_staged", paused_publish)

    def create():
        return create_planting(
            actor=User.objects.get(pk=user.pk),
            crop=crop.pk,
            start_year=2026,
            uploads=[SimpleUploadedFile("synthetic.jpg", image)],
        )

    with ThreadPoolExecutor(max_workers=3) as executor:
        save = executor.submit(worker, create)
        assert published.wait(timeout=10)
        backup = executor.submit(worker, lambda: create_backup(tmp_path / "backup", [config]))
        cleanup = executor.submit(worker, lambda: cleanup_orphans(apply=True, minimum_age=0))
        assert not backup.done() and not cleanup.done()
        release.set()
        record = save.result(timeout=15).record
        backup.result(timeout=15)
        assert cleanup.result(timeout=15) == []
    assert record.photos.count() == 1 and verify_backup(tmp_path / "backup")
