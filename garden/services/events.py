"""Validated planting-event writes and immutable save receipts."""

from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from core.writes import validated_write

from ..models import Planting, PlantingEvent, PlantingSubmission
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


def write_event(values, pk=None, expected_updated_at=None):
    """Validate and save within the caller's serialized transaction; no receipt."""
    owner = _current(Planting, values["planting_id"])
    record = _current(PlantingEvent, pk, expected_updated_at) if pk is not None else PlantingEvent()
    if pk is not None and record.planting_id != values["planting_id"]:
        raise ValidationError({"planting": _("Choose a valid record.")})
    for field, value in values.items():
        setattr(record, field, value)
    record.full_clean()
    record.save()
    owner.save(update_fields=["updated_at"])
    return record


def _save_event(
    actor, planting, pk, kind, planned_date, actual_date, note, submission_key, expected_updated_at
):
    _outside_transaction(actor)
    planting_id = _identifier(planting, "planting", model=Planting)
    if pk is not None:
        _version(expected_updated_at)
        pk = _identifier(pk, "id")
    for field, value in (("planned_date", planned_date), ("actual_date", actual_date)):
        if value is not None and type(value) is not date:
            raise ValidationError({field: _("Choose a valid event date.")})
    if not isinstance(kind, str) or not isinstance(note, str):
        raise ValidationError(_("Enter text, or leave this field blank."))
    values = {
        "planting_id": planting_id,
        "kind": kind,
        "planned_date": planned_date,
        "actual_date": actual_date,
        "note": note.strip(),
    }
    key = _key(submission_key)
    fingerprint = _planting_fingerprint("event", pk, expected_updated_at, values)
    with transaction.atomic(), validated_write():
        _active_actor(actor)
        retry = _planting_receipt(key, actor, "event", fingerprint)
        if retry is not None:
            return retry
        record = write_event(values, pk, expected_updated_at)
        receipt = PlantingSubmission(
            key=key, author=actor, kind="event", payload_sha256=fingerprint, event=record
        )
        receipt.full_clean()
        receipt.save()
        return PlantingResult(record)


def create_planting_event(
    *, actor, planting, kind, planned_date=None, actual_date=None, note="", submission_key=None
):
    return _save_event(
        actor, planting, None, kind, planned_date, actual_date, note, submission_key, None
    )


def edit_planting_event(
    *,
    actor,
    pk,
    planting,
    kind,
    planned_date=None,
    actual_date=None,
    note="",
    submission_key=None,
    expected_updated_at,
):
    return _save_event(
        actor,
        planting,
        _identifier(pk, "id"),
        kind,
        planned_date,
        actual_date,
        note,
        submission_key,
        expected_updated_at,
    )


def delete_planting_event(*, actor, pk, expected_updated_at):
    _outside_transaction(actor)
    _version(expected_updated_at)
    with transaction.atomic(), validated_write():
        _active_actor(actor)
        event = _current(PlantingEvent, _identifier(pk, "id"), expected_updated_at)
        owner = _current(Planting, event.planting_id)
        event.delete()
        owner.save(update_fields=["updated_at"])
