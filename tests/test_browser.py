"""Real Chromium/form checks; not evidence of device-native photo pickers."""

import os
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import pytest
from django.db import close_old_connections, connection
from playwright.sync_api import expect, sync_playwright

from garden.services import create_crop

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


def open_entry_edit(page, live_server, user, photos=()):
    login(page, live_server, user)
    page.locator("#id_body").fill("Synthetic saved observation")
    if photos:
        page.locator("#id_library").set_input_files(photos)
    page.locator("#save-entry").click()
    expect(page).to_have_url(re.compile(r"/entries/\d+/$"))
    entry_url = page.url
    page.goto(entry_url + "edit/")
    expect(page.locator("#entry-form")).to_be_visible()
    return entry_url


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


def test_preview_treats_hostile_filenames_and_content_as_data(page, live_server, user):
    login(page, live_server, user)
    filename = '<img src=x onerror="window.previewExecuted=true">.svg'
    page.locator("#id_library").set_input_files(
        {
            "name": filename,
            "mimeType": "image/svg+xml",
            "buffer": b'<svg xmlns="http://www.w3.org/2000/svg" '
            b'onload="window.previewExecuted=true"><script>'
            b"window.previewExecuted=true</script></svg>",
        }
    )
    preview = page.locator("#photo-previews figure")
    expect(preview).to_have_count(1)
    expect(preview.locator("figcaption")).to_have_text(filename)
    expect(preview.locator("img")).to_have_attribute("src", re.compile(r"^blob:"))
    expect(preview.locator("img")).to_have_attribute("alt", filename)
    preview.locator("img").evaluate("image => image.decode()")
    assert preview.locator("img").count() == 1
    assert preview.locator("script, svg, [onerror], [onload]").count() == 0
    assert page.evaluate("window.previewExecuted === undefined")
    preview.get_by_role("button", name=f"Ta bort: {filename}", exact=True).click()
    expect(page.locator("#photo-previews figure")).to_have_count(0)


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


def test_edit_links_filter_and_delete_round_trip(page, live_server, user):
    def create_browser_crop():
        close_old_connections()
        try:
            actor = type(user).objects.get(pk=user.pk)
            return create_crop(actor=actor, name="Synthetic browser crop").record.pk
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        crop_pk = executor.submit(create_browser_crop).result(timeout=20)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, live_server, user)
    page.locator("#id_body").fill("Before browser edit")
    page.locator("#save-entry").click()
    expect(page).to_have_url(re.compile(r"/entries/\d+/$"))
    entry_url = page.url

    page.locator('a[href$="/edit/"]').first.click()
    expect(page.locator("#entry-form")).to_be_visible()
    page.locator("#id_body").fill("Browser edited observation")
    expect(page.locator("#id_link_crops")).to_be_visible()
    page.locator("#id_link_crops").select_option(label="Synthetic browser crop")
    page.locator("#entry-form button[type=submit]").click()
    expect(page).to_have_url(entry_url)
    expect(page.locator(".entry-body")).to_contain_text("Browser edited observation")
    expect(page.locator(".link-tags")).to_contain_text("Synthetic browser crop")

    page.goto(f"{live_server.url}/?crop={crop_pk}")
    expect(page.locator(".entry-body")).to_contain_text("Browser edited observation")
    page.locator(".diary-card .entry-heading a").click()
    expect(page).to_have_url(entry_url)

    page.locator('a[href$="/delete/"]').click()
    page.locator("#id_confirm").check()
    page.locator("button.danger").click()
    expect(page).to_have_url(live_server.url + "/")
    expect(page.locator(".diary-card")).to_have_count(0)
    expect(page.get_by_text("Browser edited observation")).to_have_count(0)
    assert errors == []


def test_edit_validation_preserves_draft_and_photo_changes_then_retry_saves(
    page, live_server, user, image_upload
):
    script_errors = []
    page.on("pageerror", lambda error: script_errors.append(str(error)))
    entry_url = open_entry_edit(
        page,
        live_server,
        user,
        [upload(image_upload(), "first.jpg"), upload(image_upload(), "second.jpg")],
    )
    form = page.locator("#entry-form")
    errors = page.locator("#entry-errors")
    version = page.locator("#id_expected_updated_at").input_value()
    removal = form.locator('input[name="remove_photos"]').first
    removal.check()
    page.locator("#id_body").fill("Keep the edited note and photo changes")
    page.locator("#id_photos").set_input_files(upload(image_upload(), "replacement.jpg"))
    form.evaluate("form => form.noValidate = true")
    page.locator("#id_date").evaluate(
        "input => { input.type = 'text'; input.value = 'invalid-date'; }"
    )

    with page.expect_response(
        lambda response: response.url == entry_url + "edit/" and response.request.method == "POST"
    ) as rejected:
        form.locator("button[type=submit]").click()

    assert rejected.value.status == 422
    expect(errors).not_to_be_empty()
    expect(errors).to_be_focused()
    expect(page.locator("#id_body")).to_have_value("Keep the edited note and photo changes")
    expect(page.locator("#id_expected_updated_at")).to_have_value(version)
    expect(removal).to_be_checked()
    assert page.locator("#id_photos").evaluate("input => input.files.length") == 1
    expect(page.locator("#id_photos")).to_be_enabled()
    expect(form.locator("button[type=submit]")).to_be_enabled()

    page.locator("#id_date").fill("2026-06-01")
    form.locator("button[type=submit]").click()
    expect(page).to_have_url(entry_url)
    expect(page.locator(".entry-body")).to_contain_text("Keep the edited note and photo changes")
    expect(page.locator(".large-photo")).to_have_count(2)
    assert script_errors == []


def test_edit_conflict_is_visible_and_preserves_the_unsaved_draft(
    page, live_server, user, image_upload
):
    entry_url = open_entry_edit(page, live_server, user)
    version = page.locator("#id_expected_updated_at").input_value()
    page.locator("#id_body").fill("Unsaved stale edit")
    page.locator("#id_photos").set_input_files(upload(image_upload()))
    other = page.context.new_page()
    other.goto(entry_url + "edit/")
    other.locator("#id_body").fill("Saved in another tab")
    other.locator("#entry-form button[type=submit]").click()
    expect(other).to_have_url(entry_url)

    with page.expect_response(
        lambda response: response.url == entry_url + "edit/" and response.request.method == "POST"
    ) as rejected:
        page.locator("#entry-form button[type=submit]").click()

    assert rejected.value.status == 409
    expect(page.locator("#entry-errors")).not_to_be_empty()
    expect(page.locator("#entry-errors")).to_be_focused()
    expect(page.locator("#id_body")).to_have_value("Unsaved stale edit")
    expect(page.locator("#id_expected_updated_at")).to_have_value(version)
    assert page.locator("#id_photos").evaluate("input => input.files.length") == 1
    expect(page.locator("#entry-form button[type=submit]")).to_be_enabled()
    other.reload()
    expect(other.locator(".entry-body")).to_have_text("Saved in another tab")
    expect(other.locator(".large-photo")).to_have_count(0)
    other.close()


@pytest.mark.parametrize("failure", [503, "network"])
def test_edit_failure_unlocks_the_form_and_preserves_photos_for_retry(
    page, live_server, user, image_upload, failure
):
    entry_url = open_entry_edit(page, live_server, user)
    form = page.locator("#entry-form")
    save = form.locator("button[type=submit]")
    page.locator("#id_body").fill("Retry the edited observation")
    page.locator("#id_photos").set_input_files(upload(image_upload()))
    version = page.locator("#id_expected_updated_at").input_value()
    pending = []

    def hold_post(route):
        if route.request.method == "POST":
            pending.append(route)
        else:
            route.continue_()

    page.route(entry_url + "edit/", hold_post)
    save.click()
    expect(form).to_have_attribute("aria-busy", "true")
    expect(save).to_be_disabled()
    expect(page.locator("#id_photos")).to_be_disabled()
    expect(page.locator("#id_link_crops")).to_be_disabled()
    expect(page.locator("#id_body")).not_to_be_editable()
    assert len(pending) == 1
    if failure == 503:
        pending[0].fulfill(
            status=503,
            content_type="text/html",
            body='<div class="error-summary">Synthetic temporary save failure</div>',
        )
    else:
        pending[0].abort()

    expect(page.locator("#entry-errors")).not_to_be_empty()
    expect(page.locator("#entry-errors")).to_be_focused()
    if failure == 503:
        expect(page.locator("#entry-errors")).to_contain_text("Synthetic temporary save failure")
    expect(save).to_be_enabled()
    expect(page.locator("#id_photos")).to_be_enabled()
    expect(page.locator("#id_body")).to_be_editable()
    expect(page.locator("#id_body")).to_have_value("Retry the edited observation")
    expect(page.locator("#id_expected_updated_at")).to_have_value(version)
    assert page.locator("#id_photos").evaluate("input => input.files.length") == 1

    page.unroute(entry_url + "edit/")
    save.click()
    expect(page).to_have_url(entry_url)
    expect(page.locator(".entry-body")).to_have_text("Retry the edited observation")
    expect(page.locator(".large-photo")).to_have_count(1)


def test_edit_session_recovery_keeps_draft_and_refreshes_csrf(
    page, live_server, user, image_upload
):
    entry_url = open_entry_edit(page, live_server, user)
    page.locator("#id_body").fill("Edit after logging in again")
    page.locator("#id_photos").set_input_files(upload(image_upload()))
    version = page.locator("#id_expected_updated_at").input_value()
    other = page.context.new_page()
    other.goto(live_server.url + "/")
    other.get_by_role("button", name="Logga ut", exact=True).click()

    with page.expect_response(
        lambda response: response.url == entry_url + "edit/" and response.request.method == "POST"
    ) as rejected:
        page.locator("#entry-form button[type=submit]").click()

    assert rejected.value.status == 401
    expect(page.locator("#entry-errors")).to_contain_text("session")
    expect(page.locator("#entry-errors")).to_be_focused()
    expect(page.locator("#id_body")).to_have_value("Edit after logging in again")
    expect(page.locator("#id_expected_updated_at")).to_have_value(version)
    assert page.locator("#id_photos").evaluate("input => input.files.length") == 1
    expect(page.locator("#entry-form button[type=submit]")).to_be_enabled()

    other.locator("#id_username").fill(user.username)
    other.locator("#id_password").fill("Synthetic-password-42")
    other.get_by_role("button", name="Logga in", exact=True).click()
    expect(other).to_have_url(live_server.url + "/")
    page.locator("#entry-form button[type=submit]").click()
    expect(page).to_have_url(entry_url)
    expect(page.locator(".entry-body")).to_have_text("Edit after logging in again")
    expect(page.locator(".large-photo")).to_have_count(1)
    other.close()
