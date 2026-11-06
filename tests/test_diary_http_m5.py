from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.urls import reverse

from core.media import private_path
from diary.models import DiaryEntry, DiarySubmission
from diary.services import create_entry, update_entry
from garden.services import create_area, create_crop, create_planting, create_variety

pytestmark = pytest.mark.django_db(transaction=True)


def make_entry(user, *, body="Synthetic observation", uploads=(), links=()):
    record = create_entry(
        author=user,
        date=date(2026, 5, 9),
        body=body,
        uploads=uploads,
        submission_key=uuid4(),
    ).entry
    if links:
        record = update_entry(
            actor=user,
            pk=record.pk,
            date=record.date,
            body=record.body,
            links=links,
            expected_updated_at=record.updated_at,
        )
    return record


@pytest.fixture
def garden_records(user):
    crop = create_crop(actor=user, name="Synthetic tomato").record
    variety = create_variety(actor=user, crop=crop, name="Synthetic Ferline").record
    parent = create_area(actor=user, name="Synthetic greenhouse").record
    child = create_area(actor=user, name="Synthetic bed", parent=parent).record
    planting = create_planting(
        actor=user,
        crop=crop,
        start_year=2026,
        variety=variety,
        area=child,
        label="Synthetic batch",
    ).record
    return SimpleNamespace(
        crop=crop, variety=variety, parent=parent, child=child, planting=planting
    )


def edit_data(record, **overrides):
    data = {
        "expected_updated_at": record.updated_at.isoformat(),
        "date": record.date.isoformat(),
        "body": record.body,
    }
    data.update(overrides)
    return data


def test_author_can_open_edit_and_other_member_cannot(client, user, other_user):
    record = make_entry(user)
    client.force_login(user)
    client.cookies["django_language"] = "en"
    response = client.get(reverse("diary:list"))
    assert f'href="{reverse("diary:edit", args=[record.pk])}">Edit' in response.text
    response = client.get(reverse("diary:edit", args=[record.pk]))
    assert response.status_code == 200
    assert reverse("diary:edit", args=[record.pk]) in response.text
    client.cookies["django_language"] = "en"
    detail = client.get(record.get_absolute_url())
    assert "Add garden links" not in detail.text
    assert f'href="{reverse("diary:edit", args=[record.pk])}">Edit' in detail.text
    client.force_login(other_user)
    response = client.get(reverse("diary:list"))
    assert f'href="{reverse("diary:edit", args=[record.pk])}"' not in response.text
    response = client.get(reverse("diary:edit", args=[record.pk]))
    assert response.status_code == 403
    response = client.get(reverse("diary:delete", args=[record.pk]))
    assert response.status_code == 403


def test_staff_administrator_can_open_edit(client, user, staff_user):
    record = make_entry(user)
    client.force_login(staff_user)
    assert client.get(reverse("diary:edit", args=[record.pk])).status_code == 200
    assert client.get(reverse("diary:delete", args=[record.pk])).status_code == 200


def test_edit_post_updates_note_and_links(client, user, garden_records):
    record = make_entry(user)
    client.force_login(user)
    response = client.post(
        reverse("diary:edit", args=[record.pk]),
        edit_data(
            record,
            body="Edited note",
            date="2026-06-01",
            link_areas=[garden_records.parent.pk],
            link_crops=[garden_records.crop.pk],
            link_varieties=[garden_records.variety.pk],
            link_plantings=[garden_records.planting.pk],
        ),
    )
    assert response.status_code == 302
    assert response.url == record.get_absolute_url()
    record.refresh_from_db()
    assert (record.body, record.date) == ("Edited note", date(2026, 6, 1))
    assert record.links.count() == 4


def test_edit_post_htmx_redirects_without_rendering_the_form(client, user):
    record = make_entry(user)
    client.force_login(user)
    response = client.post(
        reverse("diary:edit", args=[record.pk]),
        edit_data(record, body="HTMX edit"),
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 204
    assert response["HX-Redirect"] == record.get_absolute_url()


@pytest.mark.parametrize("editing", [False, True])
@pytest.mark.parametrize("htmx", [False, True])
def test_apple_hdr_library_upload_succeeds_in_capture_and_edit(
    client, user, apple_hdr_upload, editing, htmx
):
    client.force_login(user)
    upload = apple_hdr_upload()
    original_bytes = upload.read()
    upload.seek(0)
    if editing:
        record = make_entry(user)
        path = reverse("diary:edit", args=[record.pk])
        data = edit_data(record, photos=upload)
    else:
        path = reverse("diary:capture")
        data = {"submission_key": uuid4(), "date": "2026-05-09", "body": "", "library": upload}
    response = client.post(path, data, **({"HTTP_HX_REQUEST": "true"} if htmx else {}))
    assert response.status_code == (204 if htmx else 302)
    record = DiaryEntry.objects.get()
    assert response["HX-Redirect" if htmx else "Location"] == record.get_absolute_url()
    photo = record.photos.get()
    assert photo.original_format == "JPEG" and (photo.width, photo.height) == (80, 60)
    assert private_path(photo.original.name).read_bytes() == original_bytes


@pytest.mark.parametrize("editing", [False, True])
@pytest.mark.parametrize("htmx", [False, True])
def test_corrupt_apple_hdr_header_returns_validation_feedback_and_allows_retry(
    client, user, apple_hdr_upload, corrupt_apple_hdr_upload, image_upload, editing, htmx
):
    client.force_login(user)
    client.cookies["django_language"] = "en"
    uploads = [image_upload(), corrupt_apple_hdr_upload()]
    if editing:
        record = make_entry(user, uploads=[image_upload()])
        original_updated_at = record.updated_at
        original_photos = list(record.photos.values_list("pk", flat=True))
        path = reverse("diary:edit", args=[record.pk])
        data = edit_data(record, body="Synthetic edited note", photos=uploads)
        photo_field = "photos"
    else:
        path = reverse("diary:capture")
        data = {
            "submission_key": uuid4(),
            "date": "2026-05-09",
            "body": "Synthetic new note",
            "library": uploads,
        }
        photo_field = "library"
    headers = {"HTTP_HX_REQUEST": "true"} if htmx else {}
    response = client.post(path, data, **headers)
    assert response.status_code == 422
    assert "A photo could not be decoded safely. Nothing was saved." in response.text
    if editing:
        record.refresh_from_db()
        assert record.body == "Synthetic observation"
        assert record.updated_at == original_updated_at
        assert list(record.photos.values_list("pk", flat=True)) == original_photos
    else:
        assert not DiaryEntry.objects.exists()
        assert not DiarySubmission.objects.exists()
    data[photo_field] = apple_hdr_upload()
    retry = client.post(path, data, **headers)
    assert retry.status_code == (204 if htmx else 302)
    record = DiaryEntry.objects.get()
    assert retry["HX-Redirect" if htmx else "Location"] == record.get_absolute_url()
    assert record.body == data["body"]
    assert record.photos.count() == (2 if editing else 1)


def test_stale_edit_returns_409_and_keeps_saved_value(client, user):
    record = make_entry(user)
    original = record.updated_at
    record = update_entry(
        actor=user,
        pk=record.pk,
        date=record.date,
        body="First save",
        expected_updated_at=original,
    )
    client.force_login(user)
    client.cookies["django_language"] = "en"
    response = client.post(
        reverse("diary:edit", args=[record.pk]),
        {
            "expected_updated_at": original.isoformat(),
            "date": record.date.isoformat(),
            "body": "Stale save",
        },
    )
    assert response.status_code == 409
    assert "changed while the form was open" in response.text
    record.refresh_from_db()
    assert record.body == "First save"


def test_edit_post_by_other_member_is_forbidden_without_changes(client, user, other_user):
    record = make_entry(user)
    client.force_login(other_user)
    response = client.post(
        reverse("diary:edit", args=[record.pk]),
        edit_data(record, body="Intruder edit"),
    )
    assert response.status_code == 403
    record.refresh_from_db()
    assert record.body == "Synthetic observation"


def test_edit_rejects_an_unexpected_file_field(client, user, image_upload):
    record = make_entry(user)
    client.force_login(user)
    client.cookies["django_language"] = "en"
    response = client.post(
        reverse("diary:edit", args=[record.pk]),
        edit_data(record, unexpected=image_upload()),
    )
    assert response.status_code == 422
    assert "unexpected photo field" in response.text


def test_edit_with_empty_note_and_removed_photo_keeps_the_form_open(client, user, image_upload):
    record = make_entry(user, body="", uploads=[image_upload()])
    photo = record.photos.get()
    client.force_login(user)
    client.cookies["django_language"] = "en"
    response = client.post(
        reverse("diary:edit", args=[record.pk]),
        edit_data(record, body="", remove_photos=[photo.pk]),
    )
    assert response.status_code == 422
    assert "Write a note or keep at least one photo." in response.text
    assert record.photos.filter(pk=photo.pk).exists()


@pytest.mark.parametrize(("body", "replace_photo"), [("Note", False), ("Note", True), ("", True)])
def test_edit_removes_a_photo_and_can_replace_it_atomically(
    client, user, image_upload, body, replace_photo
):
    record = make_entry(user, body=body, uploads=[image_upload()])
    photo = record.photos.get()
    bundle = private_path(photo.original.name).parent
    original_version = record.updated_at
    data = edit_data(record, remove_photos=[str(photo.pk)])
    if replace_photo:
        data["photos"] = image_upload()
    client.force_login(user)

    response = client.post(reverse("diary:edit", args=[record.pk]), data)

    assert response.status_code == 302
    assert response.url == record.get_absolute_url()
    record.refresh_from_db()
    assert record.body == body
    assert record.updated_at != original_version
    assert not record.photos.filter(pk=photo.pk).exists()
    assert not bundle.exists()
    assert record.photos.count() == int(replace_photo)
    for remaining in record.photos.all():
        assert private_path(remaining.original.name).exists()


def test_delete_requires_confirmation_and_removes_the_entry(client, user):
    record = make_entry(user)
    client.force_login(user)
    assert client.get(reverse("diary:delete", args=[record.pk])).status_code == 200
    response = client.post(
        reverse("diary:delete", args=[record.pk]),
        {"expected_updated_at": record.updated_at.isoformat()},
    )
    assert response.status_code == 422
    assert DiaryEntry.objects.filter(pk=record.pk).exists()
    response = client.post(
        reverse("diary:delete", args=[record.pk]),
        {"expected_updated_at": record.updated_at.isoformat(), "confirm": "on"},
    )
    assert response.status_code == 302
    assert response.url == reverse("diary:list")
    assert not DiaryEntry.objects.filter(pk=record.pk).exists()
    assert DiarySubmission.objects.get(key=record.submission_key).entry_id is None


def test_staff_deletes_another_authors_entry(client, user, staff_user):
    record = make_entry(user)
    client.force_login(staff_user)
    response = client.post(
        reverse("diary:delete", args=[record.pk]),
        {"expected_updated_at": record.updated_at.isoformat(), "confirm": "on"},
    )
    assert response.status_code == 302
    assert not DiaryEntry.objects.filter(pk=record.pk).exists()


def test_list_search_and_all_five_filters(client, user, garden_records):
    crop_only = make_entry(user, body="Ångström crop", links=[garden_records.crop])
    planting = make_entry(user, body="Planting observation", links=[garden_records.planting])
    area_only = make_entry(user, body="Greenhouse observation", links=[garden_records.parent])
    client.force_login(user)

    response = client.get(reverse("diary:list"), {"q": "ÅNGSTRÖM"})
    assert response.status_code == 200
    assert list(response.context["page_obj"].object_list) == [crop_only]

    response = client.get(reverse("diary:list"), {"crop": garden_records.crop.pk})
    assert set(response.context["page_obj"].object_list) == {crop_only, planting}

    response = client.get(reverse("diary:list"), {"variety": garden_records.variety.pk})
    assert set(response.context["page_obj"].object_list) == {planting}

    response = client.get(reverse("diary:list"), {"area": garden_records.parent.pk})
    assert set(response.context["page_obj"].object_list) == {planting, area_only}

    response = client.get(reverse("diary:list"), {"planting": garden_records.planting.pk})
    assert set(response.context["page_obj"].object_list) == {planting}

    response = client.get(reverse("diary:list"), {"year": 2026})
    assert set(response.context["page_obj"].object_list) == {crop_only, planting, area_only}


def test_pagination_preserves_the_active_filter(client, user, garden_records):
    for number in range(21):
        make_entry(user, body=f"Observation {number}", links=[garden_records.crop])
    client.force_login(user)
    response = client.get(reverse("diary:list"), {"crop": garden_records.crop.pk})
    assert response.status_code == 200
    assert "page=2" in response.text
    assert f"crop={garden_records.crop.pk}" in response.text


def test_invalid_filter_is_rejected_without_breaking_the_page(client, user):
    make_entry(user)
    client.force_login(user)
    response = client.get(reverse("diary:list"), {"year": "not-a-year"})
    assert response.status_code == 422
    assert response.context["form"].errors


def test_record_pages_show_linked_observations(client, user, garden_records):
    record = make_entry(
        user,
        body="Related observation",
        links=[
            garden_records.crop,
            garden_records.variety,
            garden_records.parent,
            garden_records.planting,
        ],
    )
    client.force_login(user)
    for name, pk in (
        ("garden:crop-detail", garden_records.crop.pk),
        ("garden:variety-detail", garden_records.variety.pk),
        ("garden:area-detail", garden_records.parent.pk),
        ("garden:planting-detail", garden_records.planting.pk),
        ("garden:crop-history", garden_records.crop.pk),
        ("garden:variety-history", garden_records.variety.pk),
        ("garden:area-history", garden_records.parent.pk),
    ):
        response = client.get(reverse(name, args=[pk]))
        assert response.status_code == 200
        assert "Related observation" in response.text
        assert response.context["observation_count"] >= 1
    assert record.get_absolute_url() in response.text


def test_photo_page_shows_edit_actions_only_to_editors(client, user, other_user, image_upload):
    record = make_entry(user, body="Note", uploads=[image_upload()])
    photo = record.photos.get()
    edit_url = reverse("diary:photo-edit", args=[photo.pk])
    client.force_login(user)
    response = client.get(reverse("photos:detail", args=[photo.pk]))
    assert edit_url in response.text
    client.force_login(other_user)
    response = client.get(reverse("photos:detail", args=[photo.pk]))
    assert edit_url not in response.text


def test_photo_edit_and_delete_http_paths(client, user, image_upload):
    record = make_entry(user, body="Note", uploads=[image_upload()])
    photo = record.photos.get()
    client.force_login(user)
    response = client.post(
        reverse("diary:photo-edit", args=[photo.pk]),
        {
            "expected_updated_at": record.updated_at.isoformat(),
            "caption": "Synthetic caption",
            "position": 2,
        },
    )
    assert response.status_code == 302
    photo.refresh_from_db()
    assert (photo.caption, photo.position) == ("Synthetic caption", 2)

    record.refresh_from_db()
    response = client.post(
        reverse("diary:photo-delete", args=[photo.pk]),
        {"expected_updated_at": record.updated_at.isoformat(), "confirm": "on"},
    )
    assert response.status_code == 302
    assert not record.photos.filter(pk=photo.pk).exists()


def test_anonymous_edit_and_delete_redirect_to_login(client):
    for name in ("diary:edit", "diary:delete", "diary:photo-edit", "diary:photo-delete"):
        response = client.get(reverse(name, args=[1]))
        assert response.status_code == 302
        assert "login" in response.url
