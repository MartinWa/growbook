"""Shared garden identity, version and immutable receipt checks; no domain writes."""

from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.utils.translation import gettext as _

from core import receipts
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


def _fingerprint(kind, pk, expected_updated_at, values, photos):
    """This app's receipt payload; the digest itself comes from ``core.receipts``."""
    return receipts.fingerprint(
        {
            "version": 1,
            "kind": kind,
            "pk": pk,
            "expected_updated_at": expected_updated_at.isoformat() if expected_updated_at else None,
            "values": values,
            "photos": [photo.sha256 for photo in photos],
        }
    )


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


def _accept_receipt(receipt, actor, kind, fingerprint):
    """Accept immutable content before callers validate the target's current state."""
    return receipts.accepted_target(
        receipt,
        actor.pk,
        digest=fingerprint,
        kind=kind,
        conflict=RegisterConflict(
            _("This save key was already used with different content. Nothing was overwritten.")
        ),
        deleted=RegisterConflict(_("This record was deleted. It was not recreated.")),
    )


def _planting_receipt(key, actor, kind, fingerprint):
    receipt = (
        PlantingSubmission.objects.select_related("planting__crop", "event__planting__crop")
        .filter(key=key)
        .first()
    )
    record = _accept_receipt(receipt, actor, kind, fingerprint)
    return PlantingResult(record, replayed=True) if record is not None else None


def _planting_fingerprint(kind, pk, expected_updated_at, values, photos=()):
    return _fingerprint(kind, pk, expected_updated_at, receipts.canonical(values), photos)
