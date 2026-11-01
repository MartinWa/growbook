"""Bounded still-image decode/re-encode with private, immutable file publication."""

import hashlib
import io
import os
import shutil
import warnings
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext as _
from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

from .media import private_path

register_heif_opener(thumbnails=False)
Image.MAX_IMAGE_PIXELS = 50_000_000
FORMATS = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
    "HEIF": ("heic", "image/heic"),
}


@dataclass
class StagedPhoto:
    directory: Path
    sha256: str
    byte_count: int
    format: str = ""
    width: int = 0
    height: int = 0


def stage_uploads(uploads, staging):
    if len(uploads) > settings.PHOTO_MAX_COUNT:
        raise ValidationError(
            _("Select no more than %(count)s photos."), params={"count": settings.PHOTO_MAX_COUNT}
        )
    staged = []
    total = 0
    for index, upload in enumerate(uploads):
        folder = staging / str(index)
        folder.mkdir(mode=0o700)
        count = 0
        checksum = hashlib.sha256()
        with (folder / "original").open("xb") as destination:
            for chunk in upload.chunks():
                count += len(chunk)
                total += len(chunk)
                if count > settings.PHOTO_MAX_FILE_BYTES:
                    raise ValidationError(_("A photo is too large. The per-photo limit is 20 MiB."))
                if total > settings.PHOTO_MAX_TOTAL_BYTES:
                    raise ValidationError(_("The selected photos exceed the total upload limit."))
                checksum.update(chunk)
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        if not count:
            raise ValidationError(_("An empty photo file was rejected."))
        staged.append(StagedPhoto(folder, checksum.hexdigest(), count))
    return staged


def decode_still(source):
    """Decode only input content here; storage errors must remain retryable OSErrors."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as original:
                if original.format not in FORMATS:
                    raise ValidationError(_("Use a JPEG, PNG, WebP or HEIC still photo."))
                if getattr(original, "n_frames", 1) != 1:
                    raise ValidationError(_("Animated and multi-image files are not supported."))
                if original.width * original.height > settings.PHOTO_MAX_PIXELS:
                    raise ValidationError(_("This photo exceeds the decoded pixel limit."))
                detected = original.format
                original.load()  # Truncated data is not accepted.
                ImageOps.exif_transpose(original, in_place=True)
                image = original
                has_alpha = "A" in image.getbands() or "transparency" in image.info
                if image.info.get("icc_profile"):
                    try:
                        image = ImageCms.profileToProfile(
                            image,
                            ImageCms.ImageCmsProfile(io.BytesIO(image.info["icc_profile"])),
                            ImageCms.createProfile("sRGB"),
                            outputMode="RGBA" if has_alpha else "RGB",
                        )
                    except (ImageCms.PyCMSError, OSError, ValueError) as exc:
                        raise ValidationError(
                            _("This photo has an unreadable color profile.")
                        ) from exc
                converted = image.convert("RGBA" if has_alpha else "RGB")
                # A new image prevents incidental metadata propagation through Pillow plugins.
                clean = Image.new(converted.mode, converted.size)
                clean.paste(converted)
                return clean, detected
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        raise ValidationError(
            _("A photo could not be decoded safely. No entry was saved.")
        ) from exc


def render_derivatives(source, display_path, thumbnail_path):
    """Return detected format/dimensions; derivative storage failures are retryable."""
    clean, detected = decode_still(source)
    try:
        width, height = clean.size
        for edge, output in [
            (settings.PHOTO_DISPLAY_EDGE, display_path),
            (settings.PHOTO_THUMBNAIL_EDGE, thumbnail_path),
        ]:
            with clean.copy() as derivative:
                derivative.thumbnail((edge, edge), Image.Resampling.LANCZOS)
                derivative.save(output, format="WEBP", quality=85, method=4)
            with Path(output).open("r+b") as stream:
                os.fsync(stream.fileno())
        return detected, width, height
    finally:
        clean.close()


def process_staged(photo):
    photo.format, photo.width, photo.height = render_derivatives(
        photo.directory / "original",
        photo.directory / "display.webp",
        photo.directory / "thumbnail.webp",
    )


def sync_directory(path):
    # Windows has no portable directory fsync. NTFS rename semantics are used there.
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def sync_directory_tree(root):
    """Flush child directories first so all newly created path components survive."""
    directories = [path for path in Path(root).rglob("*") if path.is_dir()]
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        sync_directory(directory)
    sync_directory(root)


def publish_staged(photo):
    """Publish a complete private bundle BEFORE its database rows can commit."""
    identifier = uuid4().hex
    period = timezone.now().strftime("%Y/%m")
    relative = f"photos/{period}/{identifier}"
    target = private_path(relative)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    extension = FORMATS[photo.format][0]
    (photo.directory / "original").rename(photo.directory / f"original.{extension}")
    for file in photo.directory.iterdir():
        file.chmod(0o600)
    os.rename(photo.directory, target)  # Staging and permanent media share a filesystem.
    try:
        sync_directory(target)
        root = Path(settings.MEDIA_ROOT).resolve()
        parent = target.parent
        while parent.is_relative_to(root):
            sync_directory(parent)
            if parent == root:
                break
            parent = parent.parent
    except OSError:
        # Publication has not returned and no database writer can reference this bundle.
        shutil.rmtree(target, ignore_errors=True)
        raise
    return {
        "original": f"{relative}/original.{extension}",
        "display": f"{relative}/display.webp",
        "thumbnail": f"{relative}/thumbnail.webp",
        "original_sha256": photo.sha256,
        "original_format": photo.format,
        "original_bytes": photo.byte_count,
        "width": photo.width,
        "height": photo.height,
    }, target
