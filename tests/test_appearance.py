from datetime import UTC, datetime

import pytest
from django.test import Client
from django.utils import timezone

from core.appearance import SEASON_COOKIE, current_season


@pytest.mark.parametrize(
    "month,season",
    [
        (1, "winter"),
        (2, "winter"),
        (3, "spring"),
        (4, "spring"),
        (5, "spring"),
        (6, "summer"),
        (7, "summer"),
        (8, "summer"),
        (9, "autumn"),
        (10, "autumn"),
        (11, "autumn"),
        (12, "winter"),
    ],
)
def test_automatic_season_uses_calendar_months(settings, monkeypatch, month, season):
    settings.TIME_ZONE = "Europe/Stockholm"
    monkeypatch.setattr(timezone, "now", lambda: datetime(2026, month, 15, tzinfo=UTC))
    assert current_season() == season


@pytest.mark.parametrize("zone,season", [("Europe/Stockholm", "spring"), ("UTC", "winter")])
def test_season_boundary_uses_the_garden_timezone(settings, monkeypatch, zone, season):
    settings.TIME_ZONE = zone
    monkeypatch.setattr(timezone, "now", lambda: datetime(2026, 2, 28, 23, 30, tzinfo=UTC))
    assert current_season() == season


@pytest.mark.django_db
@pytest.mark.parametrize("mode", ["auto", "spring", "summer", "autumn", "winter"])
def test_season_form_is_public_and_remembers_a_browser_cookie(client, mode):
    response = client.post("/accounts/season/", {"season": mode, "next": "/accounts/login/"})
    assert response.status_code == 302 and response.url == "/accounts/login/"
    cookie = response.cookies[SEASON_COOKIE]
    assert cookie.value == mode and cookie["path"] == "/" and cookie["samesite"] == "Lax"
    assert not cookie["httponly"]
    page = client.get(response.url)
    assert page.context["season_mode"] == mode
    assert page.context["season"] == (current_season() if mode == "auto" else mode)


@pytest.mark.django_db
def test_invalid_mode_is_rejected_and_invalid_cookie_falls_back_to_automatic(client):
    assert client.get("/accounts/season/").status_code == 405
    response = client.post("/accounts/season/", {"season": "unknown"})
    assert response.status_code == 400 and SEASON_COOKIE not in response.cookies
    client.cookies[SEASON_COOKIE] = "unknown"
    page = client.get("/accounts/login/")
    assert page.context["season_mode"] == "auto"
    assert page.context["season"] == current_season()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "mode",
    [
        None,
        "WINTER",
        "winter; Path=/; SameSite=None",
        "winter\r\nSet-Cookie: injected=synthetic",
        "winter\x00",
    ],
)
def test_season_form_rejects_cookie_injection_without_changing_the_preference(client, mode):
    client.cookies[SEASON_COOKIE] = "summer"
    data = {"next": "/accounts/login/"}
    if mode is not None:
        data["season"] = mode
    response = client.post("/accounts/season/", data)
    assert response.status_code == 400
    assert SEASON_COOKIE not in response.cookies
    assert client.cookies[SEASON_COOKIE].value == "summer"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "destination",
    [
        "https://outside.example/",
        "https:/outside.example/",
        "https:///outside.example/",
        "//outside.example/",
        "///outside.example/",
        r"/\outside.example/",
        r"\\outside.example/",
        "\t//outside.example/",
        "//[invalid-host/",
        "javascript:alert(1)",
        "http://testserver/accounts/login/",
        "https://testserver/accounts/login/",
        "//testserver/accounts/login/",
        "",
        "unknown-route",
        "favicon",
        "?next=//outside.example/",
        "#main",
    ],
)
@pytest.mark.parametrize("secure", [False, True])
def test_season_form_rejects_non_local_or_malformed_return_paths(client, destination, secure):
    response = client.post(
        "/accounts/season/", {"season": "winter", "next": destination}, secure=secure
    )
    assert response.status_code == 302 and response["Location"] == "/"
    assert response.cookies[SEASON_COOKIE].value == "winter"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "destination",
    [
        "/",
        "/accounts/login/?next=/garden/areas/",
        "/?q=%C3%85sa&year=2026",
        "/garden/varieties/?q=Synthetic&archived=1",
        "/?q=https%3A%2F%2Foutside.example%2F",
    ],
)
@pytest.mark.parametrize("secure", [False, True])
def test_season_form_preserves_local_return_paths_and_filters(client, destination, secure):
    response = client.post(
        "/accounts/season/", {"season": "winter", "next": destination}, secure=secure
    )
    assert response.status_code == 302 and response["Location"] == destination
    assert response.cookies[SEASON_COOKIE].value == "winter"


@pytest.mark.django_db
def test_season_selection_requires_csrf_and_uses_secure_cookies_over_https():
    client = Client(enforce_csrf_checks=True)
    client.get("/accounts/login/", secure=True)
    assert client.post("/accounts/season/", {"season": "winter"}, secure=True).status_code == 403
    response = client.post(
        "/accounts/season/",
        {
            "season": "winter",
            "next": "/accounts/login/",
            "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        },
        secure=True,
        HTTP_ORIGIN="https://testserver",
    )
    assert response.status_code == 302 and response.cookies[SEASON_COOKIE]["secure"]
