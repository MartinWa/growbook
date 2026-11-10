"""Optional headings share diary validation, search and immutable retry behavior."""

from datetime import date
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.html import escape

from core.models import Photo
from diary import services
from diary.models import DiaryEditSubmission, DiaryEntry, DiarySubmission
from diary.queries import filter_entries
from diary.services import SubmissionConflict, create_entry, delete_entry_photo, update_entry

pytestmark = pytest.mark.django_db(transaction=True)


def capture(user, **overrides):
    values = {
        "author": user,
        "date": date(2026, 5, 9),
        "body": "Synthetic note",
        "uploads": (),
        "submission_key": uuid4(),
        **overrides,
    }
    return create_entry(**values).entry


def edit(record, **overrides):
    return update_entry(
        **{
            "actor": record.author,
            "pk": record.pk,
            "date": record.date,
            "title": record.title,
            "body": record.body,
            "expected_updated_at": record.updated_at,
            **overrides,
        }
    )


@pytest.mark.parametrize(
    ("title", "body", "photo_count"),
    [
        ("", "Synthetic note", 0),
        (" \n\t ", "Synthetic note", 0),
        ("  Synthetic heading  ", "Synthetic note", 0),
        ("  Synthetic heading  ", "", 1),
        ("  Synthetic heading  ", "Synthetic note", 3),
        ("Å" * 200, "Synthetic note", 0),
    ],
)
def test_optional_title_with_text_and_photos(user, image_upload, title, body, photo_count):
    record = capture(
        user, title=title, body=body, uploads=[image_upload() for _ in range(photo_count)]
    )
    record.refresh_from_db()
    assert record.title == title.strip()
    assert record.body == body and record.photos.count() == photo_count
    assert record.author_id == user.pk
    if record.title:
        assert list(filter_entries(q=record.title)) == [record]


@pytest.mark.parametrize("operation", ["capture", "edit"])
@pytest.mark.parametrize("title", [None, 123, ["Synthetic heading"], "Å" * 201])
def test_invalid_title_is_rejected_before_photo_processing(
    user, image_upload, monkeypatch, operation, title
):
    record = capture(user) if operation == "edit" else None

    def must_not_stage(*args):
        raise AssertionError("Invalid titles must be rejected before staging photos.")

    monkeypatch.setattr(services, "staged_uploads", must_not_stage)
    with pytest.raises(ValidationError) as error:
        if record:
            edit(record, title=title, uploads=[image_upload()])
        else:
            capture(user, title=title, uploads=[image_upload()])
    assert "title" in error.value.error_dict
    assert DiaryEntry.objects.count() == (1 if record else 0)
    assert DiarySubmission.objects.count() == (1 if record else 0)
    assert not DiaryEditSubmission.objects.exists() and not Photo.objects.exists()
    if record:
        version = record.updated_at
        record.refresh_from_db()
        assert record.title == "" and record.body == "Synthetic note"
        assert record.updated_at == version


def test_a_title_still_requires_note_or_photo_content(user, image_upload):
    with pytest.raises(ValidationError):
        capture(user, title="Synthetic heading", body="")
    assert not DiaryEntry.objects.exists()

    record = capture(user, title="Synthetic heading", body="", uploads=[image_upload()])
    photo = record.photos.get()
    with pytest.raises(ValidationError):
        edit(record, title="Replacement heading", remove_photo_ids=[photo.pk])
    with pytest.raises(ValidationError):
        delete_entry_photo(actor=user, pk=photo.pk, expected_updated_at=record.updated_at)
    record.refresh_from_db()
    assert record.title == "Synthetic heading" and record.photos.count() == 1
    assert not DiaryEditSubmission.objects.exists()


def test_title_search_preserves_user_text_and_updates_when_title_is_edited_or_cleared(user):
    title = "A\u030angstro\u0308m 100%_"
    record = capture(user, title=title, body="Synthetic BÖNOR\nSecond line")
    assert record.title == title
    for query in ("ÅNGSTRÖM", "100%_", "bönor", "BÖNOR\nSecond"):
        assert list(filter_entries(q=query, year=2026)) == [record]
    for query in ("Angstrom", "100xx", "100%xx"):
        assert not filter_entries(q=query).exists()

    renamed = edit(record, title="Renamed synthetic heading")
    assert not filter_entries(q="ÅNGSTRÖM").exists()
    assert list(filter_entries(q="renamed synthetic")) == [renamed]
    cleared = edit(renamed, title=" \n ")
    assert cleared.title == "" and cleared.body == record.body
    assert not filter_entries(q="renamed synthetic").exists()
    assert list(filter_entries(q="bönor")) == [cleared]


def test_creation_retry_includes_title_and_does_not_overwrite_a_later_edit(
    user, image_upload, monkeypatch
):
    values = {
        "author": user,
        "date": date(2026, 5, 9),
        "title": "Synthetic original heading",
        "body": "Synthetic note",
        "submission_key": uuid4(),
    }
    original = create_entry(**values, uploads=[image_upload()]).entry
    later = edit(original, title="Later independent heading")
    photos = list(later.photos.values_list("pk", "original"))

    def must_not_decode(*args):
        raise AssertionError("Receipt checks must precede photo decoding.")

    monkeypatch.setattr("core.photo_storage.process_staged", must_not_decode)
    with pytest.raises(SubmissionConflict):
        create_entry(**{**values, "title": "Changed retry heading"}, uploads=[image_upload()])
    retry = create_entry(**values, uploads=[image_upload()])
    assert not retry.created and retry.entry.pk == later.pk
    assert retry.entry.title == later.title and retry.entry.updated_at == later.updated_at
    assert list(retry.entry.photos.values_list("pk", "original")) == photos
    assert DiaryEntry.objects.count() == DiarySubmission.objects.count() == 1


@pytest.mark.parametrize("operation", ["capture", "edit"])
@pytest.mark.parametrize("htmx", [False, True])
def test_http_invalid_title_keeps_draft_and_save_key(client, user, image_upload, operation, htmx):
    client.force_login(user)
    record = capture(user) if operation == "edit" else None
    title = "Å" * 201
    values = {
        "date": "2026-05-09",
        "title": title,
        "body": "Keep the synthetic draft",
        "submission_key": str(uuid4()),
        "photos" if record else "library": image_upload(),
    }
    url = reverse("diary:capture")
    if record:
        url = reverse("diary:edit", args=[record.pk])
        values["expected_updated_at"] = record.updated_at.isoformat()
    response = client.post(url, values, **({"HTTP_HX_REQUEST": "true"} if htmx else {}))
    assert response.status_code == 422
    form = response.context["form" if record or htmx else "capture_form"]
    assert "title" in form.errors
    assert form["title"].value() == title and form["body"].value() == values["body"]
    assert str(form["submission_key"].value()) == values["submission_key"]
    assert 'href="#id_title"' in response.text and 'data-field-errors="title"' in response.text
    assert not Photo.objects.exists() and not DiaryEditSubmission.objects.exists()
    assert DiaryEntry.objects.count() == (1 if record else 0)


def test_title_is_escaped_on_cards_and_observation_pages(client, user):
    title = 'Synthetic <script>alert("heading")</script> & flowers'
    record = capture(user, title=title)
    client.force_login(user)
    for url in (reverse("diary:list"), record.get_absolute_url()):
        response = client.get(url)
        assert response.status_code == 200
        assert escape(title) in response.text and title not in response.text
