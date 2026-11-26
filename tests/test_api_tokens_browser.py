"""The API tokens page: create a secret once, revoke it, then delete the row."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from browser_helpers import browser_page, login
from django.db import close_old_connections
from playwright.sync_api import expect

from core.models import ApiToken

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


def state():
    """Read the credential rows off the browser thread's event loop."""

    def read():
        close_old_connections()
        try:
            return {
                "names": list(ApiToken.objects.values_list("name", flat=True)),
                "prefixes": list(ApiToken.objects.values_list("name", "prefix")),
                "revoked": list(ApiToken.objects.values_list("name", "revoked_at")),
            }
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(read).result()


@pytest.fixture
def page(live_server, user, browser_endpoint):
    with browser_page(browser_endpoint, viewport={"width": 1280, "height": 900}) as page:
        login(page, live_server, user, "en")
        yield page


def open_tokens_page(page, live_server):
    page.locator("#account-menu summary").click()
    page.get_by_role("link", name="API tokens").click()
    expect(page).to_have_url(live_server.url + "/accounts/tokens/")
    expect(page.get_by_role("heading", name="API tokens")).to_be_visible()


def create_token(page, name):
    page.locator("#id_name").fill(name)
    page.get_by_role("button", name="Create token").click()


def test_the_account_menu_opens_the_token_page(page, live_server):
    open_tokens_page(page, live_server)

    expect(page.get_by_text("No tokens yet.")).to_be_visible()
    expect(page.locator("#id_name")).to_be_visible()
    expect(page.locator("#id_expires_at")).to_be_visible()


def test_a_created_secret_is_shown_once_and_then_only_listed(page, live_server):
    open_tokens_page(page, live_server)

    create_token(page, "window-laptop")

    secret = page.locator("#token-secret")
    expect(secret).to_be_visible()
    expect(page.get_by_text("Secret for window-laptop — shown only once")).to_be_visible()
    shown = secret.inner_text()
    assert len(shown) >= 32

    page.goto(live_server.url + "/accounts/tokens/")

    expect(page.locator("#token-secret")).to_have_count(0)
    expect(page.get_by_text("window-laptop", exact=True)).to_be_visible()
    expect(page.get_by_text(shown[:8] + "…")).to_be_visible()
    assert state()["prefixes"] == [("window-laptop", shown[:8])]


def test_a_repeated_name_reports_the_field_error_without_a_second_token(page, live_server):
    open_tokens_page(page, live_server)
    create_token(page, "window-laptop")
    page.goto(live_server.url + "/accounts/tokens/")

    create_token(page, "window-laptop")

    expect(page.locator("#id_name_errors")).to_contain_text(
        "A token with this name already exists."
    )
    expect(page.locator("#id_name")).to_have_value("window-laptop")
    assert state()["names"] == ["window-laptop"]


def test_revoking_then_deleting_frees_the_name(page, live_server):
    open_tokens_page(page, live_server)
    create_token(page, "old-laptop")
    page.goto(live_server.url + "/accounts/tokens/")

    page.get_by_role("button", name="Revoke").click()

    expect(
        page.get_by_text("Revoked old-laptop. Clients using it stop working now.")
    ).to_be_visible()
    assert state()["revoked"][0][1] is not None

    page.get_by_role("link", name="Delete").click()

    expect(page.get_by_role("heading", name="Delete API token: old-laptop")).to_be_visible()
    expect(page.get_by_text("No tokens yet.")).to_have_count(0)
    page.locator("#id_confirm").check()
    page.get_by_role("button", name="Delete token").click()

    expect(page.get_by_text("Deleted old-laptop. That name can be used again.")).to_be_visible()
    expect(page.get_by_text("No tokens yet.")).to_be_visible()
    assert state()["names"] == []

    create_token(page, "old-laptop")
    assert state()["names"] == ["old-laptop"]


def test_an_active_token_offers_no_delete_action(page, live_server):
    open_tokens_page(page, live_server)
    create_token(page, "kept-laptop")
    page.goto(live_server.url + "/accounts/tokens/")

    expect(page.get_by_role("link", name="Delete")).to_have_count(0)

    page.get_by_role("button", name="Revoke").click()

    expect(page.get_by_role("link", name="Delete")).to_be_visible()
