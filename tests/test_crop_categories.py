import io
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
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
from django.utils.translation import gettext
from PIL import Image

from core.backups import create_backup, open_snapshot, restore_backup, verify_backup
from core.media import private_path
from core.models import Photo
from core.writes import validated_write
from garden.models import Crop, CropCategory, CropCategoryLink, RegisterSubmission, Variety
from garden.services import (
    RegisterConflict,
    archive_crop,
    archive_variety,
    create_category,
    create_crop,
    create_variety,
    delete_category,
    delete_crop,
    edit_category,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_overview_uncategorized_and_scoped_search_preserve_registers(client, user):
    client.force_login(user)
    crop = create_crop(actor=user, name="Tomato").record
    variety = create_variety(actor=user, crop=crop, name="Golden").record
    other = create_crop(actor=user, name="Bean").record
    initial = client.get(reverse("garden:variety-list"))
    assert not list(initial.context["categories"])
    assert reverse("garden:uncategorized") in initial.text
    first = create_category(actor=user, name="Vegetables", crops=[crop]).record
    second = create_category(actor=user, name="Summer", crops=[crop]).record
    overview = client.get(reverse("garden:variety-list"))
    assert list(overview.context["categories"]) == [second, first]
    assert reverse("garden:uncategorized") in overview.text
    scoped = client.get(first.get_absolute_url())
    assert [group["crop"] for group in scoped.context["variety_groups"]] == [crop]
    assert scoped.context["records"] == [variety]
    assert "crop-maintenance" in scoped.text
    assert parse_qs(urlsplit(scoped.context["crop_create_url"]).query) == {
        "category": [str(first.pk)],
        "next": [first.get_absolute_url()],
    }
    assert not client.get(first.get_absolute_url(), {"q": "Bean"}).context["has_records"]
    assert client.get(first.get_absolute_url(), {"q": "gold"}).context["records"] == [variety]
    unlinked = client.get(reverse("garden:uncategorized"))
    assert [group["crop"] for group in unlinked.context["variety_groups"]] == [other]
    archive_crop(actor=user, pk=crop.pk)
    assert client.get(first.get_absolute_url()).context["records"] == [variety]
    first.refresh_from_db()
    delete_category(actor=user, pk=first.pk, expected_updated_at=first.updated_at)
    assert crop.categories.get() == second
    delete_category(actor=user, pk=second.pk, expected_updated_at=second.updated_at)
    assert not list(client.get(reverse("garden:variety-list")).context["categories"])
    assert client.get(reverse("garden:uncategorized")).context["variety_groups"]
    assert Variety.objects.get(pk=variety.pk).crop_id == crop.pk


def test_archive_empty_state_and_legacy_filter_clear(client, user):
    client.force_login(user)
    crop = create_crop(actor=user, name="Tomato").record
    category = create_category(actor=user, name="Vegetables", crops=[crop]).record
    archive_crop(actor=user, pk=crop.pk)
    response = client.get(category.get_absolute_url())
    assert response.context["empty_action_url"] == category.get_absolute_url() + "?archived=1"
    assert client.get(category.get_absolute_url(), {"archived": "1"}).context["has_records"]
    response = client.get(
        reverse("garden:variety-list"),
        headers={"HX-Request": "true", "HX-Target": "register-results"},
    )
    assert response.status_code == 200 and 'id="register-results"' in response.text
    assert "category-grid" not in response.text


@pytest.mark.parametrize("archived", [False, True])
@pytest.mark.parametrize("partial", [False, True])
def test_empty_uncategorized_offers_new_crop_when_all_crops_are_linked(
    client, user, archived, partial
):
    client.force_login(user)
    crop = create_crop(actor=user, name="Tomato").record
    variety = create_variety(actor=user, crop=crop, name="Golden").record
    create_category(actor=user, name="Vegetables", crops=[crop])
    if archived:
        archive_variety(actor=user, pk=variety.pk)
        archive_crop(actor=user, pk=crop.pk)
    headers = {"HX-Request": "true", "HX-Target": "register-results"} if partial else {}
    for filters in ({}, {"archived": "1"}):
        response = client.get(reverse("garden:uncategorized"), filters, headers=headers)
        assert response.status_code == 200
        assert not response.context["has_records"]
        destination = reverse("garden:uncategorized") + (
            "?" + urlencode(filters) if filters else ""
        )
        action = urlsplit(response.context["empty_action_url"])
        assert action.path == reverse("garden:crop-create")
        assert parse_qs(action.query) == {"next": [destination]}
        assert response.context["empty_action_label"] == gettext("New crop")


def test_uncategorized_archive_action_opens_unlinked_records(client, user):
    client.force_login(user)
    linked = create_crop(actor=user, name="Tomato").record
    create_category(actor=user, name="Vegetables", crops=[linked])
    unlinked = create_crop(actor=user, name="Bean").record
    archive_crop(actor=user, pk=unlinked.pk)
    response = client.get(reverse("garden:uncategorized"))
    assert not response.context["has_records"]
    assert response.context["empty_action_url"] == reverse("garden:uncategorized") + "?archived=1"
    archived = client.get(response.context["empty_action_url"])
    assert [group["crop"] for group in archived.context["variety_groups"]] == [unlinked]


@pytest.mark.parametrize("encoding", ["PNG", "WEBP"])
def test_category_transparency_and_heading_image(user, client, encoding):
    buffer = io.BytesIO()
    with Image.new("RGBA", (16, 16), (0, 0, 0, 0)) as source:
        source.putpixel((8, 8), (35, 110, 50, 128))
        source.putpixel((9, 9), (35, 110, 50, 255))
        source.save(buffer, format=encoding, **({"lossless": True} if encoding == "WEBP" else {}))
    upload = SimpleUploadedFile(
        "synthetic." + encoding.lower(), buffer.getvalue(), "image/" + encoding.lower()
    )
    category = create_category(actor=user, name="Transparent plants", uploads=[upload]).record
    photo = category.photos.get()
    for field in (photo.display, photo.thumbnail):
        with Image.open(private_path(field.name)) as derivative:
            assert derivative.mode == "RGBA"
            assert derivative.getpixel((0, 0))[3] == 0
            assert derivative.getpixel((8, 8))[3] == 128
            assert derivative.getpixel((9, 9))[3] == 255
    client.force_login(user)
    response = client.get(category.get_absolute_url())
    assert response.context["category_image"] == photo
    assert f'class="register-illustration" src="{photo.display_url}"' in response.text
    assert reverse("garden:category-create") not in response.text
    unlinked = client.get(reverse("garden:uncategorized"))
    assert (
        'class="register-illustration" src="/static/images/registers/crops-and-varieties.webp"'
        in unlinked.text
    )
    assert reverse("garden:category-create") not in unlinked.text


def test_legacy_filters_redirect_to_full_register_and_overview_always_has_create(client, user):
    client.force_login(user)
    response = client.get(reverse("garden:variety-list"), {"q": "Golden", "archived": "1"})
    assert response.status_code == 302
    assert response.url == reverse("garden:all-crops") + "?q=Golden&archived=1"
    for path in (reverse("garden:variety-list"), reverse("garden:all-crops")):
        response = client.get(path)
        assert (reverse("garden:category-create") in response.text) == (
            path == reverse("garden:variety-list")
        )


def test_membership_validation_versions_and_receipts(user, other_user):
    crop = create_crop(actor=user, name="Tomato").record
    category = create_category(actor=user, name=" Årt ", crops=[crop]).record
    assert category.name == "Årt"
    with pytest.raises(ValidationError):
        create_category(actor=user, name="A\u030aRT")
    key = uuid4()
    version = category.updated_at
    saved = edit_category(
        actor=user,
        pk=category.pk,
        name="Summer",
        crops=[crop, crop],
        submission_key=key,
        expected_updated_at=version,
    )
    assert CropCategoryLink.objects.count() == 1
    archive_crop(actor=user, pk=crop.pk)
    assert edit_category(
        actor=user,
        pk=category.pk,
        name="Summer",
        crops=[crop],
        submission_key=key,
        expected_updated_at=version,
    ).replayed
    with pytest.raises(RegisterConflict):
        edit_category(
            actor=other_user,
            pk=category.pk,
            name="Summer",
            crops=[crop],
            submission_key=key,
            expected_updated_at=version,
        )
    with pytest.raises(RegisterConflict):
        edit_category(actor=user, pk=category.pk, name="Stale", expected_updated_at=version)
    with pytest.raises(ValidationError):
        create_category(actor=user, name="Archived", crops=[crop])
    category = saved.record
    delete_category(actor=user, pk=category.pk, expected_updated_at=category.updated_at)
    with pytest.raises(RegisterConflict):
        edit_category(
            actor=user,
            pk=category.pk,
            name="Summer",
            crops=[crop],
            submission_key=key,
            expected_updated_at=version,
        )
    assert RegisterSubmission.objects.get(key=key).category_id is None


def test_crop_creation_in_category_is_atomic_and_invalidates_membership_edit(user):
    category = create_category(actor=user, name="Flowers").record
    version = category.updated_at
    key = uuid4()
    crop = create_crop(actor=user, name="Dahlia", category=category, submission_key=key).record
    assert category.crops.get() == crop
    assert create_crop(actor=user, name="Dahlia", category=category, submission_key=key).replayed
    with pytest.raises(RegisterConflict):
        edit_category(actor=user, pk=category.pk, name=category.name, expected_updated_at=version)
    category.refresh_from_db()
    version = category.updated_at
    delete_crop(actor=user, pk=crop.pk, expected_updated_at=crop.updated_at)
    category.refresh_from_db()
    assert category.updated_at != version
    delete_category(actor=user, pk=category.pk, expected_updated_at=category.updated_at)
    with pytest.raises(RegisterConflict):
        create_crop(actor=user, name="Missing category", category=category.pk)
    assert not Crop.objects.filter(name="Missing category").exists()


@pytest.mark.parametrize("format", ["JPEG", "PNG", "WEBP", "HEIF"])
def test_category_image_replacement_removal_and_retries(user, image_upload, format):
    category = create_category(
        actor=user, name="Flowers", uploads=[image_upload(format=format)]
    ).record
    original = category.photos.get()
    with Image.open(private_path(original.display.name)) as derivative:
        assert derivative.format == "WEBP" and not derivative.getexif()
    version = category.updated_at
    key = uuid4()
    saved = edit_category(
        actor=user,
        pk=category.pk,
        name=category.name,
        uploads=[image_upload()],
        submission_key=key,
        expected_updated_at=version,
    ).record
    replacement = saved.photos.get()
    assert replacement.pk != original.pk
    assert not private_path(original.original.name).exists()
    assert edit_category(
        actor=user,
        pk=category.pk,
        name=category.name,
        uploads=[image_upload()],
        submission_key=key,
        expected_updated_at=version,
    ).replayed
    assert saved.photos.get().pk == replacement.pk
    with pytest.raises(ValidationError):
        edit_category(
            actor=user,
            pk=category.pk,
            name=category.name,
            uploads=[image_upload(), image_upload()],
            expected_updated_at=saved.updated_at,
        )
    with pytest.raises(ValidationError):
        edit_category(
            actor=user,
            pk=category.pk,
            name="Invalid image",
            uploads=[SimpleUploadedFile("bad.jpg", b"broken")],
            expected_updated_at=saved.updated_at,
        )
    saved.refresh_from_db()
    assert saved.name == category.name and saved.photos.get() == replacement
    key = uuid4()
    version = saved.updated_at
    edit_category(
        actor=user,
        pk=category.pk,
        name=category.name,
        remove_image=True,
        submission_key=key,
        expected_updated_at=version,
    )
    assert edit_category(
        actor=user,
        pk=category.pk,
        name=category.name,
        remove_image=True,
        submission_key=key,
        expected_updated_at=version,
    ).replayed
    assert not saved.photos.exists() and not private_path(replacement.original.name).exists()


def test_failed_image_replacement_keeps_original_and_retries_cleanly(
    user, image_upload, monkeypatch
):
    category = create_category(actor=user, name="Flowers", uploads=[image_upload()]).record
    original = category.photos.get()
    key = uuid4()
    payload = dict(
        actor=user,
        pk=category.pk,
        name="Summer",
        submission_key=key,
        expected_updated_at=category.updated_at,
    )
    save_receipt = RegisterSubmission.save

    def fail_receipt(*args, **kwargs):
        raise OperationalError("Synthetic failure after replacing photo rows")

    monkeypatch.setattr(RegisterSubmission, "save", fail_receipt)
    with pytest.raises(OperationalError):
        edit_category(**payload, uploads=[image_upload(color=(100, 50, 20))])
    category.refresh_from_db()
    assert category.name == "Flowers" and category.photos.get() == original
    assert private_path(original.original.name).is_file()
    assert not RegisterSubmission.objects.filter(key=key).exists()
    monkeypatch.setattr(RegisterSubmission, "save", save_receipt)
    saved = edit_category(**payload, uploads=[image_upload(color=(100, 50, 20))]).record
    assert saved.name == "Summer" and saved.photos.get().pk != original.pk
    assert not private_path(original.original.name).exists()


def test_category_photo_database_constraints(user, image_upload):
    category = create_category(actor=user, name="Flowers", uploads=[image_upload()]).record
    photo = category.photos.get()
    values = {
        field.name: getattr(photo, field.name) for field in Photo._meta.fields if field.name != "id"
    }
    with pytest.raises(IntegrityError), transaction.atomic(), validated_write():
        Photo.objects.create(**values)
    values["variety"] = create_variety(
        actor=user, crop=create_crop(actor=user, name="Tomato").record, name="Golden"
    ).record
    with pytest.raises(IntegrityError), transaction.atomic(), validated_write():
        Photo.objects.create(**values)


def test_http_management_and_media_authorization(client, user, other_user, image_upload):
    crop = create_crop(actor=user, name="Tomato").record
    category = create_category(
        actor=user, name="Vegetables", crops=[crop], uploads=[image_upload()]
    ).record
    photo = category.photos.get()
    urls = [
        reverse("garden:variety-list"),
        category.get_absolute_url(),
        reverse("garden:category-create"),
        reverse("garden:category-edit", args=[category.pk]),
        reverse("garden:category-delete", args=[category.pk]),
        photo.get_absolute_url(),
        photo.display_url,
        photo.original_url,
    ]
    for url in urls:
        assert client.get(url).status_code == 302
    client.force_login(other_user)
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200
        response.close()
    response = client.get(photo.original_url)
    assert response["Content-Disposition"].startswith("attachment;")
    response.close()
    payload = {"name": "Herbs", "crop_ids": [crop.pk], "submission_key": uuid4()}
    response = client.post(reverse("garden:category-create"), payload)
    assert response.status_code == 302
    herbs = CropCategory.objects.get(name="Herbs")
    assert herbs.crops.get() == crop
    response = client.post(
        reverse("garden:crop-create"),
        {"name": "Mint", "category": herbs.pk, "submission_key": uuid4()},
    )
    assert response.status_code == 302 and herbs.crops.filter(name="Mint").exists()
    response = client.post(
        reverse("garden:category-delete", args=[category.pk]),
        {"confirm": True, "expected_updated_at": category.updated_at.isoformat()},
    )
    assert response.status_code == 302 and Crop.objects.filter(pk=crop.pk).exists()
    assert not private_path(photo.original.name).exists()
    other_user.is_active = False
    other_user.save(update_fields=["is_active"])
    with pytest.raises(PermissionDenied):
        create_category(actor=other_user, name="Inactive")


def test_overlapping_category_membership_edits_reject_lost_updates(user):
    category = create_category(actor=user, name="Summer").record
    crops = [create_crop(actor=user, name=name).record for name in ["Tomato", "Bean"]]
    barrier = threading.Barrier(2)

    def save(crop):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return edit_category(
                actor=user,
                pk=category.pk,
                name=category.name,
                crops=[crop],
                expected_updated_at=category.updated_at,
            )
        except RegisterConflict:
            return None
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, crops))
    assert sum(result is not None for result in results) == 1
    assert category.crops.count() == 1


def test_crop_creation_overlapping_category_edit_keeps_new_membership(user):
    category = create_category(actor=user, name="Summer").record
    existing = create_crop(actor=user, name="Tomato").record
    barrier = threading.Barrier(2)

    def worker(create):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            if create:
                return create_crop(actor=user, name="Bean", category=category).record
            with suppress(RegisterConflict):
                edit_category(
                    actor=user,
                    pk=category.pk,
                    name=category.name,
                    crops=[existing],
                    expected_updated_at=category.updated_at,
                )
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, create) for create in (True, False)]
        created = futures[0].result(timeout=15)
        futures[1].result(timeout=15)
    assert category.crops.filter(pk=created.pk).exists()


def test_upgrade_discards_only_variety_category():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    before = [("garden", "0002_plantings"), ("core", "0004_planting_photo_owner")]
    try:
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        crop = apps.get_model("garden", "Crop").objects.create(
            name="Tomato", name_key="tomato", notes="Keep notes"
        )
        variety = apps.get_model("garden", "Variety").objects.create(
            crop_id=crop.pk,
            name="Golden",
            name_key="golden",
            category="Discard",
            description="Keep description",
        )
        MigrationExecutor(connection).migrate(latest)
        assert Crop.objects.get(pk=crop.pk).notes == "Keep notes"
        assert Variety.objects.get(pk=variety.pk).description == "Keep description"
        assert Variety.objects.get(pk=variety.pk).crop_id == crop.pk
        assert "category" not in {field.name for field in Variety._meta.fields}
        assert not CropCategory.objects.exists()
    finally:
        MigrationExecutor(connection).migrate(latest)


def test_clean_restore_preserves_categories_links_image_and_validated_writes(
    user, image_upload, tmp_path
):
    crop = create_crop(actor=user, name="Tomato").record
    category = create_category(
        actor=user, name="Vegetables", crops=[crop], uploads=[image_upload()]
    ).record
    second = create_category(actor=user, name="Summer", crops=[crop]).record
    photo = category.photos.get()
    config = tmp_path / "operator.env"
    config.write_text("GROWBOOK_SECRET_KEY=" + "synthetic-key-" * 5)
    backup = tmp_path / "backup"
    create_backup(backup, [config])
    verify_backup(backup)
    restored = tmp_path / "restored"
    restore_backup(backup, restored)
    with open_snapshot(restored / "db.sqlite3") as snapshot:
        assert snapshot.execute(
            "SELECT category_id, crop_id FROM garden_cropcategorylink ORDER BY category_id"
        ).fetchall() == [(category.pk, crop.pk), (second.pk, crop.pk)]
        assert snapshot.execute("SELECT category_id FROM core_photo").fetchone() == (category.pk,)
    assert (restored / "media" / photo.original.name).read_bytes() == private_path(
        photo.original.name
    ).read_bytes()
    with Image.open(restored / "media" / photo.display.name) as derivative:
        assert derivative.format == "WEBP" and not derivative.getexif()
    script = """
import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'growbook.settings.development'
import django
django.setup()
from core.models import User
from garden.models import CropCategory
from garden.services import create_crop
category = CropCategory.objects.get(name='Vegetables')
crop = create_crop(
    actor=User.objects.get(username='gardener'), name='Restored bean', category=category
).record
assert category.crops.filter(pk=crop.pk).exists()
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


def test_category_variety_creation_retains_scope_and_filters(client, user, planting_catalog):
    crop, _, _, _ = planting_catalog
    category = create_category(actor=user, name="Vegetables", crops=[crop]).record
    unrelated = create_crop(actor=user, name="Rose").record
    destination = category.get_absolute_url() + "?q=tom&archived=1"
    client.force_login(user)
    page = client.get(destination)
    target = page.context["create_url"]
    form = client.get(target)
    assert form.context["cancel_url"] == destination
    assert not form.context["form"].fields["crop"].queryset.filter(pk=unrelated.pk).exists()
    response = client.post(
        reverse("garden:variety-create"),
        {
            "crop": crop.pk,
            "name": "New variety",
            "submission_key": uuid4(),
            "next": destination,
        },
        HTTP_HX_REQUEST="true",
    )
    assert response["HX-Redirect"] == destination
