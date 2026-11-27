"""Named API credentials: stored hashes, the management page and its guard rails."""

import hashlib
import re
from datetime import datetime, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext

from api.auth import resolve_token
from core.api_tokens import (
    ALREADY_REVOKED,
    GONE,
    NAME_TAKEN,
    OWNER_REQUIRED,
    STILL_ACTIVE,
    delete_token,
    issue_token,
    revoke_token,
    tokens_for,
)
from core.models import ApiToken
from core.writes import validated_write

pytestmark = pytest.mark.django_db


def create_token(actor, name="garden-laptop", expires_at=None):
    return issue_token(actor=actor, name=name, expires_at=expires_at)


def test_issuing_a_token_stores_only_a_hash_and_a_prefix(user):
    record, secret = create_token(user)

    assert len(secret) >= 32
    assert record.key_hash == hashlib.sha256(secret.encode()).hexdigest()
    assert record.prefix == secret[:8]
    assert secret not in {record.name, record.key_hash, record.prefix}
    assert record.expires_at is None
    assert record.is_active
    assert tokens_for(user).get() == record


def test_the_stored_row_cannot_be_turned_back_into_the_secret(user):
    record, secret = create_token(user)

    stored = ApiToken.objects.get(pk=record.pk)
    assert not ApiToken.objects.filter(name=secret).exists()
    assert secret not in str(stored)
    assert secret not in stored.key_hash


def test_a_name_is_unique_per_account_but_shared_between_accounts(user, other_user):
    create_token(user, name="laptop")

    with pytest.raises(ValidationError) as error:
        create_token(user, name="laptop")
    assert NAME_TAKEN in error.value.messages

    create_token(other_user, name="laptop")
    assert ApiToken.objects.filter(name="laptop").count() == 2


def test_the_database_constraint_reports_a_duplicate_name(user, monkeypatch):
    create_token(user, name="laptop")
    # Skip the pre-check so the insert itself has to report the duplicate.
    monkeypatch.setattr("core.api_tokens.clean_name", lambda value, user_id: value)

    with pytest.raises(ValidationError) as error:
        create_token(user, name="laptop")
    assert NAME_TAKEN in error.value.messages
    assert ApiToken.objects.filter(name="laptop").count() == 1


@pytest.mark.parametrize("name", ["", "   ", None, "x" * 256])
def test_a_blank_or_oversized_name_is_rejected(user, name):
    with pytest.raises(ValidationError):
        create_token(user, name=name)
    assert not ApiToken.objects.exists()


def test_an_expiry_may_be_absent_but_a_past_or_naive_one_is_rejected(user):
    _, secret = create_token(user, name="permanent")
    assert ApiToken.objects.get(name="permanent").expires_at is None
    assert resolve_token(secret) is not None

    with pytest.raises(ValidationError):
        create_token(user, name="overdue", expires_at=timezone.now() - timedelta(minutes=1))
    with pytest.raises(ValidationError):
        create_token(user, name="naive", expires_at=datetime(2030, 1, 1, 12, 0))
    assert not ApiToken.objects.filter(name__in=["overdue", "naive"]).exists()


def test_a_revoked_token_stops_resolving_and_cannot_be_revoked_twice(user):
    record, secret = create_token(user)

    revoke_token(actor=user, token=record)
    record.refresh_from_db()
    assert record.revoked_at is not None
    assert not record.is_active
    assert resolve_token(secret) is None

    with pytest.raises(ValidationError) as error:
        revoke_token(actor=user, token=record)
    assert ALREADY_REVOKED in error.value.messages


def test_an_expired_or_inactive_owner_token_stops_resolving(user):
    expired, expired_secret = create_token(user, name="expired")
    with validated_write():
        expired.expires_at = timezone.now() - timedelta(seconds=1)
        expired.save(update_fields=["expires_at"])
    assert resolve_token(expired_secret) is None

    _, inactive_secret = create_token(user, name="inactive-owner")
    user.is_active = False
    user.save(update_fields=["is_active"])
    assert resolve_token(inactive_secret) is None


def test_another_account_cannot_revoke_or_delete_a_token(user, other_user):
    record, _ = create_token(user)

    with pytest.raises(ValidationError) as error:
        revoke_token(actor=other_user, token=record)
    assert OWNER_REQUIRED in error.value.messages

    revoke_token(actor=user, token=record)
    record.refresh_from_db()
    with pytest.raises(ValidationError):
        delete_token(actor=other_user, token=record)
    assert ApiToken.objects.filter(pk=record.pk).exists()


def test_deleting_requires_a_revoked_token_and_frees_the_name(user):
    record, _ = create_token(user, name="laptop")

    with pytest.raises(ValidationError) as error:
        delete_token(actor=user, token=record)
    assert STILL_ACTIVE in error.value.messages
    assert ApiToken.objects.filter(pk=record.pk).exists()

    revoke_token(actor=user, token=record)
    delete_token(actor=user, token=record)
    assert not ApiToken.objects.exists()

    recreate, _ = create_token(user, name="laptop")
    assert recreate.name == "laptop"


def test_the_page_requires_login(client):
    response = client.get(reverse("api-tokens"))

    assert response.status_code == 302
    assert "login" in response.url


def test_the_account_menu_links_to_the_token_page(client, user):
    client.force_login(user)
    page = client.get(reverse("diary:list"))

    assert reverse("api-tokens") in page.text


def test_the_page_lists_metadata_without_any_secret_or_hash(client, user):
    record, secret = create_token(user, name="laptop")
    client.force_login(user)

    page = client.get(reverse("api-tokens"))

    assert page.status_code == 200
    assert "laptop" in page.text
    assert record.prefix in page.text
    assert secret not in page.text
    assert record.key_hash not in page.text
    assert reverse("api-tokens") in page.text


def test_the_page_discloses_what_a_token_can_do_before_one_is_created(client, user):
    client.force_login(user)

    page = client.get(reverse("api-tokens"))

    notice = gettext(
        "A token has the same access as your account: it can read, change and delete the "
        "garden records. Create one only for a client you trust with that access."
    )
    assert page.status_code == 200
    assert notice in page.text
    # Read before the credential exists, not after it has been issued.
    assert page.text.index(notice) < page.text.index('type="submit" class="btn btn-primary"')


def test_creating_a_token_shows_the_secret_once(client, user):
    client.force_login(user)

    response = client.post(reverse("api-tokens"), {"name": "laptop", "expires_at": ""})

    assert response.status_code == 200
    record = ApiToken.objects.get(name="laptop")
    assert record.prefix in response.text
    shown = re.search(r'<code id="token-secret">([^<]+)</code>', response.text).group(1)
    assert hashlib.sha256(shown.encode()).hexdigest() == record.key_hash

    again = client.get(reverse("api-tokens"))
    assert shown not in again.text


def test_a_repeated_name_is_refused_with_an_explanation(client, user):
    client.force_login(user)
    client.post(reverse("api-tokens"), {"name": "laptop", "expires_at": ""})

    duplicate = client.post(reverse("api-tokens"), {"name": "laptop", "expires_at": ""})

    assert duplicate.status_code == 422
    assert ApiToken.objects.count() == 1
    assert "name" in duplicate.context["form"].errors


def test_a_past_expiry_is_refused_before_a_credential_exists(client, user):
    client.force_login(user)
    past = (timezone.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")

    overdue = client.post(reverse("api-tokens"), {"name": "overdue", "expires_at": past})

    assert overdue.status_code == 422
    assert "expires_at" in overdue.context["form"].errors
    assert not ApiToken.objects.filter(name="overdue").exists()


def test_a_chosen_expiry_is_stored_for_the_chosen_day(client, user):
    client.force_login(user)
    chosen = (timezone.localtime() + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M")

    response = client.post(reverse("api-tokens"), {"name": "laptop", "expires_at": chosen})

    assert response.status_code == 200
    record = ApiToken.objects.get(name="laptop")
    assert record.expires_at is not None
    assert record.expires_at > timezone.now()
    assert record.is_active


def test_creation_and_revocation_require_csrf(user):
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)

    assert client.post(reverse("api-tokens"), {"name": "laptop"}).status_code == 403
    assert not ApiToken.objects.exists()

    record, _ = create_token(user)
    assert client.post(reverse("api-token-revoke", args=[record.pk])).status_code == 403
    record.refresh_from_db()
    assert record.revoked_at is None


def test_revoking_through_the_page_keeps_the_row_and_marks_it(client, user):
    record, secret = create_token(user)
    client.force_login(user)

    response = client.post(reverse("api-token-revoke", args=[record.pk]), follow=True)

    assert response.status_code == 200
    record.refresh_from_db()
    assert record.revoked_at is not None
    assert resolve_token(secret) is None
    assert "laptop" in response.text
    assert reverse("api-token-delete", args=[record.pk]) in response.text


def test_the_delete_page_asks_for_confirmation_first(client, user):
    record, _ = create_token(user)
    revoke_token(actor=user, token=record)
    client.force_login(user)

    page = client.get(reverse("api-token-delete", args=[record.pk]))

    assert page.status_code == 200
    assert record.name in page.text
    assert page.context["form"].fields["confirm"].required
    assert ApiToken.objects.filter(pk=record.pk).exists()


def test_the_delete_page_refuses_an_unconfirmed_post(client, user):
    record, _ = create_token(user)
    revoke_token(actor=user, token=record)
    client.force_login(user)

    response = client.post(reverse("api-token-delete", args=[record.pk]))

    assert response.status_code == 422
    assert "confirm" in response.context["form"].errors
    assert ApiToken.objects.filter(pk=record.pk).exists()


def test_the_delete_page_refuses_an_active_token(client, user):
    record, _ = create_token(user)
    client.force_login(user)

    response = client.post(reverse("api-token-delete", args=[record.pk]), {"confirm": "on"})

    assert response.status_code == 302
    assert ApiToken.objects.filter(pk=record.pk).exists()


def test_a_confirmed_delete_removes_the_row(client, user):
    record, _ = create_token(user, name="laptop")
    revoke_token(actor=user, token=record)
    client.force_login(user)

    response = client.post(
        reverse("api-token-delete", args=[record.pk]), {"confirm": "on"}, follow=True
    )

    assert response.status_code == 200
    assert not ApiToken.objects.exists()
    assert "laptop" in response.text


def test_another_account_token_is_invisible_and_unreachable(client, user, other_user):
    theirs, _ = create_token(other_user, name="theirs")
    client.force_login(user)

    page = client.get(reverse("api-tokens"))
    assert "theirs" not in page.text

    assert client.post(reverse("api-token-revoke", args=[theirs.pk])).status_code == 404
    theirs.refresh_from_db()
    assert theirs.revoked_at is None


def test_revoking_rejects_a_get(client, user):
    record, _ = create_token(user)
    client.force_login(user)

    response = client.get(reverse("api-token-revoke", args=[record.pk]))

    assert response.status_code == 405
    record.refresh_from_db()
    assert record.revoked_at is None
    assert ApiToken.objects.filter(pk=record.pk).exists()


def test_the_delete_page_hides_another_account_token(client, user, other_user):
    theirs, _ = create_token(other_user, name="theirs")
    revoke_token(actor=other_user, token=theirs)
    client.force_login(user)

    assert client.get(reverse("api-token-delete", args=[theirs.pk])).status_code == 404
    assert ApiToken.objects.filter(pk=theirs.pk).exists()


def test_the_page_marks_expired_and_revoked_tokens(client, user):
    expired, _ = create_token(user, name="expired")
    with validated_write():
        expired.expires_at = timezone.now() - timedelta(minutes=1)
        expired.save(update_fields=["expires_at"])
    revoked, _ = create_token(user, name="revoked")
    revoke_token(actor=user, token=revoked)
    client.force_login(user)

    page = client.get(reverse("api-tokens"))

    assert page.status_code == 200
    assert page.text.count(reverse("api-token-delete", args=[expired.pk])) == 1
    assert page.text.count(reverse("api-token-delete", args=[revoked.pk])) == 1
    assert reverse("api-token-revoke", args=[revoked.pk]) not in page.text


def test_a_stale_instance_cannot_revoke_twice(user):
    record, _ = create_token(user)
    stale = ApiToken.objects.get(pk=record.pk)
    revoke_token(actor=user, token=record)

    with pytest.raises(ValidationError) as error:
        revoke_token(actor=user, token=stale)
    assert ALREADY_REVOKED in error.value.messages


def test_revoking_a_token_another_request_deleted_reports_it(user):
    record, _ = create_token(user, name="laptop")
    revoke_token(actor=user, token=record)
    delete_token(actor=user, token=record)

    with pytest.raises(ValidationError) as error:
        revoke_token(actor=user, token=record)
    assert GONE in error.value.messages


def test_deleting_a_token_another_request_deleted_reports_it(user):
    record, _ = create_token(user, name="laptop")
    revoke_token(actor=user, token=record)
    delete_token(actor=user, token=record)

    with pytest.raises(ValidationError) as error:
        delete_token(actor=user, token=record)
    assert GONE in error.value.messages
