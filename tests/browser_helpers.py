"""Shared Chromium setup, real login forms and bounded resource waits."""

from contextlib import contextmanager

from playwright.sync_api import expect, sync_playwright


@contextmanager
def browser_page(browser_endpoint, *, timeout=8000, **context_options):
    # Start the sync event loop only after the test's Django setup has finished.
    with (
        sync_playwright() as playwright,
        playwright.chromium.connect(browser_endpoint, timeout=20000) as browser,
        browser.new_context(**context_options) as context,
    ):
        page = context.new_page()
        page.set_default_timeout(timeout)
        yield page


def login(page, live_server, user, language=None):
    if language is not None:
        page.context.add_cookies(
            [{"name": "django_language", "value": language, "url": live_server.url}]
        )
    page.goto(live_server.url + "/accounts/login/")
    page.locator("#id_username").fill(user.username)
    page.locator("#id_password").fill("Synthetic-password-42")
    page.locator('.login-panel button[type="submit"]').click()
    expect(page).to_have_url(live_server.url + "/")


def wait_for_image(image):
    image.scroll_into_view_if_needed(timeout=8000)
    image.wait_for_function("image => image.complete && image.naturalWidth > 0", timeout=8000)
