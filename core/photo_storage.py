"""Small coordinated helpers for immutable photo bundles; callers hold media_operation."""

import logging
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.db.models import Q

from .images import process_staged, publish_staged, stage_uploads
from .media import private_path
from .models import Photo

logger = logging.getLogger(__name__)


@contextmanager
def staged_uploads(uploads):
    if connection.in_atomic_block:
        raise RuntimeError("Stage photos outside any existing transaction.")
    root = Path(settings.MEDIA_ROOT) / ".staging"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = Path(tempfile.mkdtemp(prefix="register-", dir=root))
    try:
        yield stage_uploads(uploads, staging)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def discard_unreferenced_bundles(folders):
    """Never delete a bundle if commit/reference state is uncertain; cleanup can retry."""
    if connection.in_atomic_block:
        raise RuntimeError("Remove photo files only after the database transaction.")
    root = Path(settings.MEDIA_ROOT).resolve()
    for folder in set(folders):
        try:
            folder = Path(folder).resolve()
            relative = folder.relative_to(root).as_posix()
            parts = Path(relative).parts
            if len(parts) != 4 or parts[0] != "photos" or folder == root:
                raise ValueError("Not a generated photo bundle.")
            prefix = relative + "/"
            if Photo.objects.filter(
                Q(original__startswith=prefix)
                | Q(display__startswith=prefix)
                | Q(thumbnail__startswith=prefix)
            ).exists():
                continue
            if folder.exists():
                shutil.rmtree(folder)
        except Exception:
            logger.exception("Deferred uncertain media cleanup to cleanup_media.")


def photo_bundles(photos):
    return [private_path(photo.original.name).parent for photo in photos]


@contextmanager
def published_uploads(photos):
    if connection.in_atomic_block:
        raise RuntimeError("Process and publish photos outside any existing transaction.")
    folders = []
    try:
        for photo in photos:
            process_staged(photo)
        rows = []
        for photo in photos:
            values, folder = publish_staged(photo)
            folders.append(folder)
            rows.append(values)
        yield rows
    finally:
        # Also safe after a successful commit: referenced bundles are always kept.
        discard_unreferenced_bundles(folders)
