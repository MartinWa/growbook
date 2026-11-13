"""Explicit planting writes, revalidating current associations after acquiring the writer."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
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

from ..models import Crop, GrowingArea, Planting, PlantingCycle, PlantingSubmission, Variety
from ._shared import (
    PlantingResult,
    _active_actor,
    _current,
    _identifier,
    _key,
    _outside_transaction,
    _planting_fingerprint,
    _planting_receipt,
    _version,
)
from .cycles import (
    normalize_category_ids,
    set_categories,
    validate_lifecycle,
    validate_member_years,
)


def _planting_values(
    crop, start_year, variety, area, lifespan, end_year, label, quantity, notes, evaluation
):
    values = {
        "crop_id": _identifier(crop, "crop", model=Crop),
        "variety_id": _identifier(variety, "variety", optional=True, model=Variety),
        "area_id": _identifier(area, "area", optional=True, model=GrowingArea),
        "start_year": start_year,
        "lifespan": lifespan,
        "end_year": end_year,
        "label": label,
        "quantity": quantity,
        "notes": notes,
        "evaluation": evaluation,
    }
    for field in ("start_year", "end_year"):
        value = values[field]
        if value is None and field == "end_year":
            continue
        if type(value) is not int or not 1 <= value <= 9999:
            raise ValidationError({field: _("Enter a growing year from 1 to 9999.")})
    if quantity is not None and (type(quantity) is not int or quantity < 0):
        raise ValidationError(
            {"quantity": _("Enter a nonnegative whole number, or leave it blank.")}
        )
    for field in ("lifespan", "label", "notes", "evaluation"):
        if not isinstance(values[field], str):
            raise ValidationError({field: _("Enter text, or leave this field blank.")})
        if field != "lifespan":
            values[field] = values[field].strip()
    return values


def _validate_planting(record, previous):
    """Current FK eligibility and taxonomy must be checked after acquiring the writer."""
    try:
        crop = Crop.objects.get(pk=record.crop_id)
    except Crop.DoesNotExist as exc:
        raise ValidationError({"crop": _("Choose a valid record.")}) from exc
    if crop.is_archived and record.crop_id != previous.get("crop_id"):
        raise ValidationError({"crop": _("Choose an active crop for a new association.")})
    if record.variety_id is not None:
        try:
            variety = Variety.objects.get(pk=record.variety_id)
        except Variety.DoesNotExist as exc:
            raise ValidationError({"variety": _("Choose a valid record.")}) from exc
        if variety.crop_id != record.crop_id:
            raise ValidationError(
                {"variety": _("Choose a variety of the selected crop, or leave it blank.")}
            )
        if record.variety_id != previous.get("variety_id") and (
            variety.is_archived or crop.is_archived
        ):
            raise ValidationError({"variety": _("Choose an active variety for a new association.")})
    if record.area_id is not None:
        try:
            area = GrowingArea.objects.get(pk=record.area_id)
        except GrowingArea.DoesNotExist as exc:
            raise ValidationError({"area": _("Choose a valid record.")}) from exc
        if area.is_archived and record.area_id != previous.get("area_id"):
            raise ValidationError({"area": _("Choose an active area for a new association.")})
        if record.area_id != previous.get("area_id") and area.children.exists():
            raise ValidationError(
                {"area": _("Choose an area without child areas for a new association.")}
            )
    peers = Planting.objects.filter(batch_key=record.batch_key).exclude(pk=record.pk)
    if peers.exclude(crop_id=record.crop_id).exists():
        raise ValidationError({"crop": _("All varieties in a batch must belong to the same crop.")})
    if previous.get("variety_id") is not None and record.variety_id is None and peers.exists():
        raise ValidationError({"variety": _("Keep a variety for a planting in a shared batch.")})
    if peers.filter(variety_id=record.variety_id).exists():
        raise ValidationError({"variety": _("This variety is already in the batch.")})
    validate_member_years(record, record.cycle)
    record.full_clean()


def _write_planting(actor, pk, values, key, fingerprint, rows, expected_updated_at):
    try:
        with transaction.atomic(), validated_write():
            _active_actor(actor)
            retry = _planting_receipt(key, actor, "planting", fingerprint)
            if retry is not None:
                return retry
            record = _current(Planting, pk, expected_updated_at) if pk is not None else Planting()
            previous = {
                field: getattr(record, field) for field in ("crop_id", "variety_id", "area_id")
            }
            batch_planting = values.get("batch_planting")
            if batch_planting is not None:
                member = _current(Planting, batch_planting)
                record.batch_key = member.batch_key
                record.cycle = member.cycle
            elif pk is None:
                record.cycle = PlantingCycle(
                    pk=record.batch_key,
                    status=values.get("status", PlantingCycle.Status.PLANNED),
                    started_on=values.get("started_on"),
                    completed_on=values.get("completed_on"),
                )
                validate_lifecycle(record.cycle, require_dates=True)
                record.cycle.save()
                set_categories(record.cycle, values.get("category_ids", []))
            for field, value in values.items():
                if field in (
                    "batch_planting",
                    "status",
                    "started_on",
                    "completed_on",
                    "category_ids",
                ):
                    continue
                setattr(record, field, value)
            if (
                pk is None
                and record.lifespan == Planting.Lifespan.PERSISTENT
                and record.cycle.completed_on
            ):
                record.end_year = record.cycle.completed_on.year
            _validate_planting(record, previous)
            if batch_planting is not None and record.variety_id is None:
                raise ValidationError({"variety": _("Choose a variety to add to this batch.")})
            if pk is None and not record.label:
                record.label = record.crop.name
                if record.variety_id is not None:
                    record.label += "-" + record.variety.name
                record.full_clean()
            photos = list(record.photos.all()) if pk is not None else []
            if len(photos) + len(rows) > settings.PHOTO_MAX_COUNT:
                raise ValidationError(
                    _("A record can have no more than %(count)s photos."),
                    params={"count": settings.PHOTO_MAX_COUNT},
                )
            record.save()
            record.cycle.save()
            position = max((photo.position for photo in photos), default=-1) + 1
            for offset, photo_values in enumerate(rows):
                photo = Photo(planting=record, **photo_values, position=position + offset)
                photo.full_clean()
                photo.save()
            receipt = PlantingSubmission(
                key=key, author=actor, kind="planting", payload_sha256=fingerprint, planting=record
            )
            receipt.full_clean()
            receipt.save()
            return PlantingResult(record)
    except IntegrityError as exc:
        # SQLite reports these partial unique constraints by their columns, not their names.
        if str(exc) in (
            "UNIQUE constraint failed: garden_planting.batch_key, garden_planting.variety_id",
            "UNIQUE constraint failed: garden_planting.batch_key",
        ):
            raise ValidationError({"variety": _("This variety is already in the batch.")}) from exc
        raise


def _save_planting(actor, pk, values, uploads, submission_key, expected_updated_at):
    _outside_transaction(actor)
    if pk is not None:
        _version(expected_updated_at)
        pk = _identifier(pk, "id")
    key = _key(submission_key)
    uploads = list(uploads)
    if not uploads:
        fingerprint = _planting_fingerprint("planting", pk, expected_updated_at, values)
        return _write_planting(actor, pk, values, key, fingerprint, [], expected_updated_at)
    with media_operation(), staged_uploads(uploads) as photos:
        fingerprint = _planting_fingerprint("planting", pk, expected_updated_at, values, photos)
        with transaction.atomic():
            _active_actor(actor)
            retry = _planting_receipt(key, actor, "planting", fingerprint)
            if retry is not None:
                return retry
        with published_uploads(photos) as rows:
            return _write_planting(actor, pk, values, key, fingerprint, rows, expected_updated_at)


def create_planting(
    *,
    actor,
    crop,
    start_year,
    variety=None,
    area=None,
    lifespan=Planting.Lifespan.ANNUAL,
    end_year=None,
    label="",
    quantity=None,
    notes="",
    evaluation="",
    uploads=(),
    submission_key=None,
    batch_planting=None,
    status=None,
    started_on=None,
    completed_on=None,
    category_ids=None,
):
    values = _planting_values(
        crop, start_year, variety, area, lifespan, end_year, label, quantity, notes, evaluation
    )
    if batch_planting is not None:
        values["batch_planting"] = _identifier(batch_planting, "batch_planting", model=Planting)
    elif (
        status is not None
        or started_on is not None
        or completed_on is not None
        or category_ids is not None
    ):
        values.update(
            status=status or PlantingCycle.Status.PLANNED,
            started_on=started_on,
            completed_on=completed_on,
            category_ids=normalize_category_ids(category_ids or ()),
        )
    return _save_planting(actor, None, values, uploads, submission_key, None)


def edit_planting(
    *,
    actor,
    pk,
    crop,
    start_year,
    variety=None,
    area=None,
    lifespan=Planting.Lifespan.ANNUAL,
    end_year=None,
    label="",
    quantity=None,
    notes="",
    evaluation="",
    uploads=(),
    submission_key=None,
    expected_updated_at,
):
    values = _planting_values(
        crop, start_year, variety, area, lifespan, end_year, label, quantity, notes, evaluation
    )
    return _save_planting(
        actor, _identifier(pk, "id"), values, uploads, submission_key, expected_updated_at
    )


def delete_planting(*, actor, pk, expected_updated_at):
    _outside_transaction(actor)
    _version(expected_updated_at)
    with media_operation():
        folders = []
        try:
            with transaction.atomic(), validated_write():
                _active_actor(actor)
                record = _current(Planting, _identifier(pk, "id"), expected_updated_at)
                folders.extend(photo_bundles(record.photos.all()))
                try:
                    cycle = record.cycle
                    record.delete()
                    if not cycle.members.exists():
                        cycle.delete()
                    else:
                        cycle.save()
                except ProtectedError as exc:
                    raise ValidationError(
                        _("This planting is still referenced and cannot be deleted.")
                    ) from exc
        finally:
            discard_unreferenced_bundles(folders)
