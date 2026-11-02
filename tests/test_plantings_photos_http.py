from datetime import date
from uuid import uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from core.media import private_path
from core.models import Photo
from garden.models import Planting, PlantingSubmission
from garden.services import create_crop, create_planting, create_planting_event

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("hx", [False, True])
@pytest.mark.parametrize("editing", [False, True])
@pytest.mark.parametrize("failure", ["invalid", "count", "file", "total", "pixels", "unexpected"])
def test_planting_http_photo_failures_never_save_partial_fields_or_images(
    client, user, image_upload, settings, hx, editing, failure
):
    crop = create_crop(actor=user, name="Tomato").record
    client.force_login(user)
    record = (
        create_planting(
            actor=user, crop=crop, start_year=2026, label="Original", uploads=[image_upload()]
        ).record
        if editing
        else None
    )
    original = record.photos.get() if record else None
    data = {
        "submission_key": str(uuid4()),
        "crop": crop.pk,
        "start_year": 2026,
        "lifespan": "annual",
        "label": "Must not save",
        "evaluation": "Draft results",
        "photos": [image_upload(), image_upload()],
    }
    if record:
        data["expected_updated_at"] = record.updated_at.isoformat()
    if failure == "invalid":
        data["photos"] = [image_upload(), SimpleUploadedFile("bad.jpg", b"invalid")]
    elif failure == "unexpected":
        data["unexpected"] = image_upload()
    else:
        limit = {
            "count": "PHOTO_MAX_COUNT",
            "file": "PHOTO_MAX_FILE_BYTES",
            "total": "PHOTO_MAX_TOTAL_BYTES",
            "pixels": "PHOTO_MAX_PIXELS",
        }[failure]
        setattr(settings, limit, 1)
    path = (
        reverse("garden:planting-edit", args=[record.pk])
        if editing
        else reverse("garden:planting-create")
    )
    headers = {"HTTP_HX_REQUEST": "true"} if hx else {}
    response = client.post(path, data, **headers)
    assert response.status_code == 422 and "error-summary" in response.text
    assert Planting.objects.count() == int(editing) and Photo.objects.count() == int(editing)
    assert not PlantingSubmission.objects.filter(key=data["submission_key"]).exists()
    if editing:
        record.refresh_from_db()
        assert record.label == "Original" and record.evaluation == ""
        assert private_path(original.original.name).is_file()
    if hx:
        assert 'id="register-form"' not in response.text
    else:
        assert response.context["form"]["evaluation"].value() == "Draft results"
        assert response.context["form"]["submission_key"].value() == data["submission_key"]
        assert "Dina fält har behållits" in response.text


@pytest.mark.parametrize(
    "action",
    ["planting-delete", "event-create", "event-edit", "event-delete", "photo-edit", "photo-delete"],
)
def test_nonupload_planting_actions_reject_unexpected_and_oversized_photos(
    client, user, image_upload, settings, action
):
    crop = create_crop(actor=user, name="Tomato").record
    record = create_planting(
        actor=user, crop=crop, start_year=2026, uploads=[image_upload()]
    ).record
    event = create_planting_event(
        actor=user, planting=record, kind="sowing", actual_date=date(2026, 1, 1)
    ).record
    record.refresh_from_db()
    photo = record.photos.get()
    client.force_login(user)
    if action.startswith("event-"):
        args = [record.pk] if action == "event-create" else [record.pk, event.pk]
        version = event.updated_at
    elif action.startswith("photo-"):
        args, version = [photo.pk], record.updated_at
    else:
        args, version = [record.pk], record.updated_at
    data = {
        "submission_key": str(uuid4()),
        "kind": "sowing",
        "actual_date": "2026-01-02",
        "confirm": "on",
        "expected_updated_at": version.isoformat(),
        "caption": "Must not save",
        "position": 5,
    }
    path = reverse("garden:" + action, args=args)
    assert client.post(path, {**data, "unexpected": image_upload()}).status_code == 422
    settings.PHOTO_MAX_FILE_BYTES = 1
    assert client.post(path, {**data, "unexpected": image_upload()}).status_code == 422
    assert (
        Planting.objects.count() == 1 and record.events.count() == 1 and record.photos.count() == 1
    )
    assert record.events.count() == 1
    event.refresh_from_db()
    photo.refresh_from_db()
    assert event.actual_date == date(2026, 1, 1) and photo.caption == ""
    assert private_path(photo.original.name).is_file()


@pytest.mark.parametrize("hx", [False, True])
def test_planting_photo_authorization_for_files_viewer_and_mutations(
    client, user, image_upload, hx
):
    crop = create_crop(actor=user, name="Tomato").record
    record = create_planting(
        actor=user, crop=crop, start_year=2026, uploads=[image_upload()]
    ).record
    photo = record.photos.get()
    paths = [
        photo.get_absolute_url(),
        photo.display_url,
        photo.thumbnail_url,
        photo.original_url,
        reverse("garden:photo-edit", args=[photo.pk]),
        reverse("garden:photo-delete", args=[photo.pk]),
    ]
    headers = {"HTTP_HX_REQUEST": "true"} if hx else {}
    for path in paths:
        response = client.get(path, **headers)
        assert response.status_code == (401 if hx else 302)
        assert "no-store" in response["Cache-Control"]
    client.force_login(user)
    viewer = client.get(photo.get_absolute_url())
    assert "Bild till odling" in viewer.text and record.get_absolute_url() in viewer.text
    for path in (photo.display_url, photo.thumbnail_url, photo.original_url):
        response = client.get(path)
        assert response.status_code == 200
        assert len(b"".join(response.streaming_content)) > 0
    user.is_active = False
    user.save(update_fields=["is_active"])
    for path in paths:
        assert client.get(path, **headers).status_code == (401 if hx else 302)
