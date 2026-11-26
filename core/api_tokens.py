"""Issue, revoke and delete named API credentials.

Only a hash and a lookup prefix of the secret are stored, so a lost secret is replaced
rather than recovered, and an abandoned credential is revoked before it can be deleted.
"""

import hashlib
import secrets

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import PREFIX_LENGTH, ApiToken
from .writes import validated_write

NAME_REQUIRED = _("Give the token a name.")
NAME_TOO_LONG = _("Use at most 255 characters for the token name.")
NAME_TAKEN = _("A token with this name already exists.")
EXPIRY_PAST = _("Choose an expiry in the future.")
EXPIRY_NAIVE = _("Choose an expiry with a time zone.")
ACCOUNT_REQUIRED = _("Sign in with an active account to manage API tokens.")
OWNER_REQUIRED = _("That token belongs to another account.")
ALREADY_REVOKED = _("That token is already revoked.")
STILL_ACTIVE = _("Revoke the token first; a working token cannot be deleted.")
GONE = _("That token no longer exists.")
SECRET_BYTES = 32


def secret_hash(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def _actor_id(actor):
    if not getattr(actor, "is_authenticated", False) or not getattr(actor, "is_active", False):
        raise ValidationError(ACCOUNT_REQUIRED)
    return actor.pk


def _owned(actor, token):
    user_id = _actor_id(actor)
    if token.user_id != user_id:
        raise ValidationError(OWNER_REQUIRED)
    return user_id


def clean_name(value, user_id):
    name = (value or "").strip()
    if not name:
        raise ValidationError({"name": NAME_REQUIRED})
    if len(name) > ApiToken._meta.get_field("name").max_length:
        raise ValidationError({"name": NAME_TOO_LONG})
    if ApiToken.objects.filter(user_id=user_id, name=name).exists():
        raise ValidationError({"name": NAME_TAKEN})
    return name


def clean_expiry(value):
    """An expiry is optional, but a chosen one must be a future, timezone-aware time."""
    if value is None:
        return None
    if timezone.is_naive(value):
        raise ValidationError({"expires_at": EXPIRY_NAIVE})
    if value <= timezone.now():
        raise ValidationError({"expires_at": EXPIRY_PAST})
    return value


def issue_token(*, actor, name, expires_at=None):
    """Return a new named credential and its secret, which is shown once and not stored."""
    user_id = _actor_id(actor)
    clean = clean_name(name, user_id)
    expires_at = clean_expiry(expires_at)
    secret = secrets.token_urlsafe(SECRET_BYTES)
    with validated_write(), transaction.atomic():
        try:
            record = ApiToken.objects.create(
                user_id=user_id,
                name=clean,
                key_hash=secret_hash(secret),
                prefix=secret[:PREFIX_LENGTH],
                expires_at=expires_at,
            )
        except IntegrityError as exc:
            raise ValidationError({"name": NAME_TAKEN}) from exc
    return record, secret


def _refusal(pk, user_id):
    """Why a conditional write matched no row: gone, already done, or still active."""
    current = ApiToken.objects.filter(pk=pk, user_id=user_id).first()
    if current is None:
        return GONE
    return ALREADY_REVOKED if current.revoked_at is not None else STILL_ACTIVE


def revoke_token(*, actor, token):
    """Stop one credential permanently; clients using it fail on their next request."""
    user_id = _owned(actor, token)
    revoked_at = timezone.now()
    with validated_write(), transaction.atomic():
        # The condition decides, so a concurrent revoke or delete cannot turn into a
        # failed save on a row that another request already changed.
        revoked = ApiToken.objects.filter(
            pk=token.pk, user_id=user_id, revoked_at__isnull=True
        ).update(revoked_at=revoked_at)
        if not revoked:
            raise ValidationError(_refusal(token.pk, user_id))
    token.revoked_at = revoked_at
    return token


def delete_token(*, actor, token):
    """Remove a revoked or expired credential so its name can be used again."""
    user_id = _owned(actor, token)
    now = timezone.now()
    with validated_write(), transaction.atomic():
        removed, _ = (
            ApiToken.objects.filter(pk=token.pk, user_id=user_id)
            .filter(Q(revoked_at__isnull=False) | Q(expires_at__lte=now))
            .delete()
        )
        if not removed:
            raise ValidationError(_refusal(token.pk, user_id))
    return token


def tokens_for(user):
    """The account's credentials, newest first."""
    return ApiToken.objects.filter(user=user)
