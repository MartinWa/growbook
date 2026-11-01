"""The only supported diary writer: all photos and the entry commit together."""

import hashlib
import json
import logging
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date as Date
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.utils.translation import gettext as _

from core.images import process_staged, publish_staged, stage_uploads
from core.media import media_operation
from core.models import Photo, User
from core.writes import validated_write

from .models import DiaryEntry, DiarySubmission

logger = logging.getLogger(__name__)


class SubmissionConflict(ValidationError):
    pass


class SubmissionDeleted(ValidationError):
    pass


@dataclass(frozen=True)
class CaptureResult:
    entry: DiaryEntry
    created: bool


def payload_fingerprint(date, body, photos):
    payload = {
        "version": 1,
        "date": date.isoformat(),
        "body": body,
        "photos": [photo.sha256 for photo in photos],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def check_receipt(key, author_id, fingerprint):
    receipt = DiarySubmission.objects.select_related("entry").filter(key=key).first()
    if receipt is None:
        return None
    if receipt.author_id != author_id or receipt.payload_sha256 != fingerprint:
        raise SubmissionConflict(
            _(
                "This save key belongs to a different submission. "
                "Start a new entry instead; nothing was overwritten."
            )
        )
    if receipt.entry_id is None:
        raise SubmissionDeleted(_("This entry was already deleted. It was not recreated."))
    return CaptureResult(receipt.entry, created=False)


def create_entry(*, author, date, body, uploads, submission_key):
    """Process outside atomic; revalidate shared state inside the serialized write."""
    if connection.in_atomic_block:
        raise RuntimeError("Call create_entry outside any existing transaction.")
    if not author.is_authenticated or not author.is_active:
        raise PermissionDenied
    if not isinstance(date, Date):
        raise ValidationError(_("Choose a valid entry date."))
    try:
        key = UUID(str(submission_key))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError(_("The save key is invalid. Reload the capture form.")) from exc
    body = body.strip()
    uploads = list(uploads)
    if not body and not uploads:
        raise ValidationError(_("Write a note or add at least one photo."))
    with media_operation():
        staging_root = settings.MEDIA_ROOT / ".staging"
        staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        staging = Path(tempfile.mkdtemp(prefix="capture-", dir=staging_root))
        published = []
        committed = False
        try:
            photos = stage_uploads(uploads, staging)
            fingerprint = payload_fingerprint(date, body, photos)
            # Match retries against the receipt, not mutable entry fields.
            with transaction.atomic():
                if not User.objects.filter(pk=author.pk, is_active=True).exists():
                    raise PermissionDenied
                result = check_receipt(key, author.pk, fingerprint)
                if result:
                    return result
            for photo in photos:
                process_staged(photo)
            rows = []
            for position, photo in enumerate(photos):
                values, folder = publish_staged(photo)
                published.append(folder)
                rows.append({**values, "position": position})
            with transaction.atomic(), validated_write():
                if not User.objects.filter(pk=author.pk, is_active=True).exists():
                    raise PermissionDenied
                result = check_receipt(key, author.pk, fingerprint)
                if result:
                    return result
                entry = DiaryEntry(date=date, body=body, author=author, submission_key=key)
                entry.full_clean()
                entry.save()
                for values in rows:
                    image = Photo(entry=entry, **values)
                    image.full_clean()
                    image.save()
                receipt = DiarySubmission(
                    key=key, author=author, payload_sha256=fingerprint, entry=entry
                )
                receipt.full_clean()
                receipt.save()
            committed = True
            return CaptureResult(entry, created=True)
        finally:
            if not committed:
                # Do not delete potentially committed files after an uncertain commit error.
                # Confirm references using a fresh autocommit query; otherwise leave an orphan.
                for folder in published:
                    relative = folder.relative_to(settings.MEDIA_ROOT).as_posix() + "/"
                    try:
                        if not Photo.objects.filter(original__startswith=relative).exists():
                            shutil.rmtree(folder)
                    except Exception:
                        logger.exception("Deferred uncertain media cleanup to cleanup_media.")
            shutil.rmtree(staging, ignore_errors=True)
