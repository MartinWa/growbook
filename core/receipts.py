"""Shared immutable-receipt primitives for validated writes.

Only the parts every app repeats live here: save-key parsing, version-token
validation, canonical hashing and the author/hash/kind/tombstone acceptance
check. Receipt models, their payload shape and their replay glue stay in each
app. Stored digests and raised messages are part of the retry contract: changing
either silently breaks a save that is retried after a failed response.
"""

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

VERSION_MESSAGE = _("Reload the edit form before saving.")
KEY_MESSAGE = _("The save key is invalid. Reload the form.")


def submission_key(value, *, message=KEY_MESSAGE):
    """The submitted save key, or a fresh one when the caller has none."""
    try:
        return UUID(str(value)) if value is not None else uuid4()
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError(message) from exc


def version_token(value):
    """The ``updated_at`` an edit form was rendered from, never a naive datetime."""
    if not isinstance(value, datetime) or timezone.is_naive(value):
        raise ValidationError(VERSION_MESSAGE)
    return value


def canonical(value):
    """Values as they hash: decimals normalized, identifiers and dates as text.

    A datetime keeps the space-separated ``str()`` form the economy receipts were
    stored with; a date is always its ISO form.
    """
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    if isinstance(value, Decimal):
        return str(value.normalize())
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def fingerprint(payload, *, sort_keys=True):
    """The canonical SHA-256 hex digest stored on a receipt.

    ``sort_keys=False`` is reserved for payloads whose insertion order is part of
    an already stored digest.
    """
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=sort_keys,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def accepted_target(receipt, actor_id, *, digest, conflict, deleted, kind=None, target=None):
    """The target a replay returns, or None when this key was never used.

    ``kind`` names the receipt's kind column and ``target`` the foreign key to
    read; both are optional because a receipt may carry neither. ``conflict`` and
    ``deleted`` are the caller's own exceptions for a key reused with different
    content and for a retained tombstone, so each app keeps its own vocabulary.
    """
    if receipt is None:
        return None
    if receipt.author_id != actor_id or receipt.payload_sha256 != digest:
        raise conflict
    if kind is not None and receipt.kind != kind:
        raise conflict
    record = getattr(receipt, target or kind)
    if record is None:
        raise deleted
    return record
