"""Failed sign-ins are throttled per username and client address on both login routes.

``AXES_ENABLED`` is off in the test settings so parallel suites are never throttled; each
test here enables it for its own requests. ``axes.utils.reset()`` clears attempts before
and after every test so attempts never leak between modules or xdist workers.

A locked-out form post is answered with 429: nothing configures ``AXES_LOCKOUT_TEMPLATE`` or
``AXES_LOCKOUT_URL``, so Axes uses ``AXES_HTTP_RESPONSE_CODE``.
"""

import pytest
from axes.utils import reset
from django.test import override_settings
from django.urls import reverse

pytestmark = pytest.mark.django_db(transaction=True)

WRONG_PASSWORD = "Synthetic-wrong-password-7"
RIGHT_PASSWORD = "Synthetic-password-42"
CLIENT_ADDRESS = {"HTTP_X_FORWARDED_FOR": "203.0.113.7"}
OTHER_ADDRESS = {"HTTP_X_FORWARDED_FOR": "203.0.113.8"}


@pytest.fixture(autouse=True)
def clear_attempts():
    reset()
    yield
    reset()


def fail_sign_ins(client, route, username, address=CLIENT_ADDRESS, attempts=3):
    """Post wrong passwords and require the failure limit to lock the account out."""
    url = reverse(route)
    for _ in range(attempts):
        response = client.post(url, {"username": username, "password": WRONG_PASSWORD}, **address)
    assert response.status_code == 429, response.status_code
    return url


@override_settings(AXES_ENABLED=True, AXES_FAILURE_LIMIT=3)
def test_a_correct_password_signs_in_before_any_failure(client, user):
    response = client.post(
        reverse("login"),
        {"username": user.username, "password": RIGHT_PASSWORD},
        **CLIENT_ADDRESS,
    )

    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(user.pk)


@override_settings(AXES_ENABLED=True, AXES_FAILURE_LIMIT=3)
def test_the_login_route_locks_the_account_and_then_refuses_the_right_password(client, user):
    url = fail_sign_ins(client, "login", user.username)

    response = client.post(
        url, {"username": user.username, "password": RIGHT_PASSWORD}, **CLIENT_ADDRESS
    )

    assert response.status_code == 429
    assert client.session.get("_auth_user_id") is None


@override_settings(AXES_ENABLED=True, AXES_FAILURE_LIMIT=3)
def test_the_operator_route_locks_the_account_and_then_refuses_the_right_password(
    client, staff_user
):
    url = fail_sign_ins(client, "admin:login", staff_user.username)

    response = client.post(
        url, {"username": staff_user.username, "password": RIGHT_PASSWORD}, **CLIENT_ADDRESS
    )

    assert response.status_code == 429
    assert client.session.get("_auth_user_id") is None


@override_settings(AXES_ENABLED=True, AXES_FAILURE_LIMIT=3)
def test_another_account_from_the_same_address_is_not_locked(client, user, other_user):
    fail_sign_ins(client, "login", user.username)

    response = client.post(
        reverse("login"),
        {"username": other_user.username, "password": RIGHT_PASSWORD},
        **CLIENT_ADDRESS,
    )

    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(other_user.pk)


@override_settings(AXES_ENABLED=True, AXES_FAILURE_LIMIT=3)
def test_the_same_account_from_another_address_is_not_locked(client, user):
    fail_sign_ins(client, "login", user.username)

    response = client.post(
        reverse("login"),
        {"username": user.username, "password": RIGHT_PASSWORD},
        **OTHER_ADDRESS,
    )

    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(user.pk)
