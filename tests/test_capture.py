import io
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, OperationalError, connection, transaction
from PIL import Image, ImageCms

from core.cleanup import cleanup_orphans
from core.media import private_path
from core.models import Photo
from core.writes import validated_write
from diary.models import DiaryEntry, DiarySubmission
from diary.services import SubmissionConflict, SubmissionDeleted, create_entry

pytestmark = pytest.mark.django_db(transaction=True)


def capture(user, *, body="Synthetic observation", uploads=(), key=None):
    return create_entry(
        author=user,
        date=date(2026, 5, 9),
        body=body,
        uploads=uploads,
        submission_key=key or uuid4(),
    )


def assert_no_save(settings):
    assert DiaryEntry.objects.count() == 0
    assert DiarySubmission.objects.count() == 0
    assert Photo.objects.count() == 0
    assert list(settings.MEDIA_ROOT.glob("photos/*/*/*/*")) == []
    assert list((settings.MEDIA_ROOT / ".staging").glob("capture-*")) == []


def test_text_only_entry(user):
    result = capture(user, body="  Ärtor in bloom\nSecond line  ")
    assert result.created
    assert result.entry.body == "Ärtor in bloom\nSecond line"
    assert result.entry.author == user
    assert result.entry.photos.count() == 0
    assert DiarySubmission.objects.get(key=result.entry.submission_key).entry == result.entry


@pytest.mark.parametrize("format", ["JPEG", "PNG", "WEBP", "HEIF"])
def test_photo_only_entry_and_safe_derivatives(user, image_upload, format):
    upload = image_upload(format=format)
    original_bytes = upload.read()
    upload.seek(0)
    result = capture(user, body="", uploads=[upload])
    photo = result.entry.photos.get()
    assert photo.original_format == format
    assert private_path(photo.original.name).read_bytes() == original_bytes
    assert photo.original_bytes == len(original_bytes)
    for field in (photo.display, photo.thumbnail):
        with Image.open(private_path(field.name)) as image:
            assert image.format == "WEBP"
            assert image.size == (80, 60)
            assert not image.getexif()
            assert not any(key in image.info for key in ("exif", "xmp", "icc_profile"))


def test_orientation_metadata_privacy_and_original_retention(user, image_upload):
    exif = Image.Exif()
    exif[274] = 6
    exif[306] = "2020:02:03 04:05:06"
    exif[270] = "Synthetic private metadata"
    exif[34853] = {1: "N", 2: (59.0, 0.0, 0.0), 3: "E", 4: (18.0, 0.0, 0.0)}
    result = capture(user, uploads=[image_upload(size=(80, 60), exif=exif)])
    photo = result.entry.photos.get()
    assert (photo.width, photo.height) == (60, 80)
    assert result.entry.date == date(2026, 5, 9)
    with Image.open(private_path(photo.original.name)) as original:
        assert original.getexif()[274] == 6
        assert 34853 in original.getexif()
    with Image.open(private_path(photo.display.name)) as display:
        assert display.size == (60, 80)
        assert not display.getexif()


def test_color_profile_is_converted_without_propagating_metadata(user, image_upload):
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    result = capture(user, uploads=[image_upload(icc_profile=profile)])
    with Image.open(private_path(result.entry.photos.get().display.name)) as image:
        assert "icc_profile" not in image.info


def test_invalid_color_profile_rejects_entire_save(user, image_upload, settings):
    with pytest.raises(ValidationError):
        capture(user, uploads=[image_upload(icc_profile=b"not-a-profile")])
    assert_no_save(settings)


def test_png_palette_transparency_is_preserved_without_metadata(user):
    buffer = io.BytesIO()
    image = Image.new("P", (10, 10), color=0)
    image.putpalette([0, 0, 0, 0, 255, 0] + [0] * (768 - 6))
    image.info["transparency"] = 0
    image.save(buffer, format="PNG")
    result = capture(
        user, body="", uploads=[SimpleUploadedFile("transparent.png", buffer.getvalue())]
    )
    with Image.open(private_path(result.entry.photos.get().display.name)) as derivative:
        assert derivative.mode == "RGBA"
        assert derivative.getpixel((0, 0))[3] == 0
        assert not derivative.getexif()


def test_derivative_limits_do_not_upscale(user, image_upload, settings):
    settings.PHOTO_DISPLAY_EDGE = 64
    settings.PHOTO_THUMBNAIL_EDGE = 16
    result = capture(user, uploads=[image_upload(size=(160, 80))])
    photo = result.entry.photos.get()
    with Image.open(private_path(photo.display.name)) as display:
        assert display.size == (64, 32)
    with Image.open(private_path(photo.thumbnail.name)) as thumbnail:
        assert thumbnail.size == (16, 8)


def test_several_photos_have_stable_order(user, image_upload):
    result = capture(user, uploads=[image_upload(color=(n, 80, 80)) for n in (10, 20, 30)])
    assert list(result.entry.photos.values_list("position", flat=True)) == [0, 1, 2]


@pytest.mark.parametrize("body", ["", "  ", "\n\t"])
def test_empty_entry_is_rejected(user, body, settings):
    with pytest.raises(ValidationError):
        capture(user, body=body)
    assert_no_save(settings)


def test_a_rejected_photo_does_not_save_text_or_other_photos(user, image_upload, settings):
    bad = SimpleUploadedFile("pretend.jpg", b"<svg onload='alert(1)'/>", "image/jpeg")
    with pytest.raises(ValidationError):
        capture(user, uploads=[image_upload(), bad])
    assert_no_save(settings)


@pytest.mark.parametrize("format", ["GIF", "BMP", "TIFF"])
def test_unsupported_format_is_rejected(user, image_upload, settings, format):
    with pytest.raises(ValidationError):
        capture(user, uploads=[image_upload(format=format)])
    assert_no_save(settings)


def test_animated_webp_is_rejected(user, settings):
    buffer = io.BytesIO()
    image = Image.new("RGB", (10, 10), "red")
    image.save(
        buffer,
        format="WEBP",
        save_all=True,
        append_images=[Image.new("RGB", (10, 10), "blue")],
        duration=100,
        loop=0,
    )
    with pytest.raises(ValidationError):
        capture(user, uploads=[SimpleUploadedFile("animation.webp", buffer.getvalue())])
    assert_no_save(settings)


def test_multi_image_heif_is_rejected(user, settings):
    data = (Path(__file__).parent / "fixtures" / "synthetic-multi.heic").read_bytes()
    with pytest.raises(ValidationError):
        capture(user, uploads=[SimpleUploadedFile("multiple.heic", data)])
    assert_no_save(settings)


def test_apple_hdr_jpeg_uses_primary_image_and_preserves_original(user, apple_hdr_upload):
    exif = Image.Exif()
    exif[274] = 6
    exif[34853] = {1: "N", 2: (59.0, 0.0, 0.0)}
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    upload = apple_hdr_upload(exif=exif, icc_profile=profile)
    original_bytes = upload.read()
    upload.seek(0)
    with Image.open(io.BytesIO(original_bytes)) as image:
        assert image.format == "MPO" and image.n_frames == 2
    result = capture(user, body="", uploads=[upload])
    photo = result.entry.photos.get()
    assert photo.original_format == "JPEG"
    assert (photo.width, photo.height) == (60, 80)
    assert private_path(photo.original.name).read_bytes() == original_bytes
    with Image.open(private_path(photo.display.name)) as image:
        assert image.format == "WEBP" and image.size == (60, 80)
        red, green, blue = image.getpixel((30, 40))
        assert green > red and green > blue
        assert not image.getexif()
        assert not any(key in image.info for key in ("xmp", "icc_profile", "exif"))


@pytest.mark.parametrize(
    "kind",
    [
        "plain",
        "wrong-type",
        "malformed",
        "entities",
        "rgb",
        "oversize",
        "three-images",
        "primary-only",
    ],
)
def test_general_or_malformed_multi_picture_jpeg_is_rejected(
    user, apple_hdr_upload, settings, kind
):
    options = {}
    if kind == "plain":
        options["xmp"] = b""
    elif kind == "wrong-type":
        options["xmp"] = (
            b'<x xmlns="http://ns.apple.com/pixeldatainfo/1.0/"><AuxiliaryImageType>other</AuxiliaryImageType></x>'
        )
    elif kind == "malformed":
        options["xmp"] = b"<broken"
    elif kind == "entities":
        options["xmp"] = (
            b'<!DOCTYPE x [<!ENTITY marker "urn:com:apple:photo:2020:aux:hdrgainmap">]><x xmlns="http://ns.apple.com/pixeldatainfo/1.0/"><AuxiliaryImageType>&marker;</AuxiliaryImageType></x>'
        )
    elif kind == "rgb":
        options["aux_mode"] = "RGB"
    elif kind == "oversize":
        options["aux_size"] = (81, 60)
    elif kind == "three-images":
        options["extra_images"] = [Image.new("RGB", (10, 10), "blue")]
    elif kind == "primary-only":
        options["xmp"] = b""
        options["primary_xmp"] = (
            b'<x xmlns="http://ns.apple.com/pixeldatainfo/1.0/"><AuxiliaryImageType>urn:com:apple:photo:2020:aux:hdrgainmap</AuxiliaryImageType></x>'
        )
    with pytest.raises(ValidationError):
        capture(user, uploads=[apple_hdr_upload(**options)])
    assert_no_save(settings)


def test_apple_hdr_jpeg_counts_auxiliary_pixels_against_limit(user, apple_hdr_upload, settings):
    settings.PHOTO_MAX_PIXELS = 5_999
    with pytest.raises(ValidationError):
        capture(user, uploads=[apple_hdr_upload()])
    assert_no_save(settings)


def test_truncated_apple_hdr_auxiliary_image_rejects_entire_save(user, apple_hdr_upload, settings):
    data = apple_hdr_upload().read()
    with pytest.raises(ValidationError):
        capture(user, uploads=[SimpleUploadedFile("truncated.jpeg", data[:-100], "image/jpeg")])
    assert_no_save(settings)


def test_corrupt_heic_codestream_rejects_entire_save(user, settings):
    # The container stays parseable while the image bitstream ends early; libheif
    # reports that as EOFError, which must not escape as an unhandled server error.
    fixture = Path(__file__).parent / "fixtures" / "corrupt-heic-codestream.heic"
    upload = SimpleUploadedFile("corrupt.heic", fixture.read_bytes(), "image/heic")
    with pytest.raises(ValidationError):
        capture(user, uploads=[upload])
    assert_no_save(settings)


@pytest.mark.parametrize("header_byte", [0, 3])
def test_corrupt_apple_hdr_auxiliary_header_rejects_entire_save(
    user, image_upload, corrupt_apple_hdr_upload, settings, header_byte
):
    with pytest.raises(ValidationError):
        capture(user, uploads=[image_upload(), corrupt_apple_hdr_upload(header_byte=header_byte)])
    assert_no_save(settings)


def test_pixel_limit_is_checked_before_decode(user, image_upload, settings):
    settings.PHOTO_MAX_PIXELS = 100
    with pytest.raises(ValidationError):
        capture(user, uploads=[image_upload(size=(20, 20))])
    assert_no_save(settings)


def test_file_byte_limit(user, image_upload, settings):
    settings.PHOTO_MAX_FILE_BYTES = 100
    with pytest.raises(ValidationError):
        capture(user, uploads=[image_upload()])
    assert_no_save(settings)


def test_total_byte_limit(user, image_upload, settings):
    settings.PHOTO_MAX_TOTAL_BYTES = 1000
    with pytest.raises(ValidationError):
        capture(user, uploads=[image_upload(), image_upload()])
    assert_no_save(settings)


def test_photo_count_limit(user, image_upload, settings):
    settings.PHOTO_MAX_COUNT = 1
    with pytest.raises(ValidationError):
        capture(user, uploads=[image_upload(), image_upload()])
    assert_no_save(settings)


def test_empty_file_is_rejected(user, settings):
    with pytest.raises(ValidationError):
        capture(user, uploads=[SimpleUploadedFile("empty.png", b"")])
    assert_no_save(settings)


def test_identical_retry_returns_original_entry(user, image_upload):
    key = uuid4()
    first = capture(user, key=key, uploads=[image_upload()])
    retry = capture(user, key=key, uploads=[image_upload()])
    assert first.created and not retry.created
    assert retry.entry.pk == first.entry.pk
    assert DiaryEntry.objects.count() == 1
    assert Photo.objects.count() == 1


def test_changed_payload_conflicts_without_overwriting(user):
    key = uuid4()
    first = capture(user, key=key)
    with pytest.raises(SubmissionConflict):
        capture(user, key=key, body="Different content")
    first.entry.refresh_from_db()
    assert first.entry.body == "Synthetic observation"
    assert DiaryEntry.objects.count() == 1


def test_same_key_cannot_be_reused_by_another_author(user, django_user_model):
    first = capture(user)
    other = django_user_model.objects.create_user(username="another-gardener", password="synthetic")
    with pytest.raises(SubmissionConflict):
        capture(other, key=first.entry.submission_key)
    assert DiaryEntry.objects.count() == 1


def test_retry_uses_immutable_receipt_not_later_edits(user):
    first = capture(user)
    # Mutate the row to prove retries use the receipt; the application has no edit writer.
    with transaction.atomic(), validated_write():
        first.entry.body = "Later edited text"
        first.entry.save()
    retry = capture(user, key=first.entry.submission_key)
    assert retry.entry.body == "Later edited text"
    assert not retry.created
    with pytest.raises(SubmissionConflict):
        capture(user, key=first.entry.submission_key, body="Later edited text")


def test_deleted_receipt_prevents_resurrection(user):
    first = capture(user)
    key = first.entry.submission_key
    # Remove the row to exercise the receipt's missing-entry safeguard.
    with transaction.atomic(), validated_write():
        first.entry.delete()
    assert DiarySubmission.objects.get(key=key).entry_id is None
    with pytest.raises(SubmissionDeleted):
        capture(user, key=key)
    assert DiaryEntry.objects.count() == 0


def test_invalid_key_is_rejected(user):
    with pytest.raises(ValidationError):
        capture(user, key="not-a-uuid")


def test_deactivated_author_is_rejected(user):
    user.is_active = False
    with pytest.raises(PermissionDenied):
        capture(user)


def test_author_deactivation_during_processing_is_revalidated(
    user, image_upload, monkeypatch, settings
):
    from diary import services

    original = services.process_staged

    def deactivate(photo):
        original(photo)
        type(user).objects.filter(pk=user.pk).update(is_active=False)

    monkeypatch.setattr(services, "process_staged", deactivate)
    with pytest.raises(PermissionDenied):
        capture(user, uploads=[image_upload()])
    assert_no_save(settings)


def test_image_processing_and_publication_are_outside_atomic(user, image_upload, monkeypatch):
    from diary import services

    original_process = services.process_staged
    original_publish = services.publish_staged

    def process(photo):
        assert not connection.in_atomic_block
        return original_process(photo)

    def publish(photo):
        assert not connection.in_atomic_block
        return original_publish(photo)

    monkeypatch.setattr(services, "process_staged", process)
    monkeypatch.setattr(services, "publish_staged", publish)
    capture(user, uploads=[image_upload()])


def test_entry_operation_rejects_caller_wrapping_entire_request_in_atomic(user):
    with transaction.atomic(), pytest.raises(RuntimeError, match="outside"):
        capture(user)


def test_derivative_disk_failure_is_retryable_not_an_invalid_photo(
    user, image_upload, monkeypatch, settings
):
    original_save = Image.Image.save

    def disk_full(image, destination, *args, **kwargs):
        if kwargs.get("format") == "WEBP":
            raise OSError("Synthetic derivative disk full")
        return original_save(image, destination, *args, **kwargs)

    upload = image_upload()
    monkeypatch.setattr(Image.Image, "save", disk_full)
    with pytest.raises(OSError):
        capture(user, uploads=[upload])
    assert_no_save(settings)


def test_truncated_image_does_not_save_partial_entry(user, image_upload, settings):
    data = image_upload().read()[:100]
    with pytest.raises(ValidationError):
        capture(user, uploads=[SimpleUploadedFile("truncated.jpg", data)])
    assert_no_save(settings)


def test_failed_publication_cleans_previously_published_bundles(
    user, image_upload, monkeypatch, settings
):
    from core import images

    original = images.os.rename
    calls = 0

    def fail_second(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("Synthetic disk full")
        return original(source, target)

    monkeypatch.setattr(images.os, "rename", fail_second)
    with pytest.raises(OSError):
        capture(user, uploads=[image_upload(), image_upload()])
    assert_no_save(settings)


def test_sync_failure_after_publication_leaves_no_rows_or_bundle(
    user, image_upload, monkeypatch, settings
):
    from core import images

    def fail_sync(path):
        raise OSError("Synthetic flush failure")

    monkeypatch.setattr(images, "sync_directory", fail_sync)
    with pytest.raises(OSError):
        capture(user, uploads=[image_upload()])
    assert_no_save(settings)


def test_failed_database_commit_removes_unreferenced_files(
    user, image_upload, monkeypatch, settings
):
    real_commit = connection.commit
    calls = 0

    def fail_second_commit():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OperationalError("Synthetic commit failure")
        real_commit()

    monkeypatch.setattr(connection, "commit", fail_second_commit)
    with pytest.raises(OperationalError):
        capture(user, uploads=[image_upload()])
    assert_no_save(settings)


def test_uncertain_commit_preserves_referenced_files_and_can_be_retried(
    user, image_upload, monkeypatch
):
    real_commit = connection.commit
    calls = 0
    key = uuid4()

    def uncertain_commit():
        nonlocal calls
        calls += 1
        real_commit()
        if calls == 2:
            raise OperationalError("Synthetic lost commit acknowledgement")

    monkeypatch.setattr(connection, "commit", uncertain_commit)
    with pytest.raises(OperationalError):
        capture(user, key=key, uploads=[image_upload()])
    monkeypatch.undo()
    retry = capture(user, key=key, uploads=[image_upload()])
    assert not retry.created
    assert private_path(retry.entry.photos.get().original.name).is_file()
    assert cleanup_orphans(apply=True, minimum_age=0) == []


@pytest.mark.parametrize("operation", ["save", "update", "bulk_create", "bulk_update", "delete"])
def test_direct_orm_writers_are_rejected(user, operation):
    entry = capture(user).entry
    with pytest.raises(RuntimeError, match="validated domain"):
        if operation == "save":
            entry.save()
        elif operation == "update":
            DiaryEntry.objects.filter(pk=entry.pk).update(body="bypass")
        elif operation == "bulk_create":
            DiaryEntry.objects.bulk_create([])
        elif operation == "bulk_update":
            DiaryEntry.objects.bulk_update([entry], ["body"])
        else:
            entry.delete()


def test_photo_owner_check_is_enforced_in_database():
    with transaction.atomic(), pytest.raises(IntegrityError), connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO core_photo "
            "(original, display, thumbnail, original_sha256, original_format, original_bytes, "
            "width, height, caption, position, uploaded_at, entry_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            ["a", "b", "c", "0" * 64, "JPEG", 1, 1, 1, "", 0, "2026-05-09", None],
        )
