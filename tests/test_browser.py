"""Real Chromium/form checks; not evidence of device-native photo pickers."""

import os
import re
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def page(live_server, settings, user):
    settings.WHITENOISE_USE_FINDERS = True
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=os.environ.get("GROWBOOK_BROWSER_CHANNEL", "chromium")
        )
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        page.set_default_timeout(8000)
        yield page
        context.close()
        browser.close()


def login(page, live_server, user):
    page.goto(live_server.url + "/accounts/login/")
    page.locator("#id_username").fill(user.username)
    page.locator("#id_password").fill("Synthetic-password-42")
    page.locator('button[type="submit"]').filter(has_text="Logga in").click()
    expect(page).to_have_url(live_server.url + "/")
    page.goto(live_server.url + "/capture/")
    page.wait_for_function("window.htmx !== undefined")


def upload(file, name="synthetic.jpg"):
    return {"name": name, "mimeType": "image/jpeg", "buffer": file.read()}


def test_favicon_is_available_without_login(page, live_server):
    errors = []
    page.on(
        "console", lambda message: errors.append(message.text) if message.type == "error" else None
    )
    page.goto(live_server.url + "/accounts/login/")
    icon = page.locator('link[rel="icon"]')
    expect(icon).to_have_attribute("type", "image/svg+xml")
    expect(icon).to_have_attribute("href", "/static/icons/favicon.svg")
    with page.expect_response(
        lambda response: response.url.endswith("/static/icons/favicon.svg")
    ) as fetched:
        page.evaluate(
            """async () => {
                const image = new Image();
                image.src = document.querySelector('link[rel="icon"]').href;
                await image.decode();
            }"""
        )
    assert fetched.value.status == 200
    assert fetched.value.headers["content-type"] == "image/svg+xml"
    response = page.request.get(live_server.url + "/favicon.ico", max_redirects=0)
    assert response.status == 301
    assert response.headers["location"] == "/static/icons/favicon.svg"
    assert errors == []


def test_photo_links_and_direct_navigation_use_csp_safe_viewer(
    page, live_server, user, image_upload
):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console", lambda message: errors.append(message.text) if message.type == "error" else None
    )
    page.add_init_script(
        """document.addEventListener('securitypolicyviolation', event => {
            console.error('CSP violation: ' + event.effectiveDirective);
        });"""
    )
    login(page, live_server, user)
    page.locator("#id_library").set_input_files(upload(image_upload(size=(1600, 1200))))
    page.locator("#save-entry").click()
    expect(page).to_have_url(re.compile(r"/entries/\d+/$"))
    entry_url = page.url
    photo_link = page.locator(".large-photo > a")
    viewer_path = photo_link.get_attribute("href")
    expect(photo_link).to_have_attribute("href", re.compile(r"/photos/\d+/$"))
    photo_link.click()
    expect(page).to_have_url(live_server.url + viewer_path)
    image = page.locator(".photo-viewer img")
    image.evaluate("image => image.decode()")
    assert image.evaluate("image => [image.naturalWidth, image.naturalHeight]") == [1600, 1200]
    expect(page.get_by_role("link", name="Tillbaka till observationen")).to_have_attribute(
        "href", urlsplit(entry_url).path
    )
    display_path = image.get_attribute("src")
    for variant in ("display", "thumbnail"):
        response = page.goto(live_server.url + display_path.replace("/display/", f"/{variant}/"))
        expect(page).to_have_url(live_server.url + viewer_path)
        assert response.headers["content-type"].startswith("text/html")
        assert "style-src 'self'" in response.headers["content-security-policy"]
        assert "unsafe-inline" not in response.headers["content-security-policy"]
        image.evaluate("image => image.decode()")
    for width in (320, 1280):
        page.set_viewport_size({"width": width, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert image.evaluate("image => image.getBoundingClientRect().right <= innerWidth")
    assert image.evaluate("image => image.getBoundingClientRect().width > 1050")
    with page.expect_download() as downloaded:
        page.get_by_role("link", name="Ladda ner original").click()
    assert downloaded.value.failure() is None
    assert downloaded.value.suggested_filename.endswith(".jpg")
    page.get_by_role("link", name="Tillbaka till observationen").click()
    expect(page).to_have_url(entry_url)
    page.get_by_role("navigation", name="Huvudmeny").get_by_role("link", name="Dagbok").click()
    page.locator(".photo-grid a").click()
    expect(page).to_have_url(live_server.url + viewer_path)
    assert errors == []


def test_multiphoto_capture_accumulates_and_saves_under_strict_csp(
    page,
    live_server,
    user,
    image_upload,
):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console",
        lambda message: (
            errors.append(message.text)
            if "content security policy" in message.text.lower()
            else None
        ),
    )
    login(page, live_server, user)
    assert page.locator("#id_camera").get_attribute("capture") == "environment"
    page.locator("#id_camera").set_input_files(upload(image_upload(), "camera.jpg"))
    page.locator("#id_library").set_input_files(upload(image_upload(), "library-1.jpg"))
    page.locator("#id_library").set_input_files(upload(image_upload(), "library-2.jpg"))
    expect(page.locator("#photo-previews figure")).to_have_count(3)
    expect(page.locator("#photo-count")).to_contain_text("3")
    page.locator("#id_body").fill("Synthetic browser observation — Ärtor")
    page.locator("#save-entry").click()
    expect(page).to_have_url(re.compile(r"/entries/\d+/$"))
    expect(page.locator(".large-photo")).to_have_count(3)
    expect(page.get_by_role("status")).to_contain_text("har sparats")
    expect(page.locator(".entry-body")).to_contain_text("Ärtor")
    assert errors == []
    assert page.evaluate("Object.keys(localStorage).length") == 0


def test_validation_preserves_text_photos_and_submission_key(page, live_server, user, image_upload):
    login(page, live_server, user)
    page.locator("#id_library").set_input_files(upload(image_upload()))
    page.locator("#id_body").fill("Keep this draft")
    key = page.locator("#id_submission_key").input_value()
    # Trigger server validation while deliberately bypassing native date constraints.
    page.locator("#capture-form").evaluate("form => form.noValidate = true")
    page.locator("#id_date").evaluate(
        "input => { input.type = 'text'; input.value = 'invalid-date'; }"
    )
    page.locator("#save-entry").click()
    expect(page.locator("#capture-errors")).not_to_be_empty()
    expect(page.locator("#id_body")).to_have_value("Keep this draft")
    expect(page.locator("#id_submission_key")).to_have_value(key)
    assert page.locator("#id_library").evaluate("input => input.files.length") == 1
    expect(page.locator("#save-entry")).to_be_enabled()


def test_network_error_preserves_selection_then_retry_saves_once(
    page, live_server, user, image_upload
):
    login(page, live_server, user)
    page.locator("#id_library").set_input_files(upload(image_upload()))
    page.locator("#id_body").fill("Retry this observation")
    key = page.locator("#id_submission_key").input_value()
    page.route(
        "**/capture/",
        lambda route: route.abort() if route.request.method == "POST" else route.continue_(),
    )
    page.locator("#save-entry").click()
    expect(page.locator("#capture-errors")).not_to_be_empty()
    expect(page.locator("#save-entry")).to_be_enabled()
    assert page.locator("#id_library").evaluate("input => input.files.length") == 1
    expect(page.locator("#id_submission_key")).to_have_value(key)
    page.unroute("**/capture/")
    page.locator("#save-entry").click()
    expect(page).to_have_url(re.compile(r"/entries/\d+/$"))
    expect(page.locator(".large-photo")).to_have_count(1)


def test_session_expiry_and_new_tab_login_keep_draft_and_rotate_csrf(
    page,
    live_server,
    user,
    image_upload,
):
    login(page, live_server, user)
    page.locator("#id_library").set_input_files(upload(image_upload()))
    page.locator("#id_body").fill("Session recovery")
    key = page.locator("#id_submission_key").input_value()
    other = page.context.new_page()
    other.goto(live_server.url + "/")
    other.locator('button[type="submit"]').filter(has_text="Logga ut").click()
    page.locator("#save-entry").click()
    expect(page.locator("#capture-errors")).to_contain_text("session")
    expect(page.locator("#id_submission_key")).to_have_value(key)
    assert page.locator("#id_library").evaluate("input => input.files.length") == 1
    other.locator("#id_username").fill(user.username)
    other.locator("#id_password").fill("Synthetic-password-42")
    other.locator('button[type="submit"]').filter(has_text="Logga in").click()
    expect(other).to_have_url(live_server.url + "/")
    page.locator("#save-entry").click()
    expect(page).to_have_url(re.compile(r"/entries/\d+/$"))
    expect(page.locator(".large-photo")).to_have_count(1)
    other.close()
