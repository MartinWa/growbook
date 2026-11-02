import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, transaction

from core.backups import create_backup, verify_backup
from core.cleanup import cleanup_orphans
from core.models import Photo, User
from garden.models import Crop, GrowingArea
from garden.services import (
    archive_crop,
    create_area,
    create_crop,
    create_variety,
    delete_area,
    edit_area,
)

pytestmark = pytest.mark.django_db(transaction=True)


def worker(callback):
    close_old_connections()
    try:
        return callback()
    finally:
        connection.close()


def test_simultaneous_unicode_crop_creates_save_only_one_name(user):
    start = threading.Barrier(2)

    def create(name):
        start.wait(timeout=10)
        try:
            return create_crop(actor=User.objects.get(pk=user.pk), name=name)
        except ValidationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(worker, lambda name=name: create(name))
            for name in ("Årt", " A\u030aRT ")
        ]
        results = [future.result(timeout=15) for future in futures]
    assert sum(result is not None for result in results) == 1
    assert Crop.objects.count() == 1


def test_simultaneous_reparenting_cannot_make_two_record_cycle(user):
    first = create_area(actor=user, name="First").record
    second = create_area(actor=user, name="Second").record
    start = threading.Barrier(2)

    def reparent(record, parent):
        start.wait(timeout=10)
        try:
            return edit_area(
                actor=User.objects.get(pk=user.pk), pk=record.pk, name=record.name, parent=parent.pk
            )
        except ValidationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(worker, lambda: reparent(first, second)),
            executor.submit(worker, lambda: reparent(second, first)),
        ]
        results = [future.result(timeout=15) for future in futures]
    assert sum(result is not None for result in results) == 1
    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.parent_id is None) != (second.parent_id is None)
    assert not (first.parent_id == second.pk and second.parent_id == first.pk)


def test_simultaneous_reparenting_into_same_scope_rechecks_unique_name(user):
    parent = create_area(actor=user, name="Parent").record
    first = create_area(actor=user, name="Bed").record
    other = create_area(actor=user, name="Other").record
    second = create_area(actor=user, name="BED", parent=other).record
    start = threading.Barrier(2)

    def move(record):
        start.wait(timeout=10)
        try:
            return edit_area(
                actor=User.objects.get(pk=user.pk), pk=record.pk, name=record.name, parent=parent.pk
            )
        except ValidationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(worker, lambda record=record: move(record))
            for record in (first, second)
        ]
        results = [future.result(timeout=15) for future in futures]
    assert sum(result is not None for result in results) == 1
    assert parent.children.count() == 1


def test_variety_creation_waits_for_crop_archive_then_revalidates(user, monkeypatch):
    from garden import services

    crop = create_crop(actor=user, name="Tomato").record
    acquired, release, started = threading.Event(), threading.Event(), threading.Event()
    real_validate = services._validate

    def pause_archive(record, previous_parent_id=None):
        result = real_validate(record, previous_parent_id)
        if isinstance(record, Crop) and record.is_archived:
            acquired.set()
            assert release.wait(timeout=10)
        return result

    monkeypatch.setattr(services, "_validate", pause_archive)

    def add_variety():
        started.set()
        with pytest.raises(ValidationError):
            create_variety(actor=User.objects.get(pk=user.pk), crop=crop.pk, name="Golden")

    with ThreadPoolExecutor(max_workers=2) as executor:
        archive = executor.submit(worker, lambda: archive_crop(actor=user, pk=crop.pk))
        assert acquired.wait(timeout=10)
        create = executor.submit(worker, add_variety)
        assert started.wait(timeout=10)
        assert not create.done()
        release.set()
        archive.result(timeout=15)
        create.result(timeout=15)
    assert crop.varieties.count() == 0


def test_delete_waits_for_child_commit_and_protects_new_reference(user):
    parent = create_area(actor=user, name="Parent").record
    locked, release, started = threading.Event(), threading.Event(), threading.Event()

    def create_child():
        # Hold the writer deliberately; the normal create operation itself remains short.
        from core.writes import validated_write

        with transaction.atomic(), validated_write():
            child = GrowingArea(parent_id=parent.pk, name="Child", name_key="child")
            child.full_clean()
            child.save()
            locked.set()
            assert release.wait(timeout=10)

    def remove_parent():
        started.set()
        with pytest.raises(ValidationError):
            delete_area(actor=User.objects.get(pk=user.pk), pk=parent.pk)

    with ThreadPoolExecutor(max_workers=2) as executor:
        create = executor.submit(worker, create_child)
        assert locked.wait(timeout=10)
        delete = executor.submit(worker, remove_parent)
        assert started.wait(timeout=10)
        assert not delete.done()
        release.set()
        create.result(timeout=15)
        delete.result(timeout=15)
    assert parent.children.count() == 1


def test_simultaneous_register_photo_creation_retry_makes_one_record(user, image_upload):
    key, start = uuid4(), threading.Barrier(2)
    data = image_upload().read()

    def create():
        from django.core.files.uploadedfile import SimpleUploadedFile

        start.wait(timeout=10)
        return create_area(
            actor=User.objects.get(pk=user.pk),
            name="Same bed",
            uploads=[SimpleUploadedFile("synthetic.jpg", data)],
            submission_key=key,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result(timeout=15)
            for future in [executor.submit(worker, create), executor.submit(worker, create)]
        ]
    assert results[0].record.pk == results[1].record.pk
    assert sorted(result.replayed for result in results) == [False, True]
    assert GrowingArea.objects.count() == 1 and Photo.objects.count() == 1


def test_backup_and_cleanup_wait_for_inflight_register_photo_write(
    user, image_upload, settings, tmp_path, monkeypatch
):
    from core import photo_storage

    data = image_upload().read()
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
        from django.core.files.uploadedfile import SimpleUploadedFile

        return create_area(
            actor=User.objects.get(pk=user.pk),
            name="Inflight bed",
            uploads=[SimpleUploadedFile("synthetic.jpg", data)],
        )

    with ThreadPoolExecutor(max_workers=3) as executor:
        save = executor.submit(worker, create)
        assert published.wait(timeout=10)
        backup = executor.submit(worker, lambda: create_backup(tmp_path / "backup", [config]))
        cleanup = executor.submit(worker, lambda: cleanup_orphans(apply=True, minimum_age=0))
        assert not backup.done() and not cleanup.done()
        release.set()
        result = save.result(timeout=15)
        backup.result(timeout=15)
        assert cleanup.result(timeout=15) == []
    assert result.record.photos.count() == 1
    assert verify_backup(tmp_path / "backup")
    assert len(list(settings.MEDIA_ROOT.glob("photos/*/*/*/original.jpg"))) == 1
