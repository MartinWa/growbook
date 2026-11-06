"""Diary edit retries acknowledge immutable submissions without applying them twice."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from uuid import uuid4

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError, close_old_connections, connection

from core import photo_storage
from core.cleanup import cleanup_orphans
from core.media import private_path
from diary.models import DiaryEditSubmission, DiaryEntry, DiarySubmission
from diary.services import EntryConflict, create_entry, delete_entry, update_entry
from garden.services import archive_crop, create_area, create_crop, delete_crop

pytestmark = pytest.mark.django_db(transaction=True)


def make_entry(user, **values):
    return create_entry(
        author=user,
        date=date(2026, 5, 9),
        body=values.pop("body", "Synthetic observation"),
        uploads=values.pop("uploads", ()),
        submission_key=uuid4(),
        **values,
    ).entry


def payload(record, user, **values):
    return {
        "actor": user,
        "pk": record.pk,
        "date": record.date,
        "body": "Edited synthetic note",
        "expected_updated_at": record.updated_at,
        "submission_key": uuid4(),
        **values,
    }


@pytest.mark.parametrize("photo_count", [0, 1, 3])
def test_identical_edit_retry_preserves_version_photos_and_creation_receipt(
    user, image_upload, monkeypatch, photo_count
):
    record = make_entry(user)
    creation = DiarySubmission.objects.get(entry=record)
    values = payload(record, user, body="" if photo_count else "Edited note")
    first = update_entry(**values, uploads=[image_upload() for _ in range(photo_count)])
    before = list(first.photos.values_list("pk", "original", "position"))

    def must_not_decode(*args):
        raise AssertionError("An acknowledged retry must not decode or publish photos again.")

    monkeypatch.setattr(photo_storage, "process_staged", must_not_decode)
    retry = update_entry(**values, uploads=[image_upload() for _ in range(photo_count)])
    assert retry.pk == first.pk and retry.updated_at == first.updated_at
    assert list(retry.photos.values_list("pk", "original", "position")) == before
    assert DiaryEditSubmission.objects.count() == 1
    creation.refresh_from_db()
    assert creation.key == record.submission_key and creation.entry_id == record.pk
    assert cleanup_orphans(apply=True, minimum_age=0) == []


def test_retry_precedes_removed_photos_upload_limit_and_current_links(user, image_upload):
    crop = create_crop(actor=user, name="Synthetic crop").record
    area = create_area(actor=user, name="Synthetic area").record
    record = make_entry(user, uploads=[image_upload() for _ in range(10)])
    removed = list(record.photos.values_list("pk", flat=True))[:2]
    values = payload(record, user, links=[crop, area], remove_photo_ids=removed)
    first = update_entry(**values, uploads=[image_upload(), image_upload()])
    later = update_entry(**payload(first, user, links=(), body="Later independent change"))
    delete_crop(actor=user, pk=crop.pk, expected_updated_at=crop.updated_at)
    before = list(later.photos.values_list("pk", "original"))
    retry = update_entry(
        **{
            **values,
            "links": [("area", area.pk), ("crop", crop.pk)],
            "remove_photo_ids": reversed(removed),
        },
        uploads=[image_upload(), image_upload()],
    )
    assert retry.body == later.body and retry.updated_at == later.updated_at
    assert not retry.links.exists()
    assert list(retry.photos.values_list("pk", "original")) == before
    assert DiaryEditSubmission.objects.count() == 2
    assert cleanup_orphans(apply=True, minimum_age=0) == []


@pytest.mark.parametrize(
    "changed", ["body", "date", "version", "target", "links", "removals", "photos"]
)
def test_key_reuse_with_changed_payload_conflicts(user, image_upload, changed):
    record = make_entry(user)
    values = payload(record, user)
    first = update_entry(**values)
    overrides = {
        "body": {"body": "Changed retry"},
        "date": {"date": record.date + timedelta(days=1)},
        "version": {"expected_updated_at": first.updated_at},
        "target": {"pk": make_entry(user).pk},
        "links": {"links": [("crop", 999999)]},
        "removals": {"remove_photo_ids": [999999]},
        "photos": {"uploads": [image_upload()]},
    }
    with pytest.raises(EntryConflict):
        update_entry(**{**values, **overrides[changed]})
    first.refresh_from_db()
    assert first.body == values["body"] and not first.photos.exists()
    assert DiaryEditSubmission.objects.count() == 1


def test_new_key_with_stale_version_is_still_rejected(user):
    record = make_entry(user)
    values = payload(record, user)
    first = update_entry(**values)
    with pytest.raises(EntryConflict):
        update_entry(**{**values, "submission_key": uuid4()})
    first.refresh_from_db()
    assert first.body == values["body"] and DiaryEditSubmission.objects.count() == 1


def test_new_submission_still_validates_associations_and_removals(user):
    crop = create_crop(actor=user, name="Synthetic archived crop").record
    archive_crop(actor=user, pk=crop.pk)
    record = make_entry(user)
    for values in ({"links": [crop]}, {"remove_photo_ids": [999999]}, {"body": ""}):
        with pytest.raises(ValidationError):
            update_entry(**payload(record, user, **values))
    assert not DiaryEditSubmission.objects.exists()
    record.refresh_from_db()
    assert record.body == "Synthetic observation"


@pytest.mark.parametrize("invalid_key", ["malformed", "", 42])
def test_malformed_key_is_rejected_without_writing(user, invalid_key):
    record = make_entry(user)
    with pytest.raises(ValidationError):
        update_entry(**payload(record, user, submission_key=invalid_key))
    assert not DiaryEditSubmission.objects.exists()


def test_retry_checks_receipt_actor_and_current_authorization(user, staff_user, other_user):
    record = make_entry(user)
    values = payload(record, staff_user)
    first = update_entry(**values)
    # The author is allowed to edit the target but cannot reuse the administrator's key.
    with pytest.raises(EntryConflict):
        update_entry(**{**values, "actor": user})
    with pytest.raises(PermissionDenied):
        update_entry(**{**values, "actor": other_user})
    staff_user.is_staff = False
    staff_user.save(update_fields=["is_staff"])
    with pytest.raises(PermissionDenied):
        update_entry(**values)
    assert first.author_id == user.pk


def test_retry_rechecks_active_actor_in_database(user):
    record = make_entry(user)
    values = payload(record, user)
    update_entry(**values)
    type(user).objects.filter(pk=user.pk).update(is_active=False)
    assert user.is_active  # The caller still holds its pre-deactivation object.
    with pytest.raises(PermissionDenied):
        update_entry(**values)


def test_deleted_entry_keeps_edit_tombstones_and_is_not_recreated(user):
    record = make_entry(user)
    values = payload(record, user)
    first = update_entry(**values)
    receipt = DiaryEditSubmission.objects.get(key=values["submission_key"])
    delete_entry(actor=user, pk=record.pk, expected_updated_at=first.updated_at)
    receipt.refresh_from_db()
    assert receipt.entry_id is None
    with pytest.raises(EntryConflict):
        update_entry(**values)
    assert not DiaryEntry.objects.exists()


def test_uncertain_commit_retry_preserves_published_photos(user, image_upload, monkeypatch):
    record = make_entry(user, uploads=[image_upload()])
    removed = record.photos.get()
    old_bundle = private_path(removed.original.name).parent
    values = payload(record, user, remove_photo_ids=[removed.pk])
    real_commit, count = connection.commit, 0

    def uncertain_commit():
        nonlocal count
        count += 1
        real_commit()
        if count == 2:
            raise OperationalError("Synthetic lost commit acknowledgement")

    monkeypatch.setattr(connection, "commit", uncertain_commit)
    with pytest.raises(OperationalError):
        update_entry(**values, uploads=[image_upload()])
    monkeypatch.undo()
    retry = update_entry(**values, uploads=[image_upload()])
    assert retry.photos.count() == 1 and not old_bundle.exists()
    assert private_path(retry.photos.get().original.name).is_file()
    assert DiaryEditSubmission.objects.count() == 1
    assert cleanup_orphans(apply=True, minimum_age=0) == []


def test_failed_photo_save_has_no_receipt_and_can_retry(user, image_upload, monkeypatch):
    record = make_entry(user)
    values = payload(record, user)
    real_publish = photo_storage.publish_staged

    def failed_publish(photo):
        raise OSError("Synthetic storage failure")

    monkeypatch.setattr(photo_storage, "publish_staged", failed_publish)
    with pytest.raises(OSError):
        update_entry(**values, uploads=[image_upload()])
    assert not DiaryEditSubmission.objects.exists()
    record.refresh_from_db()
    assert record.updated_at == values["expected_updated_at"] and not record.photos.exists()
    monkeypatch.setattr(photo_storage, "publish_staged", real_publish)
    retry = update_entry(**values, uploads=[image_upload()])
    assert retry.photos.count() == 1 and DiaryEditSubmission.objects.count() == 1


@pytest.mark.parametrize("with_photos", [False, True])
def test_overlapping_identical_retries_commit_one_edit(user, image_upload, with_photos):
    record = make_entry(user)
    values = payload(record, user)
    start = threading.Barrier(2)

    def write():
        close_old_connections()
        try:
            actor = type(user).objects.get(pk=user.pk)
            start.wait(timeout=10)
            return update_entry(
                **{**values, "actor": actor},
                uploads=[image_upload()] if with_photos else [],
            ).updated_at
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(write) for _ in range(2)]
        versions = [future.result(timeout=20) for future in futures]
    record.refresh_from_db()
    assert versions == [record.updated_at, record.updated_at]
    assert record.photos.count() == int(with_photos)
    assert DiaryEditSubmission.objects.count() == 1
