"""Explicit crop, variety and growing-area writes, with serialized validation."""

from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.utils.translation import gettext as _

from core.media import media_operation
from core.models import Photo
from core.photo_storage import (
    discard_unreferenced_bundles,
    photo_bundles,
    published_uploads,
    staged_uploads,
)
from core.writes import validated_write

from ..models import Crop, GrowingArea, RegisterSubmission, Variety
from ..names import normalize_name
from ._shared import (
    RegisterConflict,
    _active_actor,
    _current,
    _fingerprint,
    _identifier,
    _key,
    _outside_transaction,
    _version,
)


@dataclass(frozen=True)
class RegisterResult:
    record: Crop | Variety | GrowingArea
    replayed: bool = False


def _receipt(key, actor, kind, fingerprint):
    receipt = RegisterSubmission.objects.select_related(kind).filter(key=key).first()
    if receipt is None:
        return None
    if (
        receipt.author_id != actor.pk
        or receipt.kind != kind
        or receipt.payload_sha256 != fingerprint
    ):
        raise RegisterConflict(
            _("This save key was already used with different content. Nothing was overwritten.")
        )
    record = getattr(receipt, kind)
    if record is None:
        raise RegisterConflict(_("This record was deleted. It was not recreated."))
    return RegisterResult(record, replayed=True)


def _check_area_parent(area):
    parent_id = area.parent_id
    seen = set()
    while parent_id is not None:
        if parent_id == area.pk or parent_id in seen:
            raise ValidationError({"parent": _("Choose a parent that does not create a cycle.")})
        seen.add(parent_id)
        try:
            parent_id = GrowingArea.objects.values_list("parent_id", flat=True).get(pk=parent_id)
        except GrowingArea.DoesNotExist as exc:
            raise ValidationError({"parent": _("Choose a valid record.")}) from exc


def _validate(record, previous_parent_id=None):
    # Model.clean derives keys; prepare them before field/constraint validation too.
    record.name, record.name_key = normalize_name(record.name)
    if isinstance(record, Variety):
        try:
            crop = Crop.objects.get(pk=record.crop_id)
        except Crop.DoesNotExist as exc:
            raise ValidationError({"crop": _("Choose a valid record.")}) from exc
        if record.pk and record.crop_id != previous_parent_id and record.plantings.exists():
            raise ValidationError(
                {"crop": _("This variety has plantings. Reconcile them before changing its crop.")}
            )
        if crop.is_archived and record.crop_id != previous_parent_id:
            raise ValidationError({"crop": _("Choose an active crop for a new association.")})
    elif isinstance(record, GrowingArea):
        if record.parent_id is not None:
            try:
                parent = GrowingArea.objects.get(pk=record.parent_id)
            except GrowingArea.DoesNotExist as exc:
                raise ValidationError({"parent": _("Choose a valid record.")}) from exc
            if parent.is_archived and record.parent_id != previous_parent_id:
                raise ValidationError(
                    {"parent": _("Choose an active parent for a new association.")}
                )
        _check_area_parent(record)
        for field in ("length_cm", "width_cm"):
            value = getattr(record, field)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValidationError(
                    {field: _("Enter a positive whole number, or leave it blank.")}
                )
    duplicate = type(record).objects.filter(name_key=record.name_key).exclude(pk=record.pk)
    if isinstance(record, Variety):
        duplicate = duplicate.filter(crop_id=record.crop_id)
    elif isinstance(record, GrowingArea):
        duplicate = duplicate.filter(parent_id=record.parent_id)
    if duplicate.exists():
        raise ValidationError({"name": _("This name is already used in this register scope.")})
    record.full_clean()


def _write_record(actor, model, kind, pk, values, key, fingerprint, rows, expected_updated_at):
    with transaction.atomic(), validated_write():
        _active_actor(actor)
        retry = _receipt(key, actor, kind, fingerprint)
        if retry is not None:
            return retry
        record = _current(model, pk, expected_updated_at) if pk is not None else model()
        previous_parent_id = (
            record.crop_id if kind == "variety" else record.parent_id if kind == "area" else None
        )
        for field, value in values.items():
            setattr(record, field, value)
        _validate(record, previous_parent_id)
        photos = list(record.photos.all()) if pk is not None and kind != "crop" else []
        if len(photos) + len(rows) > settings.PHOTO_MAX_COUNT:
            raise ValidationError(
                _("A record can have no more than %(count)s photos."),
                params={"count": settings.PHOTO_MAX_COUNT},
            )
        record.save()
        position = max((photo.position for photo in photos), default=-1) + 1
        for offset, values in enumerate(rows):
            photo = Photo(**{kind: record}, **values, position=position + offset)
            photo.full_clean()
            photo.save()
        receipt = RegisterSubmission(
            key=key, author=actor, kind=kind, payload_sha256=fingerprint, **{kind: record}
        )
        receipt.full_clean()
        receipt.save()
        return RegisterResult(record)


def _save(actor, model, kind, pk, values, uploads, submission_key, expected_updated_at):
    _outside_transaction(actor)
    if pk is not None:
        _version(expected_updated_at)
    values = dict(values)
    values["name"] = normalize_name(values["name"])[0]
    for field, value in values.items():
        if field not in {"parent_id", "crop_id", "length_cm", "width_cm"} and not isinstance(
            value, str
        ):
            raise ValidationError({field: _("Enter text, or leave this field blank.")})
    for field in ("length_cm", "width_cm"):
        value = values.get(field)
        if value is not None and (type(value) is not int or value <= 0):
            raise ValidationError({field: _("Enter a positive whole number, or leave it blank.")})
    if pk is not None:
        pk = _identifier(pk, "id")
    key = _key(submission_key)
    uploads = list(uploads)
    if not uploads:
        fingerprint = _fingerprint(kind, pk, expected_updated_at, values, [])
        return _write_record(
            actor, model, kind, pk, values, key, fingerprint, [], expected_updated_at
        )
    with media_operation(), staged_uploads(uploads) as photos:
        fingerprint = _fingerprint(kind, pk, expected_updated_at, values, photos)
        with transaction.atomic():
            _active_actor(actor)
            retry = _receipt(key, actor, kind, fingerprint)
            if retry is not None:
                return retry
        with published_uploads(photos) as rows:
            return _write_record(
                actor, model, kind, pk, values, key, fingerprint, rows, expected_updated_at
            )


def create_crop(*, actor, name, notes="", submission_key=None):
    return _save(
        actor, Crop, "crop", None, {"name": name, "notes": notes}, [], submission_key, None
    )


def edit_crop(*, actor, pk, name, notes="", submission_key=None, expected_updated_at):
    return _save(
        actor,
        Crop,
        "crop",
        _identifier(pk, "id"),
        {"name": name, "notes": notes},
        [],
        submission_key,
        expected_updated_at,
    )


def create_variety(
    *,
    actor,
    crop,
    name,
    category="",
    description="",
    source="",
    experiences="",
    uploads=(),
    submission_key=None,
):
    values = {
        "crop_id": _identifier(crop, "crop", model=Crop),
        "name": name,
        "category": category,
        "description": description,
        "source": source,
        "experiences": experiences,
    }
    return _save(actor, Variety, "variety", None, values, uploads, submission_key, None)


def edit_variety(
    *,
    actor,
    pk,
    crop,
    name,
    category="",
    description="",
    source="",
    experiences="",
    uploads=(),
    submission_key=None,
    expected_updated_at,
):
    values = {
        "crop_id": _identifier(crop, "crop", model=Crop),
        "name": name,
        "category": category,
        "description": description,
        "source": source,
        "experiences": experiences,
    }
    return _save(
        actor,
        Variety,
        "variety",
        _identifier(pk, "id"),
        values,
        uploads,
        submission_key,
        expected_updated_at,
    )


def create_area(
    *,
    actor,
    name,
    parent=None,
    area_type="",
    length_cm=None,
    width_cm=None,
    description="",
    notes="",
    uploads=(),
    submission_key=None,
):
    values = {
        "name": name,
        "parent_id": _identifier(parent, "parent", optional=True, model=GrowingArea),
        "area_type": area_type,
        "length_cm": length_cm,
        "width_cm": width_cm,
        "description": description,
        "notes": notes,
    }
    return _save(actor, GrowingArea, "area", None, values, uploads, submission_key, None)


def edit_area(
    *,
    actor,
    pk,
    name,
    parent=None,
    area_type="",
    length_cm=None,
    width_cm=None,
    description="",
    notes="",
    uploads=(),
    submission_key=None,
    expected_updated_at,
):
    values = {
        "name": name,
        "parent_id": _identifier(parent, "parent", optional=True, model=GrowingArea),
        "area_type": area_type,
        "length_cm": length_cm,
        "width_cm": width_cm,
        "description": description,
        "notes": notes,
    }
    return _save(
        actor,
        GrowingArea,
        "area",
        _identifier(pk, "id"),
        values,
        uploads,
        submission_key,
        expected_updated_at,
    )


def _archive(actor, model, pk, archived):
    _outside_transaction(actor)
    if type(archived) is not bool:
        raise ValidationError(_("Choose a valid archive state."))
    with transaction.atomic(), validated_write():
        _active_actor(actor)
        record = _current(model, _identifier(pk, "id"))
        record.is_archived = archived
        _validate(record, getattr(record, "crop_id", getattr(record, "parent_id", None)))
        record.save(update_fields=["is_archived", "updated_at"])
        return record


def archive_crop(*, actor, pk, archived=True):
    return _archive(actor, Crop, pk, archived)


def archive_variety(*, actor, pk, archived=True):
    return _archive(actor, Variety, pk, archived)


def archive_area(*, actor, pk, archived=True):
    return _archive(actor, GrowingArea, pk, archived)


def _delete_rows(actor, model, pk, expected_updated_at, folders):
    with transaction.atomic(), validated_write():
        _active_actor(actor)
        record = _current(model, _identifier(pk, "id"), expected_updated_at)
        if isinstance(record, Crop) and record.varieties.exists():
            raise ValidationError(_("This crop has varieties. Archive it instead."))
        if isinstance(record, GrowingArea) and record.children.exists():
            raise ValidationError(
                _("This area has children. Reorganize them or archive it instead.")
            )
        if not isinstance(record, Crop):
            folders.extend(photo_bundles(record.photos.all()))
        try:
            record.delete()
        except ProtectedError as exc:
            raise ValidationError(
                _("This record is still referenced. Archive it instead.")
            ) from exc


def _delete(actor, model, pk, expected_updated_at):
    _outside_transaction(actor)
    _version(expected_updated_at)
    if model is Crop:
        return _delete_rows(actor, model, pk, expected_updated_at, [])
    with media_operation():
        folders = []
        try:
            _delete_rows(actor, model, pk, expected_updated_at, folders)
        finally:
            discard_unreferenced_bundles(folders)


def delete_crop(*, actor, pk, expected_updated_at):
    _delete(actor, Crop, pk, expected_updated_at)


def delete_variety(*, actor, pk, expected_updated_at):
    _delete(actor, Variety, pk, expected_updated_at)


def delete_area(*, actor, pk, expected_updated_at):
    _delete(actor, GrowingArea, pk, expected_updated_at)
