"""Failed sign-ins are throttled per username and connection address on both login routes.

``AXES_ENABLED`` is off in the test settings so parallel suites are never throttled; each
test here enables it for its own requests. ``axes.utils.reset()`` clears attempts before
and after every test so attempts never leak between modules or xdist workers.

The address is the connection address: the test client sets ``REMOTE_ADDR``, as the server
does from the trusted proxy header. A locked-out form post is answered with 429 because
nothing configures ``AXES_LOCKOUT_TEMPLATE`` or ``AXES_LOCKOUT_URL``.
"""

from datetime import timedelta

import pytest
from axes.models import AccessAttempt
from axes.utils import reset
from django.conf import settings
from django.test import override_settings
from django.urls import reverse

pytestmark = pytest.mark.django_db(transaction=True)

WRONG_PASSWORD = "Synthetic-wrong-password-7"
RIGHT_PASSWORD = "Synthetic-password-42"
CLIENT_ADDRESS = "203.0.113.7"
OTHER_ADDRESS = "203.0.113.8"


@pytest.fixture(autouse=True)
def clear_attempts():
    reset()
    yield
    reset()


def sign_in(client, route, username, password, address=CLIENT_ADDRESS):
    return client.post(
        reverse(route), {"username": username, "password": password}, REMOTE_ADDR=address
    )


def lockout_expiry(attempt):
    """When axes stops counting this attempt: its last recorded time plus the cooloff."""
    return attempt.attempt_time + timedelta(hours=settings.AXES_COOLOFF_TIME)


def fail_sign_ins(client, route, username, address=CLIENT_ADDRESS, attempts=3):
    """Post wrong passwords and require the failure limit to lock the account out."""
    for _ in range(attempts):
        response = sign_in(client, route, username, WRONG_PASSWORD, address)
    assert response.status_code == 429, response.status_code
    return address


@override_settings(AXES_ENABLED=True, AXES_FAILURE_LIMIT=3)
def test_a_correct_password_signs_in_before_any_failure(client, user):
    response = sign_in(client, "login", user.username, RIGHT_PASSWORD)

    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(user.pk)


@override_settings(AXES_ENABLED=True, AXES_FAILURE_LIMIT=3)
def test_the_login_route_locks_the_account_and_then_refuses_the_right_password(client, user):
    address = fail_sign_ins(client, "login", user.username)

    response = sign_in(client, "login", user.username, RIGHT_PASSWORD, address)

    assert response.status_code == 429
    assert client.session.get("_auth_user_id") is None


@override_settings(AXES_ENABLED=True, AXES_FAILURE_LIMIT=3)
def test_the_operator_route_locks_the_account_and_then_refuses_the_right_password(
    client, staff_user
):
    address = fail_sign_ins(client, "admin:login", staff_user.username)

    response = sign_in(client, "admin:login", staff_user.username, RIGHT_PASSWORD, address)

    assert response.status_code == 429
    assert client.session.get("_auth_user_id") is None


@override_settings(AXES_ENABLED=True, AXES_FAILURE_LIMIT=3)
def test_a_blocked_retry_does_not_extend_the_lockout(client, user):
    address = fail_sign_ins(client, "login", user.username)
    attempt = AccessAttempt.objects.get(username=user.username, ip_address=address)
    failures, expiry = attempt.failures_since_start, lockout_expiry(attempt)

    response = sign_in(client, "login", user.username, RIGHT_PASSWORD, address)

    assert response.status_code == 429
    attempt.refresh_from_db()
    assert attempt.failures_since_start == failures
    assert lockout_expiry(attempt) == expiry


@override_settings(AXES_ENABLED=True, AXES_FAILURE_LIMIT=3)
def test_another_account_from_the_same_address_is_not_locked(client, user, other_user):
    address = fail_sign_ins(client, "login", user.username)

    response = sign_in(client, "login", other_user.username, RIGHT_PASSWORD, address)

    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(other_user.pk)


@override_settings(AXES_ENABLED=True, AXES_FAILURE_LIMIT=3)
def test_the_same_account_from_another_address_is_not_locked(client, user):
    fail_sign_ins(client, "login", user.username)

    response = sign_in(client, "login", user.username, RIGHT_PASSWORD, OTHER_ADDRESS)

    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(user.pk)
