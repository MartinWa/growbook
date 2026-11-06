"""Capture tags, immutable receipts and current eligibility under serialized writes."""

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.urls import reverse
from django.utils.translation import gettext as _

from core import photo_storage
from core.models import Photo
from diary.forms import CaptureForm, EntryEditForm
from diary.models import DiaryEntry, DiaryLink, DiarySubmission
from diary.queries import filter_entries
from diary.services import SubmissionConflict, create_entry, update_entry
from garden.services import (
    archive_area,
    archive_crop,
    archive_variety,
    create_area,
    create_crop,
    create_planting,
    create_variety,
    delete_crop,
    edit_planting,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def tags(user):
    crop = create_crop(actor=user, name="Tomato").record
    variety = create_variety(actor=user, crop=crop, name="Golden Cherry").record
    parent = create_area(actor=user, name="Glasshouse").record
    area = create_area(actor=user, name="West bed", parent=parent).record
    planting = create_planting(
        actor=user, crop=crop, variety=variety, area=area, start_year=2026, label="Batch A"
    ).record
    return SimpleNamespace(crop=crop, variety=variety, parent=parent, area=area, planting=planting)


def capture(user, **overrides):
    values = dict(
        date=date(2026, 7, 14), body="Synthetic observation", uploads=(), submission_key=uuid4()
    )
    values.update(overrides)
    return create_entry(author=user, **values)


@pytest.mark.parametrize("photo_count", [0, 1, 3])
def test_capture_tags_are_explicit_and_derive_retrieval_context(
    user, tags, image_upload, photo_count
):
    result = capture(
        user,
        body="" if photo_count else "Note",
        links=[tags.planting],
        uploads=[image_upload(color=(number, 80, 80)) for number in range(photo_count)],
    )
    assert result.entry.photos.count() == photo_count
    link = result.entry.links.get()
    assert link.planting_id == tags.planting.pk
    assert link.crop_id is link.variety_id is link.area_id is None
    for filters in (
        {"planting": tags.planting},
        {"crop": tags.crop},
        {"variety": tags.variety},
        {"area": tags.area},
        {"area": tags.parent},
    ):
        assert list(filter_entries(**filters)) == [result.entry]
    variety_entry = capture(user, links=[tags.variety]).entry
    assert variety_entry.links.count() == 1
    assert variety_entry in filter_entries(crop=tags.crop)


def test_tag_only_observation_is_still_empty(user, tags):
    with pytest.raises(ValidationError):
        capture(user, body="", links=[tags.planting])
    assert not DiaryEntry.objects.exists()
    assert not DiaryLink.objects.exists()


def test_unlinked_receipts_keep_the_legacy_fingerprint_after_later_tagging(user, tags):
    first = capture(user)
    legacy_payload = b'{"version":1,"date":"2026-07-14","body":"Synthetic observation","photos":[]}'
    receipt = DiarySubmission.objects.get(entry=first.entry)
    assert receipt.payload_sha256 == hashlib.sha256(legacy_payload).hexdigest()
    update_entry(
        actor=user,
        pk=first.entry.pk,
        date=first.entry.date,
        body=first.entry.body,
        links=[tags.crop],
        expected_updated_at=first.entry.updated_at,
    )
    retry = capture(user, submission_key=receipt.key)
    assert not retry.created and retry.entry.links.get().crop_id == tags.crop.pk


def test_retry_uses_target_ids_and_retrieval_follows_current_relationships(user, tags):
    first = capture(user, links=[tags.planting])
    destination = create_area(actor=user, name="Kitchen beds").record
    edit_planting(
        actor=user,
        pk=tags.planting.pk,
        crop=tags.crop,
        start_year=2026,
        variety=tags.variety,
        area=destination,
        label="Renamed batch",
        expected_updated_at=tags.planting.updated_at,
    )
    retry = capture(user, links=[tags.planting], submission_key=first.entry.submission_key)
    assert not retry.created and retry.entry.pk == first.entry.pk
    assert list(filter_entries(area=destination)) == [first.entry]
    assert not filter_entries(area=tags.area).exists()


def test_order_independent_retry_and_changed_typed_ids_conflict(user, tags, image_upload):
    key = uuid4()
    first = capture(
        user, links=[tags.planting, tags.area], submission_key=key, uploads=[image_upload()]
    )
    retry = capture(
        user,
        links=[("area", tags.area.pk), ("planting", tags.planting.pk)],
        submission_key=key,
        uploads=[image_upload()],
    )
    assert not retry.created and retry.entry.pk == first.entry.pk
    for links in ([tags.planting], [tags.planting, tags.parent]):
        with pytest.raises(SubmissionConflict):
            capture(user, links=links, submission_key=key, uploads=[image_upload()])
    assert (DiaryEntry.objects.count(), DiaryLink.objects.count(), Photo.objects.count()) == (
        1,
        2,
        1,
    )


@pytest.mark.parametrize("kind", ["area", "crop", "variety"])
def test_http_retry_resolves_archived_targets_before_eligibility(
    client, user, tags, image_upload, kind
):
    target = getattr(tags, kind)
    field = {"area": "link_areas", "crop": "link_crops", "variety": "link_varieties"}[kind]
    data = {
        "date": "2026-07-14",
        "body": "Original note",
        "submission_key": uuid4(),
        field: [target.pk],
    }
    client.force_login(user)
    first = client.post(reverse("diary:capture"), {**data, "library": image_upload()})
    assert first.status_code == 302
    {"area": archive_area, "crop": archive_crop, "variety": archive_variety}[kind](
        actor=user, pk=target.pk
    )
    retry = client.post(
        reverse("diary:capture"), {**data, "library": image_upload()}, HTTP_HX_REQUEST="true"
    )
    assert retry.status_code == 204 and retry["HX-Redirect"] == first.url
    fresh = client.post(
        reverse("diary:capture"),
        {**data, "submission_key": uuid4(), "library": image_upload()},
        HTTP_HX_REQUEST="true",
    )
    assert fresh.status_code == 422
    assert field in fresh.context["form"].errors
    assert (DiaryEntry.objects.count(), DiaryLink.objects.count(), Photo.objects.count()) == (
        1,
        1,
        1,
    )


def test_http_retry_can_reference_a_removed_original_target(client, user):
    crop = create_crop(actor=user, name="Original crop").record
    crop_pk = crop.pk
    key = uuid4()
    record = capture(user, links=[crop], submission_key=key).entry
    update_entry(
        actor=user,
        pk=record.pk,
        date=record.date,
        body="Later edit",
        links=(),
        expected_updated_at=record.updated_at,
    )
    delete_crop(actor=user, pk=crop_pk, expected_updated_at=crop.updated_at)
    client.force_login(user)
    data = {
        "date": "2026-07-14",
        "body": "Synthetic observation",
        "submission_key": key,
        "link_crops": [crop_pk],
    }
    retry = client.post(reverse("diary:capture"), data)
    assert retry.status_code == 302 and retry.url == record.get_absolute_url()
    conflict = client.post(reverse("diary:capture"), {**data, "body": "Changed"})
    assert conflict.status_code == 409
    assert f'value="{crop_pk}"' in conflict.text and " checked" in conflict.text
    assert not DiaryLink.objects.exists()
    assert DiaryEntry.objects.count() == 1


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "nope", "area:1", "１", "01"])
def test_capture_rejects_malformed_tag_ids(value):
    form = CaptureForm(
        {"date": "2026-07-14", "body": "Note", "submission_key": uuid4(), "link_crops": [value]}
    )
    assert not form.is_valid()
    assert "link_crops" in form.errors


def test_duplicate_and_unknown_typed_targets_are_rejected(user, tags):
    for links in (
        [tags.crop, tags.crop],
        [("crop", tags.crop.pk), ("crop", tags.crop.pk)],
        [("bogus", 1)],
        [("crop", True)],
        [("crop", 999999)],
    ):
        with pytest.raises(ValidationError):
            capture(user, links=links)
    assert not DiaryEntry.objects.exists()


def test_archive_during_photo_processing_is_rechecked_inside_new_write(
    user, tags, image_upload, monkeypatch, settings
):
    original = photo_storage.process_staged

    def process(photo):
        assert not connection.in_atomic_block
        original(photo)

        def archive():
            close_old_connections()
            try:
                archive_crop(actor=type(user).objects.get(pk=user.pk), pk=tags.crop.pk)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(archive).result(timeout=10)

    monkeypatch.setattr(photo_storage, "process_staged", process)
    with pytest.raises(ValidationError):
        capture(user, links=[tags.crop], uploads=[image_upload()])
    assert not DiaryEntry.objects.exists()
    assert not DiaryLink.objects.exists()
    assert not DiarySubmission.objects.exists()
    assert not Photo.objects.exists()
    assert list(settings.MEDIA_ROOT.glob("photos/*/*/*/*")) == []


def test_link_failure_rolls_back_photos_entry_and_receipt(
    user, tags, image_upload, monkeypatch, settings
):
    def fail(*args, **kwargs):
        raise OSError("Synthetic link write failure")

    monkeypatch.setattr(DiaryLink, "save", fail)
    with pytest.raises(OSError):
        capture(user, links=[tags.planting], uploads=[image_upload()])
    assert not DiaryEntry.objects.exists()
    assert not DiarySubmission.objects.exists()
    assert not Photo.objects.exists()
    assert list(settings.MEDIA_ROOT.glob("photos/*/*/*/*")) == []


def test_older_planting_with_archived_associations_stays_selectable(user, tags):
    archive_crop(actor=user, pk=tags.crop.pk)
    archive_variety(actor=user, pk=tags.variety.pk)
    archive_area(actor=user, pk=tags.area.pk)
    record = capture(user, date=date(2024, 1, 1), links=[tags.planting]).entry
    assert record.links.get().planting_id == tags.planting.pk
    form = CaptureForm(initial={"date": "2024-01-01"})
    planting_group = form.tag_groups[0]
    assert planting_group["options"][0]["in_year"] is False
    assert "Golden Cherry" in planting_group["options"][0]["title"]
    assert "Glasshouse / West bed" in planting_group["options"][0]["context"]


def test_edit_keeps_archived_explicit_links_and_does_not_add_derived_links(user, tags):
    record = capture(user, links=[tags.crop, tags.area, tags.variety, tags.planting]).entry
    archive_crop(actor=user, pk=tags.crop.pk)
    data = {
        "date": record.date.isoformat(),
        "body": record.body,
        "expected_updated_at": record.updated_at.isoformat(),
        "link_crops": [tags.crop.pk],
        "link_areas": [tags.area.pk],
        "link_varieties": [tags.variety.pk],
        "link_plantings": [tags.planting.pk],
    }
    form = EntryEditForm(data, entry=record)
    assert form.is_valid(), form.errors
    updated = update_entry(
        actor=user,
        pk=record.pk,
        date=record.date,
        body="Edited",
        links=form.selected_links,
        expected_updated_at=record.updated_at,
    )
    assert updated.links.count() == 4
    assert any(
        option["selected"] and _("Archived") in option["context"]
        for option in form.tag_groups[2]["options"]
    )


def test_simultaneous_tagged_retries_commit_one_complete_submission(user, tags, image_upload):
    key = uuid4()
    photo_bytes = image_upload().read()
    start = threading.Barrier(2)

    def write(reverse_order):
        close_old_connections()
        try:
            actor = type(user).objects.get(pk=user.pk)
            links = [("planting", tags.planting.pk), ("area", tags.parent.pk)]
            start.wait(timeout=10)
            return capture(
                actor,
                links=links[::-1] if reverse_order else links,
                submission_key=key,
                uploads=[SimpleUploadedFile("synthetic.jpg", photo_bytes)],
            )
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(write, value) for value in (False, True)]
        results = [future.result(timeout=20) for future in futures]
    assert sorted(result.created for result in results) == [False, True]
    assert results[0].entry.pk == results[1].entry.pk
    assert (
        DiaryEntry.objects.count(),
        DiarySubmission.objects.count(),
        DiaryLink.objects.count(),
        Photo.objects.count(),
    ) == (1, 1, 2, 1)
