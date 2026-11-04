from datetime import date
from uuid import uuid4

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, OperationalError, connection, transaction
from PIL import Image

from core.cleanup import cleanup_orphans
from core.media import private_path
from core.models import Photo
from core.writes import validated_write
from diary.services import create_entry
from garden.models import Crop, GrowingArea, Planting, RegisterSubmission, Variety
from garden.services import (
    RegisterConflict,
    create_area,
    create_crop,
    create_planting,
    create_variety,
    delete_area,
    delete_planting,
    delete_register_photo,
    delete_variety,
    edit_area,
    edit_planting,
    edit_register_photo,
    edit_variety,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(params=["area", "variety", "planting"])
def register_owner(request, user):
    if request.param == "area":
        return create_area(actor=user, name="Synthetic bed").record
    crop = create_crop(actor=user, name="Tomato").record
    if request.param == "planting":
        return create_planting(
            actor=user, crop=crop, start_year=2026, label="Synthetic batch"
        ).record
    return create_variety(actor=user, crop=crop, name="Synthetic variety").record


def owner_name(owner):
    return owner.label if isinstance(owner, Planting) else owner.name


def save_owner(user, owner, *, name=None, uploads=(), key=None, **overrides):
    values = {
        "actor": user,
        "pk": owner.pk,
        "uploads": uploads,
        "submission_key": key or uuid4(),
        "expected_updated_at": owner.updated_at,
        **overrides,
    }
    if isinstance(owner, Planting):
        return edit_planting(
            **values,
            crop=owner.crop_id,
            start_year=owner.start_year,
            label=name or owner_name(owner),
            notes="Synthetic edited notes",
        )
    values["name"] = name or owner.name
    if isinstance(owner, GrowingArea):
        return edit_area(**values, parent=owner.parent_id, notes="Synthetic edited notes")
    return edit_variety(**values, crop=owner.crop_id, description="Synthetic description")


def delete_owner(user, owner):
    writer = (
        delete_planting
        if isinstance(owner, Planting)
        else delete_area
        if isinstance(owner, GrowingArea)
        else delete_variety
    )
    writer(actor=user, pk=owner.pk, expected_updated_at=owner.updated_at)


def test_new_owners_share_private_pipeline_and_append_in_stable_order(
    user, register_owner, image_upload
):
    owner = register_owner
    owner = save_owner(
        user, owner, uploads=[image_upload(format="HEIF"), image_upload(format="PNG")]
    ).record
    save_owner(user, owner, uploads=[image_upload(format="WEBP")])
    assert list(owner.photos.values_list("position", flat=True)) == [0, 1, 2]
    assert list(owner.photos.values_list("original_format", flat=True)) == ["HEIF", "PNG", "WEBP"]
    for photo in owner.photos.all():
        assert photo.entry_id is None and photo.owner.pk == owner.pk
        assert private_path(photo.original.name).is_file()
        with Image.open(private_path(photo.display.name)) as derivative:
            assert derivative.format == "WEBP" and not derivative.getexif()
    assert cleanup_orphans(apply=True, minimum_age=0) == []


def test_photo_creation_receipts_prevent_duplicate_owner_and_photos(
    user, register_owner, image_upload
):
    owner = register_owner
    retry_payload = dict(
        actor=user, name="Synthetic photo record", uploads=[image_upload()], submission_key=uuid4()
    )
    if isinstance(owner, Planting):
        retry_payload["label"] = retry_payload.pop("name")
        saved = create_planting(**retry_payload, crop=owner.crop_id, start_year=2026)
        replayed = create_planting(
            **{**retry_payload, "uploads": [image_upload()]}, crop=owner.crop_id, start_year=2026
        )
    elif isinstance(owner, Variety):
        saved = create_variety(**retry_payload, crop=owner.crop_id)
        replayed = create_variety(
            **{**retry_payload, "uploads": [image_upload()]}, crop=owner.crop_id
        )
    else:
        saved = create_area(**retry_payload)
        replayed = create_area(**{**retry_payload, "uploads": [image_upload()]})
    assert not saved.replayed and replayed.replayed and saved.record.pk == replayed.record.pk
    assert replayed.record.photos.count() == 1


def test_photo_edit_failure_keeps_old_fields_and_photos(
    user, register_owner, image_upload, settings
):
    owner = register_owner
    save_owner(user, owner, uploads=[image_upload()])
    owner.refresh_from_db()
    previous_name, version = owner_name(owner), owner.updated_at
    old_photo = owner.photos.get()
    with pytest.raises(ValidationError):
        save_owner(
            user,
            owner,
            name="Must not save",
            uploads=[image_upload(), SimpleUploadedFile("bad.jpg", b"invalid")],
        )
    owner.refresh_from_db()
    assert (owner_name(owner), owner.updated_at) == (previous_name, version)
    assert owner.photos.count() == 1 and private_path(old_photo.original.name).is_file()
    assert len(list(settings.MEDIA_ROOT.glob("photos/*/*/*"))) == 1
    assert list((settings.MEDIA_ROOT / ".staging").iterdir()) == []


def test_failed_photo_create_saves_no_owner_or_receipt(user, image_upload, settings):
    with pytest.raises(ValidationError):
        create_area(
            actor=user, name="No area", uploads=[image_upload(), SimpleUploadedFile("bad", b"bad")]
        )
    assert not GrowingArea.objects.exists() and not RegisterSubmission.objects.exists()
    assert not Photo.objects.exists()
    assert not list(settings.MEDIA_ROOT.glob("photos/*/*/*"))


def test_identical_photo_edit_retry_never_appends_twice_even_after_later_edit(
    user, register_owner, image_upload
):
    owner, key = register_owner, uuid4()
    first = save_owner(
        user, owner, uploads=[image_upload()], key=key, expected_updated_at=owner.updated_at
    )
    save_owner(user, first.record, name="Later name")
    retry = save_owner(
        user, owner, uploads=[image_upload()], key=key, expected_updated_at=owner.updated_at
    )
    assert retry.replayed and owner_name(retry.record) == "Later name"
    assert retry.record.photos.count() == 1
    with pytest.raises(RegisterConflict):
        save_owner(
            user,
            owner,
            uploads=[image_upload(color=(100, 20, 30))],
            key=key,
            expected_updated_at=owner.updated_at,
        )
    assert retry.record.photos.count() == 1


def test_photo_retry_key_cannot_be_reused_by_another_member(
    user, register_owner, django_user_model, image_upload
):
    key = uuid4()
    save_owner(user, register_owner, key=key, uploads=[image_upload()])
    other = django_user_model.objects.create_user(username="other", password="synthetic")
    with pytest.raises(RegisterConflict):
        save_owner(other, register_owner, key=key, uploads=[image_upload()])
    assert register_owner.photos.count() == 1


def test_deleted_owner_receipt_does_not_recreate_record_or_photos(user, image_upload, settings):
    key = uuid4()
    owner = create_area(
        actor=user, name="Deleted", uploads=[image_upload()], submission_key=key
    ).record
    delete_area(actor=user, pk=owner.pk, expected_updated_at=owner.updated_at)
    with pytest.raises(RegisterConflict):
        create_area(actor=user, name="Deleted", uploads=[image_upload()], submission_key=key)
    assert not GrowingArea.objects.exists() and not Photo.objects.exists()
    assert not list(settings.MEDIA_ROOT.glob("photos/*/*/*"))


@pytest.mark.parametrize(
    "limit",
    ["PHOTO_MAX_COUNT", "PHOTO_MAX_FILE_BYTES", "PHOTO_MAX_TOTAL_BYTES", "PHOTO_MAX_PIXELS"],
)
def test_register_photo_limits_reject_complete_save(
    user, register_owner, image_upload, settings, limit
):
    setattr(settings, limit, 1)
    with pytest.raises(ValidationError):
        save_owner(user, register_owner, uploads=[image_upload(), image_upload()])
    assert register_owner.photos.count() == 0
    assert not list(settings.MEDIA_ROOT.glob("photos/*/*/*"))


def test_owner_photo_count_limit_includes_existing_images(
    user, register_owner, image_upload, settings
):
    settings.PHOTO_MAX_COUNT = 2
    owner = save_owner(user, register_owner, uploads=[image_upload(), image_upload()]).record
    with pytest.raises(ValidationError):
        save_owner(user, owner, uploads=[image_upload()])
    assert register_owner.photos.count() == 2
    assert len(list(settings.MEDIA_ROOT.glob("photos/*/*/*"))) == 2


def test_register_photo_captions_order_and_last_photo_removal(user, register_owner, image_upload):
    owner = save_owner(user, register_owner, uploads=[image_upload(), image_upload()]).record
    first, second = owner.photos.all()
    updated = edit_register_photo(
        actor=user,
        pk=second.pk,
        caption="Synthetic caption",
        position=0,
        expected_updated_at=owner.updated_at,
    )
    assert updated.caption == "Synthetic caption"
    assert list(owner.photos.values_list("pk", flat=True)) == [first.pk, second.pk]
    owner.refresh_from_db()
    edit_register_photo(
        actor=user, pk=first.pk, caption="First", position=10, expected_updated_at=owner.updated_at
    )
    assert list(owner.photos.values_list("pk", flat=True)) == [second.pk, first.pk]
    bundle = private_path(first.original.name).parent
    owner.refresh_from_db()
    delete_register_photo(actor=user, pk=first.pk, expected_updated_at=owner.updated_at)
    assert not bundle.exists() and owner.photos.count() == 1
    owner.refresh_from_db()
    delete_register_photo(actor=user, pk=second.pk, expected_updated_at=owner.updated_at)
    assert owner.photos.count() == 0 and type(owner).objects.filter(pk=owner.pk).exists()


def test_photo_writes_invalidate_open_owner_form_versions(user, register_owner, image_upload):
    owner = save_owner(user, register_owner, uploads=[image_upload()]).record
    photo = owner.photos.get()
    version = owner.updated_at
    edit_register_photo(
        actor=user, pk=photo.pk, caption="Changed", position=0, expected_updated_at=version
    )
    with pytest.raises(RegisterConflict):
        save_owner(user, owner, name="Stale edit", expected_updated_at=version)
    with pytest.raises(RegisterConflict):
        delete_register_photo(actor=user, pk=photo.pk, expected_updated_at=version)
    assert owner.photos.count() == 1


@pytest.mark.parametrize("position", [-1, 1.5, True])
def test_photo_order_is_validated(user, register_owner, image_upload, position):
    photo = save_owner(user, register_owner, uploads=[image_upload()]).record.photos.get()
    with pytest.raises(ValidationError):
        edit_register_photo(
            actor=user,
            pk=photo.pk,
            caption="No change",
            position=position,
            expected_updated_at=photo.owner.updated_at,
        )
    photo.refresh_from_db()
    assert photo.caption == "" and photo.position == 0


def test_register_photo_mutations_cannot_edit_or_delete_diary_photos(user, image_upload):
    entry = create_entry(
        author=user,
        date=date(2026, 5, 9),
        body="",
        uploads=[image_upload()],
        submission_key=uuid4(),
    ).entry
    photo = entry.photos.get()
    with pytest.raises(PermissionDenied):
        edit_register_photo(
            actor=user, pk=photo.pk, caption="No", position=1, expected_updated_at=entry.updated_at
        )
    with pytest.raises(PermissionDenied):
        delete_register_photo(actor=user, pk=photo.pk, expected_updated_at=entry.updated_at)
    assert entry.photos.count() == 1 and private_path(photo.original.name).is_file()


def test_allowed_owner_delete_removes_rows_and_files_but_not_crop(
    user, register_owner, image_upload
):
    owner = save_owner(user, register_owner, uploads=[image_upload(), image_upload()]).record
    paths = [private_path(photo.original.name).parent for photo in owner.photos.all()]
    delete_owner(user, owner)
    assert not type(owner).objects.filter(pk=owner.pk).exists()
    assert not Photo.objects.exists() and all(not folder.exists() for folder in paths)
    if isinstance(owner, Variety):
        assert Crop.objects.filter(pk=owner.crop_id).exists()


def test_rejected_parent_delete_keeps_photo_bundles(user, image_upload):
    parent = create_area(actor=user, name="Parent", uploads=[image_upload()]).record
    create_area(actor=user, name="Child", parent=parent)
    photo = parent.photos.get()
    with pytest.raises(ValidationError):
        delete_area(actor=user, pk=parent.pk, expected_updated_at=parent.updated_at)
    assert parent.photos.count() == 1 and private_path(photo.original.name).is_file()


def test_register_image_processing_is_outside_transactions_and_revalidates_active_user(
    user, register_owner, image_upload, monkeypatch, settings
):
    from core import photo_storage

    real_process, real_publish = photo_storage.process_staged, photo_storage.publish_staged

    def process(photo):
        assert not connection.in_atomic_block
        return real_process(photo)

    def publish(photo):
        assert not connection.in_atomic_block
        result = real_publish(photo)
        type(user).objects.filter(pk=user.pk).update(is_active=False)
        return result

    monkeypatch.setattr(photo_storage, "process_staged", process)
    monkeypatch.setattr(photo_storage, "publish_staged", publish)
    with pytest.raises(PermissionDenied):
        save_owner(user, register_owner, uploads=[image_upload()])
    assert not Photo.objects.exists() and not list(settings.MEDIA_ROOT.glob("photos/*/*/*"))


def test_reparent_during_processing_is_revalidated_before_save(user, image_upload, monkeypatch):
    from core import photo_storage

    first = create_area(actor=user, name="First").record
    second = create_area(actor=user, name="Second").record
    real_process = photo_storage.process_staged

    def changed_parent(photo):
        real_process(photo)
        # No media lock is required for a text-only hierarchy edit; it can overlap decoding.
        edit_area(
            actor=user,
            pk=second.pk,
            name=second.name,
            parent=first,
            expected_updated_at=second.updated_at,
        )

    monkeypatch.setattr(photo_storage, "process_staged", changed_parent)
    with pytest.raises(ValidationError):
        edit_area(
            actor=user,
            pk=first.pk,
            name=first.name,
            parent=second,
            uploads=[image_upload()],
            expected_updated_at=first.updated_at,
        )
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.parent_id is None and second.parent_id == first.pk
    assert not Photo.objects.exists()


def test_photo_publication_failure_cleans_only_new_unreferenced_bundles(
    user, register_owner, image_upload, settings, monkeypatch
):
    from core import photo_storage

    register_owner = save_owner(user, register_owner, uploads=[image_upload()]).record
    existing = register_owner.photos.get()
    real_publish, count = photo_storage.publish_staged, 0

    def fail_second(photo):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("Synthetic publication failure")
        return real_publish(photo)

    monkeypatch.setattr(photo_storage, "publish_staged", fail_second)
    with pytest.raises(OSError):
        save_owner(user, register_owner, uploads=[image_upload(), image_upload()])
    assert register_owner.photos.count() == 1 and private_path(existing.original.name).is_file()
    assert len(list(settings.MEDIA_ROOT.glob("photos/*/*/*"))) == 1


def test_failed_save_commit_removes_only_unreferenced_new_bundles(
    user, register_owner, image_upload, monkeypatch, settings
):
    owner = register_owner
    real_commit, count = connection.commit, 0

    def failed_commit():
        nonlocal count
        count += 1
        if count == 2:
            raise OperationalError("Synthetic failed register commit")
        real_commit()

    monkeypatch.setattr(connection, "commit", failed_commit)
    with pytest.raises(OperationalError):
        save_owner(user, owner, uploads=[image_upload()])
    monkeypatch.undo()
    assert not owner.photos.exists()
    assert not list(settings.MEDIA_ROOT.glob("photos/*/*/*"))


def test_register_derivative_disk_failure_is_retryable_not_invalid_input(
    user, register_owner, image_upload, monkeypatch
):
    from core import photo_storage

    def disk_failure(photo):
        raise OSError("Synthetic derivative disk failure")

    monkeypatch.setattr(photo_storage, "process_staged", disk_failure)
    with pytest.raises(OSError):
        save_owner(user, register_owner, uploads=[image_upload()])
    assert not register_owner.photos.exists()


def test_uncertain_save_commit_preserves_referenced_photos_and_retry_resolves(
    user, register_owner, image_upload, monkeypatch
):
    key, owner = uuid4(), register_owner
    real_commit, count = connection.commit, 0

    def uncertain_commit():
        nonlocal count
        count += 1
        real_commit()
        if count == 2:
            raise OperationalError("Synthetic lost commit acknowledgement")

    monkeypatch.setattr(connection, "commit", uncertain_commit)
    with pytest.raises(OperationalError):
        save_owner(
            user, owner, uploads=[image_upload()], key=key, expected_updated_at=owner.updated_at
        )
    monkeypatch.undo()
    retry = save_owner(
        user, owner, uploads=[image_upload()], key=key, expected_updated_at=owner.updated_at
    )
    assert retry.replayed and retry.record.photos.count() == 1
    assert private_path(retry.record.photos.get().original.name).is_file()
    assert cleanup_orphans(apply=True, minimum_age=0) == []


def test_failed_delete_commit_never_removes_still_referenced_files(
    user, register_owner, image_upload, monkeypatch
):
    owner = save_owner(user, register_owner, uploads=[image_upload()]).record
    photo = owner.photos.get()

    def failed_commit():
        raise OperationalError("Synthetic rollback")

    monkeypatch.setattr(connection, "commit", failed_commit)
    with pytest.raises(OperationalError):
        delete_owner(user, owner)
    monkeypatch.undo()
    assert (
        Photo.objects.filter(pk=photo.pk).exists() and private_path(photo.original.name).is_file()
    )


def test_file_delete_is_after_commit_and_failed_cleanup_is_retryable(
    user, register_owner, image_upload, monkeypatch
):
    from core import photo_storage

    owner = save_owner(user, register_owner, uploads=[image_upload()]).record
    bundle = private_path(owner.photos.get().original.name).parent
    real_remove = photo_storage.shutil.rmtree

    def disk_failure(path, *args, **kwargs):
        if path == bundle:
            assert not connection.in_atomic_block and not owner.photos.exists()
            raise OSError("Synthetic delete failure")
        return real_remove(path, *args, **kwargs)

    monkeypatch.setattr(photo_storage.shutil, "rmtree", disk_failure)
    delete_owner(user, owner)
    assert bundle.exists() and not Photo.objects.exists()
    monkeypatch.undo()
    assert cleanup_orphans(apply=True, minimum_age=0) == [
        bundle.relative_to(bundle.parents[3]).as_posix()
    ]
    assert not bundle.exists()


@pytest.mark.parametrize("prefix", ["capture", "register", "upload"])
def test_cleanup_recognizes_current_and_legacy_staging_directories(settings, prefix):
    import os

    relative = f".staging/{prefix}-abandoned"
    abandoned = settings.MEDIA_ROOT / relative
    abandoned.mkdir(parents=True)
    os.utime(abandoned, (0, 0))
    assert cleanup_orphans(minimum_age=1) == [relative]
    assert abandoned.exists()
    assert cleanup_orphans(apply=True, minimum_age=1) == [relative]
    assert not abandoned.exists()


def test_exactly_one_photo_owner_database_check_covers_all_four_kinds(user, image_upload):
    entry = create_entry(
        author=user,
        date=date(2026, 5, 9),
        body="",
        uploads=[image_upload()],
        submission_key=uuid4(),
    ).entry
    area = create_area(actor=user, name="Bed", uploads=[image_upload()]).record
    crop = create_crop(actor=user, name="Tomato").record
    variety = create_variety(actor=user, crop=crop, name="Golden", uploads=[image_upload()]).record
    planting = create_planting(
        actor=user, crop=crop, start_year=2026, uploads=[image_upload()]
    ).record
    for owner in (entry, area, variety, planting):
        photo = owner.photos.get()
        assert photo.owner.pk == owner.pk and type(photo.owner) is type(owner)
    from itertools import product

    photo = entry.photos.get()
    for selected in product([False, True], repeat=4):
        if sum(selected) == 1:
            continue
        owners = {
            f"{kind}_id": owner.pk if present else None
            for kind, owner, present in zip(
                ("entry", "area", "variety", "planting"),
                (entry, area, variety, planting),
                selected,
                strict=True,
            )
        }
        with pytest.raises(IntegrityError), transaction.atomic(), validated_write():
            Photo.objects.filter(pk=photo.pk).update(**owners)
    assert Photo.objects.count() == 4
