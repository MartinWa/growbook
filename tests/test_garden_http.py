from datetime import date
from uuid import uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import OperationalError
from django.test import Client
from django.urls import reverse

from core.media import private_path
from core.models import Photo
from diary.services import create_entry
from garden.models import Crop, GrowingArea, RegisterSubmission, Variety
from garden.services import (
    archive_area,
    archive_crop,
    archive_variety,
    create_area,
    create_crop,
    create_variety,
    delete_area,
    delete_crop,
    edit_area,
    edit_variety,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("kind", ["crop", "variety", "area"])
def test_empty_register_offers_creation_with_crop_prerequisite(client, user, kind):
    client.force_login(user)
    response = client.get(reverse(f"garden:{kind}-list"))
    action = reverse(f"garden:{'crop' if kind == 'variety' else kind}-create")
    assert response.context["empty_action_url"] == action
    assert b'class="panel empty-state"' in response.content
    assert b'class="register-table' not in response.content
    if kind == "variety":
        create_crop(actor=user, name="First crop")
        assert client.get(reverse("garden:variety-list")).context["empty_action_url"] == reverse(
            "garden:variety-create"
        )


def test_archived_only_register_offers_a_view_that_contains_records(client, user, register_record):
    kind, record = register_record
    {"crop": archive_crop, "variety": archive_variety, "area": archive_area}[kind](
        actor=user, pk=record.pk
    )
    client.force_login(user)
    response = client.get(reverse(f"garden:{kind}-list"))
    assert not response.context["records"]
    assert response.context["empty_action_url"] == reverse(f"garden:{kind}-list") + "?archived=1"
    archived = client.get(response.context["empty_action_url"])
    assert [item.pk for item in archived.context["records"]] == [record.pk]
    assert b'class="panel empty-state"' not in archived.content


def test_garden_landing_requires_login_and_counts_only_active_records(client, user):
    path = reverse("garden:home")
    assert client.get(path).status_code == 302
    assert client.get(path, HTTP_HX_REQUEST="true").status_code == 401
    crop = create_crop(actor=user, name="Active crop").record
    archived_crop = create_crop(actor=user, name="Archived crop").record
    variety = create_variety(actor=user, crop=crop, name="Archived variety").record
    create_variety(actor=user, crop=crop, name="Active variety")
    area = create_area(actor=user, name="Archived area").record
    create_area(actor=user, name="Active area")
    archive_crop(actor=user, pk=archived_crop.pk)
    archive_variety(actor=user, pk=variety.pk)
    archive_area(actor=user, pk=area.pk)
    client.force_login(user)
    response = client.get(path)
    assert response.status_code == 200
    assert "no-store" in response["Cache-Control"]
    assert [response.context[key] for key in ("crop_count", "variety_count", "area_count")] == [
        1,
        1,
        1,
    ]


@pytest.fixture(params=["crop", "variety", "area"])
def register_record(request, user):
    if request.param == "crop":
        record = create_crop(actor=user, name="Synthetic crop", notes="Full crop notes").record
    elif request.param == "variety":
        crop = create_crop(actor=user, name="Synthetic crop").record
        record = create_variety(
            actor=user, crop=crop, name="Synthetic variety", description="Full description"
        ).record
    else:
        record = create_area(
            actor=user, name="Synthetic area", description="Full description"
        ).record
    return request.param, record


def payload(kind, record=None, **overrides):
    values = {"submission_key": str(uuid4()), "name": "Synthetic new record"}
    if record:
        values["expected_updated_at"] = record.updated_at.isoformat()
        if kind == "variety":
            values["crop"] = record.crop_id
        elif kind == "area":
            values["parent"] = record.parent_id or ""
    return {**values, **overrides}


@pytest.mark.parametrize("hx", [False, True])
def test_register_pages_and_mutations_require_login(client, register_record, hx):
    kind, record = register_record
    headers = {"HTTP_HX_REQUEST": "true"} if hx else {}
    for action in ("list", "create", "detail", "edit", "delete"):
        path = reverse(
            f"garden:{kind}-{action}",
            args=[record.pk] if action in {"detail", "edit", "delete"} else [],
        )
        response = client.get(path, **headers)
        assert response.status_code == (401 if hx else 302)
        assert "no-store" in response["Cache-Control"]
    for action in ("create", "edit", "archive", "delete"):
        path = reverse(f"garden:{kind}-{action}", args=[] if action == "create" else [record.pk])
        response = client.post(
            path, payload(kind, record, archived="true", confirm="on"), **headers
        )
        assert response.status_code == (401 if hx else 302)
    assert type(record).objects.count() == 1


def test_register_compact_list_and_inline_fragment_use_private_templates(
    client, user, register_record
):
    kind, record = register_record
    client.force_login(user)
    response = client.get(reverse(f"garden:{kind}-list"))
    assert response.status_code == 200
    assert '<table class="register-table' in response.text
    assert "table table-sm" in response.text
    assert f'aria-controls="details-{kind}-{record.pk}"' in response.text
    assert 'aria-expanded="false"' in response.text
    assert f'href="{record.get_absolute_url()}"' in response.text
    assert "record-details" not in response.text
    fragment = client.get(record.get_absolute_url(), HTTP_HX_REQUEST="true")
    assert fragment.status_code == 200 and "record-details" in fragment.text
    assert "<!doctype" not in fragment.text and "<script" not in fragment.text
    assert "no-store" in fragment["Cache-Control"]
    assert "unsafe-eval" not in fragment["Content-Security-Policy"]
    full = client.get(record.get_absolute_url())
    assert "<!doctype" in full.text and "record-details" in full.text
    client.cookies["django_language"] = "en"
    english = client.get(reverse(f"garden:{kind}-list"))
    assert '<html lang="en">' in english.text
    assert {"crop": "Crops", "variety": "Varieties", "area": "Growing areas"}[kind] in english.text


def test_normal_create_edit_and_archive_controls(client, user, register_record):
    kind, record = register_record
    client.force_login(user)
    create_path = reverse(f"garden:{kind}-create")
    data = payload(kind, record)
    response = client.post(create_path, data)
    assert response.status_code == 302
    new = type(record).objects.get(name=data["name"])
    assert response.url == new.get_absolute_url()
    data = payload(kind, new, name="Renamed", notes="Preserved note")
    response = client.post(reverse(f"garden:{kind}-edit", args=[new.pk]), data)
    assert response.status_code == 302
    new.refresh_from_db()
    assert new.name == "Renamed"
    archive_path = reverse(f"garden:{kind}-archive", args=[new.pk])
    assert client.get(archive_path).status_code == 405
    assert client.post(archive_path, {"archived": "true"}).status_code == 302
    new.refresh_from_db()
    assert new.is_archived
    assert new not in client.get(reverse(f"garden:{kind}-list")).context["records"]
    assert new in client.get(reverse(f"garden:{kind}-list"), {"archived": "1"}).context["records"]
    assert client.get(new.get_absolute_url()).status_code == 200
    assert client.post(archive_path, {"archived": "false"}).status_code == 302
    assert client.post(archive_path, {"archived": "nonsense"}).status_code == 422


def test_htmx_register_save_redirects_and_invalid_form_replaces_only_errors(
    client, user, register_record
):
    kind, record = register_record
    client.force_login(user)
    path = reverse(f"garden:{kind}-create")
    response = client.post(path, payload(kind, record, name=""), HTTP_HX_REQUEST="true")
    assert response.status_code == 422 and "error-summary" in response.text
    assert 'id="register-form"' not in response.text
    response = client.post(path, payload(kind, record), HTTP_HX_REQUEST="true")
    assert response.status_code == 204 and response["HX-Redirect"].startswith("/garden/")
    assert type(record).objects.count() == 2


def test_duplicate_errors_are_visible_and_fields_and_key_are_preserved(client, user):
    client.force_login(user)
    create_crop(actor=user, name="Ärtor")
    data = payload("crop", name=" A\u0308RTOR ", notes="Keep this note")
    response = client.post(reverse("garden:crop-create"), data)
    assert response.status_code == 422
    assert "name" in response.context["form"].errors
    assert response.context["form"]["notes"].value() == data["notes"]
    assert response.context["form"]["submission_key"].value() == data["submission_key"]
    assert Crop.objects.count() == 1


def test_edit_form_versions_roundtrip_without_truncating_microseconds(
    client, user, register_record
):
    kind, record = register_record
    client.force_login(user)
    response = client.get(reverse(f"garden:{kind}-edit", args=[record.pk]))
    assert f'value="{record.updated_at.isoformat()}"' in response.text
    form = response.context["form"]
    data = payload(kind, record, expected_updated_at=form["expected_updated_at"].value())
    assert client.post(reverse(f"garden:{kind}-edit", args=[record.pk]), data).status_code == 302


def test_edit_rejects_missing_or_stale_version_without_losing_existing_fields(client, user):
    area = create_area(actor=user, name="Bed").record
    client.force_login(user)
    path = reverse("garden:area-edit", args=[area.pk])
    assert client.post(path, payload("area", name="No token")).status_code == 422
    changed = edit_area(
        actor=user, pk=area.pk, name="Changed", expected_updated_at=area.updated_at
    ).record
    response = client.post(path, payload("area", area, name="Old tab"), HTTP_HX_REQUEST="true")
    assert response.status_code == 409
    assert "error-summary" in response.text
    changed.refresh_from_db()
    assert changed.name == "Changed"


def test_parent_choices_exclude_cycles_archives_but_preserve_current_archived_parent(client, user):
    root = create_area(actor=user, name="Root").record
    child = create_area(actor=user, name="Child", parent=root).record
    grandchild = create_area(actor=user, name="Grandchild", parent=child).record
    other = create_area(actor=user, name="Other").record
    archive_area(actor=user, pk=other.pk)
    archive_area(actor=user, pk=root.pk)
    client.force_login(user)
    form = client.get(reverse("garden:area-edit", args=[child.pk])).context["form"]
    offered = set(form.fields["parent"].queryset.values_list("pk", flat=True))
    assert offered == {root.pk}
    assert child.pk not in offered and grandchild.pk not in offered and other.pk not in offered
    root_form = client.get(reverse("garden:area-edit", args=[root.pk])).context["form"]
    assert not root_form.fields["parent"].queryset.exists()
    root.refresh_from_db()
    invalid = client.post(
        reverse("garden:area-edit", args=[root.pk]), payload("area", root, parent=grandchild.pk)
    )
    assert invalid.status_code == 422 and "parent" in invalid.context["form"].errors


def test_variety_choices_exclude_archived_crops_except_current_association(client, user):
    crop = create_crop(actor=user, name="Tomato").record
    other = create_crop(actor=user, name="Bean").record
    variety = create_variety(actor=user, crop=crop, name="Golden").record
    archive_crop(actor=user, pk=crop.pk)
    client.force_login(user)
    new = client.get(reverse("garden:variety-create")).context["form"]
    assert list(new.fields["crop"].queryset) == [other]
    edit = client.get(reverse("garden:variety-edit", args=[variety.pk])).context["form"]
    assert set(edit.fields["crop"].queryset) == {crop, other}
    assert (
        client.post(reverse("garden:variety-create"), payload("variety", crop=crop.pk)).status_code
        == 422
    )


def test_delete_is_get_safe_confirmed_and_protects_child_references(client, user):
    parent = create_area(actor=user, name="Parent").record
    child = create_area(actor=user, name="Child", parent=parent).record
    client.force_login(user)
    path = reverse("garden:area-delete", args=[parent.pk])
    assert client.get(path).status_code == 200 and GrowingArea.objects.count() == 2
    assert client.post(path, payload("area", parent)).status_code == 422
    response = client.post(path, payload("area", parent, confirm="on"))
    assert response.status_code == 422 and response.context["form"].errors
    child_path = reverse("garden:area-delete", args=[child.pk])
    response = client.post(child_path, payload("area", child, confirm="on"))
    assert response.status_code == 302 and not GrowingArea.objects.filter(pk=child.pk).exists()
    assert GrowingArea.objects.filter(pk=parent.pk).exists()


@pytest.mark.parametrize("kind", ["area", "variety", "planting"])
def test_photo_creation_edit_viewer_and_confirmed_delete(client, user, image_upload, kind):
    client.force_login(user)
    data = payload(kind, photos=[image_upload(), image_upload()])
    if kind in {"variety", "planting"}:
        data["crop"] = create_crop(actor=user, name="Tomato").record.pk
    if kind == "planting":
        data = {
            "submission_key": data["submission_key"],
            "crop": data["crop"],
            "start_year": 2026,
            "lifespan": "annual",
            "photos": data["photos"],
        }
    response = client.post(reverse(f"garden:{kind}-create"), data)
    assert response.status_code == 302
    if kind == "planting":
        from garden.models import Planting

        owner = Planting.objects.get()
    else:
        owner = (GrowingArea if kind == "area" else Variety).objects.get()
    assert owner.photos.count() == 2
    photo = owner.photos.first()
    viewer = client.get(photo.get_absolute_url())
    assert viewer.status_code == 200
    assert f'href="{owner.get_absolute_url()}"' in viewer.text
    assert "Tillbaka till posten" in viewer.text
    for variant in ("display", "thumbnail", "original"):
        response = client.get(photo.variant_url(variant))
        assert response.status_code == 200 and "no-store" in response["Cache-Control"]
        assert response["Content-Disposition"].startswith(
            "attachment" if variant == "original" else "inline"
        )
        assert (
            b"".join(response.streaming_content)
            == private_path(getattr(photo, variant).name).read_bytes()
        )
    response = client.post(
        reverse("garden:photo-edit", args=[photo.pk]),
        {
            "caption": "New caption",
            "position": 5,
            "expected_updated_at": owner.updated_at.isoformat(),
        },
    )
    assert response.status_code == 302
    photo.refresh_from_db()
    owner.refresh_from_db()
    assert photo.caption == "New caption" and photo.position == 5
    path = reverse("garden:photo-delete", args=[photo.pk])
    assert client.get(path).status_code == 200
    assert (
        client.post(path, {"expected_updated_at": owner.updated_at.isoformat()}).status_code == 422
    )
    response = client.post(
        path, {"expected_updated_at": owner.updated_at.isoformat(), "confirm": "on"}
    )
    assert response.status_code == 302 and owner.photos.count() == 1
    assert not private_path(photo.original.name).exists()


@pytest.mark.parametrize("kind", ["area", "variety"])
@pytest.mark.parametrize("failure", ["invalid", "count", "file", "total", "unexpected"])
def test_register_multipart_guard_rejects_upload_without_partial_owner(
    client, user, image_upload, settings, kind, failure
):
    client.force_login(user)
    data = payload(kind, photos=[image_upload(), image_upload()])
    if kind == "variety":
        data["crop"] = create_crop(actor=user, name="Tomato").record.pk
    if failure == "invalid":
        data["photos"] = [image_upload(), SimpleUploadedFile("bad.jpg", b"invalid")]
    elif failure == "count":
        settings.PHOTO_MAX_COUNT = 1
    elif failure == "file":
        settings.PHOTO_MAX_FILE_BYTES = 100
    elif failure == "total":
        settings.PHOTO_MAX_TOTAL_BYTES = 100
    else:
        data["unexpected"] = image_upload()
    response = client.post(reverse(f"garden:{kind}-create"), data)
    assert response.status_code == 422 and response.context["form"].errors
    assert not (GrowingArea if kind == "area" else Variety).objects.exists()
    assert not Photo.objects.exists()


def test_native_multipart_count_guard_also_applies_to_register_edit(client, user, image_upload):
    area = create_area(actor=user, name="Bed").record
    client.force_login(user)
    response = client.post(
        reverse("garden:area-edit", args=[area.pk]),
        payload("area", area, photos=[image_upload() for _ in range(11)]),
    )
    assert response.status_code == 422
    assert area.photos.count() == 0
    area.refresh_from_db()
    assert area.name == "Bed"


def test_lost_register_http_response_retries_once_and_conflicts_on_changed_content(
    client, user, image_upload, monkeypatch
):
    from garden import views

    client.force_login(user)
    real_create, count = views.services.create_area, 0

    def lost_response(**kwargs):
        nonlocal count
        result = real_create(**kwargs)
        count += 1
        if count == 1:
            raise OperationalError("Synthetic response lost after commit")
        return result

    monkeypatch.setattr(views.services, "create_area", lost_response)
    key = uuid4()
    path = reverse("garden:area-create")
    assert (
        client.post(
            path, payload("area", submission_key=key, photos=image_upload()), HTTP_HX_REQUEST="true"
        ).status_code
        == 503
    )
    assert (
        client.post(
            path, payload("area", submission_key=key, photos=image_upload()), HTTP_HX_REQUEST="true"
        ).status_code
        == 204
    )
    assert GrowingArea.objects.count() == 1 and Photo.objects.count() == 1
    response = client.post(
        path,
        payload("area", submission_key=key, name="Different", photos=image_upload()),
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 409
    assert GrowingArea.objects.count() == 1 and Photo.objects.count() == 1


@pytest.mark.parametrize("kind", ["variety", "area"])
@pytest.mark.parametrize("hx", [False, True])
@pytest.mark.parametrize("with_photo", [False, True])
def test_register_create_retry_after_archive_preserves_receipt_and_draft(
    client, user, image_upload, monkeypatch, kind, hx, with_photo
):
    from garden import views

    association = (
        create_crop(actor=user, name="Original crop").record
        if kind == "variety"
        else create_area(actor=user, name="Original parent").record
    )
    field = "crop" if kind == "variety" else "parent"
    model = Variety if kind == "variety" else GrowingArea
    writer = getattr(views.services, f"create_{kind}")
    attempts = 0

    def lost_response(**values):
        nonlocal attempts
        result = writer(**values)
        attempts += 1
        if attempts == 1:
            raise OperationalError("Synthetic lost response after commit")
        return result

    monkeypatch.setattr(views.services, f"create_{kind}", lost_response)
    client.force_login(user)
    headers = {"HTTP_HX_REQUEST": "true"} if hx else {}
    path = reverse(f"garden:{kind}-create")
    data = payload(kind, **{field: association.pk})

    def post(**overrides):
        photos = {"photos": image_upload()} if with_photo else {}
        return client.post(path, {**data, **photos, **overrides}, **headers)

    assert post().status_code == 503
    saved = model.objects.get(name=data["name"])
    version = saved.updated_at
    photo_ids = list(saved.photos.values_list("pk", flat=True))
    assert len(photo_ids) == int(with_photo)
    originals = [private_path(photo.original.name) for photo in saved.photos.all()]
    receipt_count = RegisterSubmission.objects.count()
    {"variety": archive_crop, "area": archive_area}[kind](actor=user, pk=association.pk)

    # An ordinary failed form must keep unavailable selections for the next retry.
    invalid = post(name="")
    assert invalid.status_code == 422
    if not hx:
        draft = invalid.context["form"]
        assert f'value="{association.pk}" selected' in str(draft[field])
        assert draft["submission_key"].value() == data["submission_key"]

    response = post()
    assert response.status_code == (204 if hx else 302)
    assert response["HX-Redirect" if hx else "Location"] == saved.get_absolute_url()
    assert post(name="Different content").status_code == 409
    if with_photo:
        assert post(photos=image_upload(color=(100, 20, 30))).status_code == 409
    saved.refresh_from_db()
    assert saved.updated_at == version
    assert model.objects.count() == (1 if kind == "variety" else 2)
    assert list(saved.photos.values_list("pk", flat=True)) == photo_ids
    assert all(original.is_file() for original in originals)
    assert RegisterSubmission.objects.count() == receipt_count


@pytest.mark.parametrize("hx", [False, True])
@pytest.mark.parametrize(
    ("kind", "change"),
    [
        ("variety", "archive"),
        ("variety", "delete"),
        ("area", "archive"),
        ("area", "delete"),
        ("area", "cycle"),
    ],
)
def test_register_edit_retry_after_later_association_change_keeps_latest_record(
    client, user, image_upload, kind, change, hx
):
    if kind == "variety":
        original = create_crop(actor=user, name="Original crop").record
        replacement = create_crop(actor=user, name="Replacement crop").record
        record = create_variety(actor=user, name="Original variety", crop=original).record
        writer, field = edit_variety, "crop"
    else:
        original = create_area(actor=user, name="Original parent").record
        replacement = create_area(actor=user, name="Replacement parent").record
        record = create_area(actor=user, name="Original child", parent=original).record
        writer, field = edit_area, "parent"
    client.force_login(user)
    headers = {"HTTP_HX_REQUEST": "true"} if hx else {}
    path = reverse(f"garden:{kind}-edit", args=[record.pk])
    data = payload(kind, record, name="First edit")
    assert client.post(path, {**data, "photos": image_upload()}, **headers).status_code == (
        204 if hx else 302
    )
    record.refresh_from_db()
    latest = writer(
        actor=user,
        pk=record.pk,
        name="Later edit",
        expected_updated_at=record.updated_at,
        **{field: replacement},
    ).record
    if change == "archive":
        {"variety": archive_crop, "area": archive_area}[kind](actor=user, pk=original.pk)
    elif change == "delete":
        {"variety": delete_crop, "area": delete_area}[kind](
            actor=user, pk=original.pk, expected_updated_at=original.updated_at
        )
    else:
        edit_area(
            actor=user,
            pk=original.pk,
            name=original.name,
            parent=latest,
            expected_updated_at=original.updated_at,
        )
    receipt_count = RegisterSubmission.objects.count()
    photo_ids = list(record.photos.values_list("pk", flat=True))

    response = client.post(path, {**data, "photos": image_upload()}, **headers)
    assert response.status_code == (204 if hx else 302)
    assert response["HX-Redirect" if hx else "Location"] == record.get_absolute_url()
    assert (
        client.post(
            path, {**data, "name": "Changed payload", "photos": image_upload()}, **headers
        ).status_code
        == 409
    )
    assert client.post(path, {**data, "submission_key": str(uuid4())}, **headers).status_code == 409
    record.refresh_from_db()
    assert record.name == latest.name and record.updated_at == latest.updated_at
    assert getattr(record, f"{field}_id") == replacement.pk
    assert list(record.photos.values_list("pk", flat=True)) == photo_ids
    assert RegisterSubmission.objects.count() == receipt_count


@pytest.mark.parametrize("kind", ["variety", "area"])
@pytest.mark.parametrize("hx", [False, True])
def test_register_receipt_after_archive_rejects_other_author(client, user, other_user, kind, hx):
    association = (
        create_crop(actor=user, name="Original crop").record
        if kind == "variety"
        else create_area(actor=user, name="Original parent").record
    )
    field = "crop" if kind == "variety" else "parent"
    data = payload(kind, **{field: association.pk})
    headers = {"HTTP_HX_REQUEST": "true"} if hx else {}
    path = reverse(f"garden:{kind}-create")
    client.force_login(user)
    assert client.post(path, data, **headers).status_code == (204 if hx else 302)
    {"variety": archive_crop, "area": archive_area}[kind](actor=user, pk=association.pk)
    receipt_count = RegisterSubmission.objects.count()
    client.force_login(other_user)
    assert client.post(path, data, **headers).status_code == 409
    assert RegisterSubmission.objects.count() == receipt_count


@pytest.mark.parametrize("kind", ["variety", "area"])
@pytest.mark.parametrize("selection", ["archived", "missing", "0", "-1", "1.5", str(2**63)])
def test_new_register_submission_rejects_ineligible_and_malformed_associations(
    client, user, image_upload, kind, selection
):
    association = (
        create_crop(actor=user, name="Original crop").record
        if kind == "variety"
        else create_area(actor=user, name="Original parent").record
    )
    field = "crop" if kind == "variety" else "parent"
    if selection == "archived":
        {"variety": archive_crop, "area": archive_area}[kind](actor=user, pk=association.pk)
        selection = association.pk
    elif selection == "missing":
        selection = association.pk + 1000
    receipt_count = RegisterSubmission.objects.count()
    client.force_login(user)
    response = client.post(
        reverse(f"garden:{kind}-create"),
        payload(kind, **{field: selection}, photos=image_upload()),
    )
    assert response.status_code == 422 and field in response.context["form"].errors
    assert not Photo.objects.exists()
    assert (
        not (Variety if kind == "variety" else GrowingArea)
        .objects.filter(name="Synthetic new record")
        .exists()
    )
    assert RegisterSubmission.objects.count() == receipt_count


def test_register_csrf_and_shared_member_permissions(user, django_user_model):
    area = create_area(actor=user, name="Bed").record
    other = django_user_model.objects.create_user(username="second-gardener", password="synthetic")
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(other)
    assert (
        csrf_client.post(
            reverse("garden:area-edit", args=[area.pk]), payload("area", area)
        ).status_code
        == 403
    )
    client = Client()
    client.force_login(other)
    assert client.get(area.get_absolute_url()).status_code == 200
    assert (
        client.post(reverse("garden:area-edit", args=[area.pk]), payload("area", area)).status_code
        == 302
    )
    other.is_active = False
    other.save()
    assert client.get(area.get_absolute_url()).status_code == 302


@pytest.mark.parametrize("action", ["archive", "delete", "photo-edit", "photo-delete"])
def test_nonupload_register_actions_reject_unexpected_or_oversized_files(
    client, user, image_upload, settings, action
):
    area = create_area(actor=user, name="Bed", uploads=[image_upload()]).record
    photo = area.photos.get()
    client.force_login(user)
    if action.startswith("photo-"):
        path = reverse(f"garden:{action}", args=[photo.pk])
    else:
        path = reverse(f"garden:area-{action}", args=[area.pk])
    data = {
        "expected_updated_at": area.updated_at.isoformat(),
        "archived": "true",
        "confirm": "on",
        "caption": "Must not save",
        "position": 5,
    }
    assert client.post(path, {**data, "unexpected": image_upload()}).status_code == 422
    settings.PHOTO_MAX_FILE_BYTES = 1
    assert client.post(path, {**data, "unexpected": image_upload()}).status_code == 422
    area.refresh_from_db()
    photo.refresh_from_db()
    assert not area.is_archived and area.photos.count() == 1
    assert photo.caption == "" and photo.position == 0
    assert private_path(photo.original.name).is_file()


def test_register_photo_routes_deny_anonymous_and_do_not_expose_diary_mutation(
    client, user, image_upload
):
    area = create_area(actor=user, name="Bed", uploads=[image_upload()]).record
    photo = area.photos.get()
    for path in (
        photo.get_absolute_url(),
        photo.original_url,
        reverse("garden:photo-edit", args=[photo.pk]),
        reverse("garden:photo-delete", args=[photo.pk]),
    ):
        assert client.get(path).status_code == 302
    diary_photo = create_entry(
        author=user,
        date=date(2026, 5, 9),
        body="",
        uploads=[image_upload()],
        submission_key=uuid4(),
    ).entry.photos.get()
    client.force_login(user)
    for action in ("edit", "delete"):
        path = reverse(f"garden:photo-{action}", args=[diary_photo.pk])
        assert client.get(path).status_code == 403 and client.post(path).status_code == 403


def test_all_register_photo_routes_deny_inactive_and_expired_fragment_sessions(
    client, user, image_upload
):
    area = create_area(actor=user, name="Bed", uploads=[image_upload()]).record
    photo = area.photos.get()
    paths = [photo.get_absolute_url(), photo.display_url, photo.thumbnail_url, photo.original_url]
    for path in paths:
        assert client.get(path).status_code == 302
        response = client.get(path, HTTP_HX_REQUEST="true")
        assert response.status_code == 401 and "no-store" in response["Cache-Control"]
    client.force_login(user)
    user.is_active = False
    user.save(update_fields=["is_active"])
    for path in paths:
        assert client.get(path).status_code == 302


def test_register_user_entered_content_is_escaped_in_pages_fragments_and_photo_viewer(
    client, user, image_upload
):
    hostile = '<script>alert("x")</script>'
    area = create_area(actor=user, name=hostile, notes=hostile, uploads=[image_upload()]).record
    client.force_login(user)
    for path, headers in (
        (reverse("garden:area-list"), {}),
        (area.get_absolute_url(), {}),
        (area.get_absolute_url(), {"HTTP_HX_REQUEST": "true"}),
        (area.photos.get().get_absolute_url(), {}),
    ):
        response = client.get(path, **headers)
        assert response.status_code == 200
        assert hostile not in response.text
        if path != area.photos.get().get_absolute_url():
            assert "&lt;script&gt;" in response.text
        assert "unsafe-inline" not in response["Content-Security-Policy"]
