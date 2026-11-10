"""The only supported diary writer: all photos and the entry commit together."""

import hashlib
import json
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime
from uuid import UUID, uuid4

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from core.media import media_operation
from core.models import Photo, User
from core.photo_storage import (
    discard_unreferenced_bundles,
    photo_bundles,
    published_uploads,
    staged_uploads,
)
from core.writes import validated_write
from garden.models import Crop, GrowingArea, Planting, Variety

from .models import DiaryEditSubmission, DiaryEntry, DiaryLink, DiarySubmission
from .permissions import can_edit_entry
from .search import entry_search_text


class SubmissionConflict(ValidationError):
    pass


class SubmissionDeleted(ValidationError):
    pass


class EntryConflict(ValidationError):
    """The entry changed, disappeared, or was deleted while the form was open."""


@dataclass(frozen=True)
class CaptureResult:
    entry: DiaryEntry
    created: bool


def payload_fingerprint(date, body, photos, links=(), *, title=""):
    payload = {
        "version": 1,
        "date": date.isoformat(),
        "body": body,
        "photos": [photo.sha256 for photo in photos],
    }
    # Keep existing unlinked receipts byte-for-byte compatible.
    if links:
        payload["links"] = sorted(links)
    # Blank titles preserve receipts issued before titles were supported.
    if title:
        payload["title"] = title
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


def _normalize_title(title):
    if not isinstance(title, str):
        raise ValidationError({"title": _("Enter a title, or leave it blank.")})
    try:
        return DiaryEntry._meta.get_field("title").clean(title.strip(), None)
    except ValidationError as exc:
        raise ValidationError({"title": exc}) from exc


def create_entry(*, author, date, body, uploads, submission_key, links=(), title=""):
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
    title = _normalize_title(title)
    body = body.strip()
    links = _normalize_links(links)
    uploads = list(uploads)
    if not body and not uploads:
        raise ValidationError(_("Write a note or add at least one photo."))
    with media_operation(), staged_uploads(uploads) as photos:
        fingerprint = payload_fingerprint(date, body, photos, links, title=title)
        # Match retries before decoding against the receipt, not mutable entry fields.
        with transaction.atomic():
            if not User.objects.filter(pk=author.pk, is_active=True).exists():
                raise PermissionDenied
            result = check_receipt(key, author.pk, fingerprint)
            if result:
                return result
        with published_uploads(photos) as rows:
            with transaction.atomic(), validated_write():
                if not User.objects.filter(pk=author.pk, is_active=True).exists():
                    raise PermissionDenied
                result = check_receipt(key, author.pk, fingerprint)
                if result:
                    return result
                resolved_links = _resolve_links(links)
                entry = DiaryEntry(
                    date=date,
                    title=title,
                    body=body,
                    search_text=entry_search_text(title, body),
                    author=author,
                    submission_key=key,
                )
                entry.full_clean()
                entry.save()
                _save_links(entry, resolved_links)
                for position, values in enumerate(rows):
                    image = Photo(entry=entry, **values, position=position)
                    image.full_clean()
                    image.save()
                receipt = DiarySubmission(
                    key=key, author=author, payload_sha256=fingerprint, entry=entry
                )
                receipt.full_clean()
                receipt.save()
            return CaptureResult(entry, created=True)


LINK_FIELDS = {
    GrowingArea: "area",
    Crop: "crop",
    Variety: "variety",
    Planting: "planting",
}
LINK_MODELS = {field: model for model, field in LINK_FIELDS.items()}
LINK_FORM_FIELDS = {
    "area": "link_areas",
    "crop": "link_crops",
    "variety": "link_varieties",
    "planting": "link_plantings",
}


def _outside_transaction(actor):
    if connection.in_atomic_block:
        raise RuntimeError("Call diary operations outside any existing transaction.")
    if not getattr(actor, "is_authenticated", False) or not getattr(actor, "is_active", False):
        raise PermissionDenied


def _positive_id(value, message=None):
    message = message or _("Choose a valid record.")
    if type(value) is not int or value <= 0:
        raise ValidationError(message)
    return value


def _version(value):
    if not isinstance(value, datetime) or timezone.is_naive(value):
        raise ValidationError(_("Reload the edit form before saving."))
    return value


def _entry_for_editor(actor, pk):
    try:
        entry = DiaryEntry.objects.select_related("author").get(pk=pk)
    except DiaryEntry.DoesNotExist as exc:
        raise EntryConflict(_("This observation no longer exists. Reload the diary.")) from exc
    if not can_edit_entry(actor, entry):
        raise PermissionDenied
    return entry


def _fresh_editor(actor, entry):
    current = User.objects.filter(pk=actor.pk).first()
    if current is None or not current.is_active or not can_edit_entry(current, entry):
        raise PermissionDenied
    return current


def _normalize_links(links):
    normalized = {}
    for target in links:
        if isinstance(target, tuple) and len(target) == 2:
            field, target_pk = target
            if not isinstance(field, str) or field not in LINK_MODELS:
                raise ValidationError(_("Choose a valid record to link."))
        else:
            field = next(
                (name for model, name in LINK_FIELDS.items() if isinstance(target, model)), None
            )
            target_pk = getattr(target, "pk", None)
        if field is None:
            raise ValidationError(_("Choose a valid record to link."))
        key = (field, _positive_id(target_pk))
        if key in normalized:
            raise ValidationError(_("A record can only be linked once."))
        normalized[key] = target
    return normalized


def _resolve_links(links, previous_links=()):
    resolved = {}
    for field, target_pk in links:
        model = LINK_MODELS[field]
        form_field = LINK_FORM_FIELDS[field]
        try:
            target = model.objects.get(pk=target_pk)
        except model.DoesNotExist as exc:
            raise ValidationError({form_field: _("Choose a valid record.")}) from exc
        if field != "planting" and target.is_archived and (field, target_pk) not in previous_links:
            raise ValidationError({form_field: _("Choose an active record for a new link.")})
        resolved[(field, target_pk)] = target
    return resolved


def _save_links(entry, links):
    for (field, _target_pk), target in links.items():
        link = DiaryLink(entry=entry, **{field: target})
        link.full_clean()
        link.save()


def _normalize_photo_ids(photo_ids):
    normalized = set()
    for value in photo_ids:
        if type(value) is not int or value <= 0:
            raise ValidationError(_("A selected photo is no longer available. Reload the entry."))
        normalized.add(value)
    return normalized


def _link_key(link):
    for field in ("area", "crop", "variety", "planting"):
        value = getattr(link, f"{field}_id")
        if value is not None:
            return field, value
    return None


def _edit_fingerprint(
    pk, date, body, links, remove_photo_ids, expected_updated_at, photos, *, title=""
):
    payload = {
        "version": 1,
        "pk": pk,
        "date": date.isoformat(),
        "body": body,
        "links": sorted(links),
        "remove_photo_ids": sorted(remove_photo_ids),
        "expected_updated_at": expected_updated_at.isoformat(),
        "photos": [photo.sha256 for photo in photos],
    }
    if title:
        payload["title"] = title
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _check_edit_receipt(key, actor, fingerprint):
    receipt = DiaryEditSubmission.objects.select_related("entry__author").filter(key=key).first()
    if receipt is None:
        return None
    if receipt.author_id != actor.pk or receipt.payload_sha256 != fingerprint:
        raise EntryConflict(
            _("This save key was already used with different content. Nothing was overwritten.")
        )
    if receipt.entry_id is None:
        raise EntryConflict(_("This entry was already deleted. It was not recreated."))
    _fresh_editor(actor, receipt.entry)
    return receipt.entry


def _write_entry_edit(
    *,
    actor,
    pk,
    date,
    title,
    body,
    links,
    remove_photo_ids,
    rows,
    expected_updated_at,
    folders,
    key,
    fingerprint,
):
    with transaction.atomic(), validated_write():
        entry = _entry_for_editor(actor, pk)
        _fresh_editor(actor, entry)
        retry = _check_edit_receipt(key, actor, fingerprint)
        if retry is not None:
            return retry
        if entry.updated_at != expected_updated_at:
            raise EntryConflict(
                _(
                    "This observation changed while the form was open. "
                    "Reload it before saving again."
                )
            )
        previous_links = {_link_key(link) for link in entry.links.all()}
        resolved_links = _resolve_links(links, previous_links)

        photos = list(entry.photos.all())
        photo_ids = {photo.pk for photo in photos}
        if not remove_photo_ids <= photo_ids:
            raise ValidationError(
                _("A selected photo no longer belongs to this entry. Reload it before saving.")
            )
        remaining = [photo for photo in photos if photo.pk not in remove_photo_ids]
        if not body and not remaining and not rows:
            raise ValidationError(_("Write a note or keep at least one photo."))
        if len(remaining) + len(rows) > settings.PHOTO_MAX_COUNT:
            raise ValidationError(
                _("An entry can have no more than %(count)s photos."),
                params={"count": settings.PHOTO_MAX_COUNT},
            )

        entry.links.all().delete()
        _save_links(entry, resolved_links)

        removed = [photo for photo in photos if photo.pk in remove_photo_ids]
        folders.extend(photo_bundles(removed))
        for photo in removed:
            photo.delete()

        entry.date = date
        entry.title = title
        entry.body = body
        entry.search_text = entry_search_text(title, body)
        entry.full_clean()
        entry.save()

        position = max((photo.position for photo in remaining), default=-1) + 1
        for offset, values in enumerate(rows):
            photo = Photo(entry=entry, **values, position=position + offset)
            photo.full_clean()
            photo.save()
        receipt = DiaryEditSubmission(
            key=key, author=actor, payload_sha256=fingerprint, entry=entry
        )
        receipt.full_clean()
        receipt.save()
        return entry


def update_entry(
    *,
    actor,
    pk,
    date,
    body,
    title="",
    links=(),
    uploads=(),
    remove_photo_ids=(),
    expected_updated_at,
    submission_key=None,
):
    """Edit atomically; an identical key/payload/version retry returns the current entry."""
    _outside_transaction(actor)
    pk = _positive_id(pk)
    if not isinstance(date, Date):
        raise ValidationError(_("Choose a valid entry date."))
    if not isinstance(body, str):
        raise ValidationError(_("Enter text, or leave the note blank."))
    title = _normalize_title(title)
    body = body.strip()
    expected_updated_at = _version(expected_updated_at)
    try:
        key = UUID(str(submission_key)) if submission_key is not None else uuid4()
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError(_("The save key is invalid. Reload the form.")) from exc
    links = _normalize_links(links)
    remove_photo_ids = _normalize_photo_ids(remove_photo_ids)
    uploads = list(uploads)
    if len(uploads) > settings.PHOTO_MAX_COUNT:
        raise ValidationError(
            _("Select no more than %(count)s photos."), params={"count": settings.PHOTO_MAX_COUNT}
        )
    _entry_for_editor(actor, pk)

    folders = []
    with media_operation():
        try:
            if uploads:
                with staged_uploads(uploads) as staged:
                    fingerprint = _edit_fingerprint(
                        pk,
                        date,
                        body,
                        links,
                        remove_photo_ids,
                        expected_updated_at,
                        staged,
                        title=title,
                    )
                    with transaction.atomic():
                        entry = _entry_for_editor(actor, pk)
                        _fresh_editor(actor, entry)
                        retry = _check_edit_receipt(key, actor, fingerprint)
                        if retry is not None:
                            return retry
                    with published_uploads(staged) as rows:
                        entry = _write_entry_edit(
                            actor=actor,
                            pk=pk,
                            date=date,
                            title=title,
                            body=body,
                            links=links,
                            remove_photo_ids=remove_photo_ids,
                            rows=rows,
                            expected_updated_at=expected_updated_at,
                            folders=folders,
                            key=key,
                            fingerprint=fingerprint,
                        )
            else:
                fingerprint = _edit_fingerprint(
                    pk, date, body, links, remove_photo_ids, expected_updated_at, (), title=title
                )
                entry = _write_entry_edit(
                    actor=actor,
                    pk=pk,
                    date=date,
                    title=title,
                    body=body,
                    links=links,
                    remove_photo_ids=remove_photo_ids,
                    rows=[],
                    expected_updated_at=expected_updated_at,
                    folders=folders,
                    key=key,
                    fingerprint=fingerprint,
                )
        finally:
            discard_unreferenced_bundles(folders)
    return entry


def delete_entry(*, actor, pk, expected_updated_at):
    _outside_transaction(actor)
    pk = _positive_id(pk)
    expected_updated_at = _version(expected_updated_at)
    _entry_for_editor(actor, pk)
    folders = []
    with media_operation():
        try:
            with transaction.atomic(), validated_write():
                entry = _entry_for_editor(actor, pk)
                _fresh_editor(actor, entry)
                if entry.updated_at != expected_updated_at:
                    raise EntryConflict(
                        _("This observation changed while the form was open. Reload it first.")
                    )
                folders.extend(photo_bundles(entry.photos.all()))
                entry.delete()
        finally:
            discard_unreferenced_bundles(folders)


def _entry_photo(pk):
    photo = Photo.objects.select_related("entry__author").filter(entry__isnull=False, pk=pk).first()
    if photo is None:
        raise PermissionDenied
    return photo


def update_entry_photo(*, actor, pk, caption, position, expected_updated_at):
    _outside_transaction(actor)
    pk = _positive_id(pk)
    expected_updated_at = _version(expected_updated_at)
    if not isinstance(caption, str):
        raise ValidationError(_("Enter a caption, or leave it blank."))
    if type(position) is not int or position < 0:
        raise ValidationError(_("Enter a nonnegative whole number for the order."))
    photo = _entry_photo(pk)
    if not can_edit_entry(actor, photo.entry):
        raise PermissionDenied
    with transaction.atomic(), validated_write():
        entry = _entry_for_editor(actor, photo.entry_id)
        _fresh_editor(actor, entry)
        if entry.updated_at != expected_updated_at:
            raise EntryConflict(
                _(
                    "This observation changed while the form was open. "
                    "Reload it before saving again."
                )
            )
        photo = Photo.objects.get(pk=pk, entry=entry)
        photo.caption = caption
        photo.position = position
        photo.full_clean()
        photo.save(update_fields=["caption", "position"])
        entry.save(update_fields=["updated_at"])
    return photo


def delete_entry_photo(*, actor, pk, expected_updated_at):
    _outside_transaction(actor)
    pk = _positive_id(pk)
    expected_updated_at = _version(expected_updated_at)
    photo = _entry_photo(pk)
    if not can_edit_entry(actor, photo.entry):
        raise PermissionDenied
    folders = []
    with media_operation():
        try:
            with transaction.atomic(), validated_write():
                entry = _entry_for_editor(actor, photo.entry_id)
                _fresh_editor(actor, entry)
                if entry.updated_at != expected_updated_at:
                    raise EntryConflict(
                        _("This observation changed while the form was open. Reload it first.")
                    )
                photo = Photo.objects.get(pk=pk, entry=entry)
                if not entry.body and entry.photos.count() == 1:
                    raise ValidationError(_("Keep at least one photo when the note is empty."))
                folders.extend(photo_bundles([photo]))
                photo.delete()
                entry.save(update_fields=["updated_at"])
        finally:
            discard_unreferenced_bundles(folders)
