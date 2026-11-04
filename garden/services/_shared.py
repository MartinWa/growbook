"""Shared garden identity, version and immutable receipt checks; no domain writes."""

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID, uuid4

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import User

from ..models import Planting, PlantingEvent, PlantingSubmission


class RegisterConflict(ValidationError):
    pass


def _outside_transaction(actor):
    if connection.in_atomic_block:
        raise RuntimeError("Call register operations outside any existing transaction.")
    if not actor.is_authenticated or not actor.is_active or not actor.pk:
        raise PermissionDenied


def _active_actor(actor):
    if not User.objects.filter(pk=actor.pk, is_active=True).exists():
        raise PermissionDenied


def _identifier(value, field, *, optional=False, model=None):
    if value is None and optional:
        return None
    if model is not None and not isinstance(value, (int, model)):
        raise ValidationError({field: _("Choose a valid record.")})
    value = getattr(value, "pk", value)
    if type(value) is not int or value <= 0:
        raise ValidationError({field: _("Choose a valid record.")})
    return value


def _key(value):
    try:
        return UUID(str(value)) if value is not None else uuid4()
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError(_("The save key is invalid. Reload the form.")) from exc


def _fingerprint(kind, pk, expected_updated_at, values, photos):
    payload = {
        "version": 1,
        "kind": kind,
        "pk": pk,
        "expected_updated_at": expected_updated_at.isoformat() if expected_updated_at else None,
        "values": values,
        "photos": [photo.sha256 for photo in photos],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _current(model, pk, expected_updated_at=None):
    try:
        record = model.objects.get(pk=pk)
    except model.DoesNotExist as exc:
        raise RegisterConflict(_("This record no longer exists. Reload the register.")) from exc
    if expected_updated_at is not None and record.updated_at != expected_updated_at:
        raise RegisterConflict(
            _("This record changed while the form was open. Reload it before saving again.")
        )
    return record


@dataclass(frozen=True)
class PlantingResult:
    record: Planting | PlantingEvent
    replayed: bool = False


def _planting_receipt(key, actor, kind, fingerprint):
    receipt = (
        PlantingSubmission.objects.select_related("planting__crop", "event__planting__crop")
        .filter(key=key)
        .first()
    )
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
    return PlantingResult(record, replayed=True)


def _planting_fingerprint(kind, pk, expected_updated_at, values, photos=()):
    canonical = {
        field: value.isoformat() if isinstance(value, date) else value
        for field, value in values.items()
    }
    return _fingerprint(kind, pk, expected_updated_at, canonical, photos)


def _version(value):
    if not isinstance(value, datetime) or timezone.is_naive(value):
        raise ValidationError(_("Reload the edit form before saving."))
