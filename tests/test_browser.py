"""Real Chromium/form checks; not evidence of device-native photo pickers."""

import os
import re
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from urllib.parse import urlsplit

import pytest
from django.db import close_old_connections, connection
from PIL import Image
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


def test_more_options_warns_before_dropping_a_quick_draft(page, live_server, user):
    login(page, live_server, user)
    page.goto(live_server.url + "/")
    expect(page.locator("#capture-form")).to_be_visible()
    page.locator("#id_body").fill("Keep this quick note")
    dialogs = []
    page.on(
        "dialog",
        lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()),
    )
    page.get_by_role("link", name="Fler alternativ").click()
    assert dialogs
    expect(page).to_have_url(live_server.url + "/")
    assert page.locator("#id_body").input_value() == "Keep this quick note"


def test_favicon_is_available_without_login(page, live_server):
    errors = []
    page.on(
        "console", lambda message: errors.append(message.text) if message.type == "error" else None
    )
    page.goto(live_server.url + "/accounts/login/")
    expect(page.locator(".login-panel .brand-mark")).to_be_visible()
    expect(page.locator(".login-panel .brand-mark")).to_have_attribute("alt", "")
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
    touch_icon = page.locator('link[rel="apple-touch-icon"]')
    expect(touch_icon).to_have_attribute("sizes", "180x180")
    expect(touch_icon).to_have_attribute("href", "/static/icons/apple-touch-icon.png")
    response = page.request.get(live_server.url + touch_icon.get_attribute("href"))
    assert response.status == 200
    assert response.headers["content-type"] == "image/png"
    with Image.open(BytesIO(response.body())) as touch_image:
        assert touch_image.size == (180, 180)
        assert touch_image.convert("RGBA").getextrema()[3] == (255, 255)
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
    expect(page.locator("#photo-count")).to_have_text("Valda bilder: 3 av 10")
    page.locator("#id_body").fill("Synthetic browser observation — Ärtor")
    page.locator("#save-entry").click()
    expect(page).to_have_url(re.compile(r"/entries/\d+/$"))
    expect(page.locator(".large-photo")).to_have_count(3)
    expect(page.get_by_role("status")).to_contain_text("har sparats")
    expect(page.locator(".entry-body")).to_contain_text("Ärtor")
    assert errors == []
    assert page.evaluate("Object.keys(localStorage).length") == 0


@pytest.mark.parametrize("mime_type", ["image/svg+xml", "text/html"])
def test_preview_treats_hostile_filenames_and_content_as_data(page, live_server, user, mime_type):
    login(page, live_server, user)
    filename = '<img src=x onerror="window.previewExecuted=true">.svg'
    page.locator("#id_library").set_input_files(
        {
            "name": filename,
            "mimeType": mime_type,
            "buffer": b'<svg xmlns="http://www.w3.org/2000/svg" '
            b'onload="window.previewExecuted=true"><script>'
            b"window.previewExecuted=true</script></svg>",
        }
    )
    preview = page.locator("#photo-previews figure")
    expect(preview).to_have_count(1)
    expect(preview.locator("figcaption")).to_have_text(filename)
    if mime_type == "image/svg+xml":
        expect(preview.locator("img")).to_have_attribute("src", re.compile(r"^blob:"))
        expect(preview.locator("img")).to_have_attribute("alt", filename)
        preview.locator("img").evaluate("image => image.decode()")
        assert preview.locator("img").count() == 1
    else:
        expect(preview.locator("img")).to_have_count(0)
        expect(preview.locator("p")).to_have_text(
            page.locator("[data-photo-picker]").get_attribute("data-preview-unavailable")
        )
    assert preview.locator("script, svg, [onerror], [onload]").count() == 0
    assert page.evaluate("window.previewExecuted === undefined")
    key = page.locator("#id_submission_key").input_value()
    page.locator("#id_body").fill("Synthetic draft with unsupported photo")
    page.locator("#save-entry").click()
    expect(page.locator("#capture-errors")).not_to_be_empty()
    expect(page.locator("#id_body")).to_have_value("Synthetic draft with unsupported photo")
    expect(page.locator("#id_submission_key")).to_have_value(key)
    assert page.locator("#id_library").evaluate("input => input.files.length") == 1
    preview.get_by_role("button", name=f"Ta bort: {filename}", exact=True).click()
    expect(page.locator("#photo-previews figure")).to_have_count(0)


@pytest.mark.parametrize(
    "preview_url",
    [
        "javascript:window.previewExecuted=true",
        "data:image/svg+xml,<svg onload='window.previewExecuted=true'/>",
        "https://example.invalid/preview.jpg",
        'blob:<img src=x onerror="window.previewExecuted=true">',
    ],
)
def test_preview_rejects_unexpected_urls_without_losing_upload(
    page, live_server, user, image_upload, preview_url
):
    login(page, live_server, user)
    page.evaluate("url => { URL.createObjectURL = () => url; }", preview_url)
    page.locator("#id_library").set_input_files(upload(image_upload()))
    preview = page.locator("#photo-previews figure")
    expect(preview).to_have_count(1)
    expect(preview.locator("img")).to_have_count(0)
    expect(preview.locator("p")).to_have_text(
        page.locator("[data-photo-picker]").get_attribute("data-preview-unavailable")
    )
    assert preview.locator("script, svg, [onerror], [onload]").count() == 0
    assert page.evaluate("window.previewExecuted === undefined")
    assert page.locator("#id_library").evaluate("input => input.files.length") == 1
    page.locator("#save-entry").click()
    expect(page).to_have_url(re.compile(r"/entries/\d+/$"))
    expect(page.locator(".large-photo")).to_have_count(1)


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
    expect(page.locator("#id_date")).to_have_attribute("aria-invalid", "true")
    expect(page.locator("#capture-date-errors")).to_contain_text("datum")
    expect(page.locator("#capture-errors")).to_be_focused()
    page.locator('#capture-errors a[href="#id_date"]').click()
    expect(page.locator("#id_date")).to_be_focused()
    page.locator("#id_date").fill("2026-05-09")
    page.locator("#save-entry").click()
    expect(page).to_have_url(re.compile(r"/entries/\d+/$"))
    expect(page.locator(".entry-body")).to_have_text("Keep this draft")
    expect(page.locator(".large-photo")).to_have_count(1)


@pytest.mark.parametrize("language", ["sv", "en"])
def test_capture_foundation_reflows_and_supports_keyboard(
    page, live_server, user, image_upload, language
):
    login(page, live_server, user)
    page.context.add_cookies(
        [{"name": "django_language", "value": language, "url": live_server.url}]
    )
    page.reload()
    expect(page.locator("html")).to_have_attribute("lang", language)
    for width in (320, 390, 1280):
        page.set_viewport_size({"width": width, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        for selector in ("#save-entry", ".capture-actions a", "#id_date", "#id_body"):
            box = page.locator(selector).bounding_box()
            assert box["width"] >= 44 and box["height"] >= 44
    page.locator("#id_body").focus()
    page.keyboard.press("Tab")
    expect(page.locator("[data-photo-chooser] > summary")).to_be_focused()
    page.keyboard.press("Enter")
    page.keyboard.press("Tab")
    expect(page.locator("[data-pick-photo=camera]")).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.locator("[data-pick-photo=library]")).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.locator("[data-photo-chooser] > summary")).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.locator(".photo-limits > summary")).to_be_focused()
    assert page.locator("#capture-form").evaluate(
        """form => [...form.querySelectorAll('[aria-describedby]')].every(input =>
            input.getAttribute('aria-describedby').split(' ').every(id =>
                document.getElementById(id)))"""
    )
    page.emulate_media(reduced_motion="reduce")
    assert page.locator("#save-entry").evaluate(
        """button => getComputedStyle(button).transitionDuration.split(',')
            .every(t => t.trim() === '0s')"""
    )
    page.locator("#id_library").set_input_files(upload(image_upload()))
    expected = "Valda bilder: 1 av 10" if language == "sv" else "Photos selected: 1 of 10"
    expect(page.locator("[data-photo-count]")).to_have_text(expected)
    page.locator("#capture-form").evaluate("form => form.reset()")
    expect(page.locator("[data-photo-count]")).to_have_text("")
    expect(page.locator("[data-photo-previews] figure")).to_have_count(0)
    # Emulate larger user text with a self-hosted stylesheet under the unchanged CSP.
    page.route(
        "**/static/css/test-text-size.css",
        lambda route: route.fulfill(
            status=200, content_type="text/css", body=":root { font-size: 212.5%; }"
        ),
    )
    for path in ("/capture/", "/"):
        page.goto(live_server.url + path)
        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate(
            """() => {
                const link = document.createElement('link');
                link.rel = 'stylesheet'; link.href = '/static/css/test-text-size.css';
                document.head.append(link);
            }"""
        )
        page.wait_for_function(
            "() => getComputedStyle(document.documentElement).fontSize === '34px'"
        )
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("language", ["sv", "en"])
def test_capture_without_javascript_keeps_native_inputs_and_retry_help(
    page, live_server, user, image_upload, language
):
    context = page.context.browser.new_context(
        java_script_enabled=False, viewport={"width": 390, "height": 844}
    )
    try:
        context.add_cookies(
            [{"name": "django_language", "value": language, "url": live_server.url}]
        )
        plain = context.new_page()
        plain.goto(live_server.url + "/accounts/login/")
        plain.locator("#id_username").fill(user.username)
        plain.locator("#id_password").fill("Synthetic-password-42")
        plain.locator('form:has(#id_username) button[type="submit"]').click()
        plain.goto(live_server.url + "/capture/")
        expect(plain.locator("#id_camera")).to_be_visible()
        expect(plain.locator("#id_library")).to_be_visible()
        key = plain.locator("#id_submission_key").input_value()
        # An empty observation gets a server error and the same submission key.
        plain.locator("#save-entry").click()
        expect(plain.locator("#capture-errors")).not_to_be_empty()
        expect(plain.locator("#id_submission_key")).to_have_value(key)
        expect(plain.locator("#capture-form > p[role=alert]")).to_be_visible()
        plain.locator("#id_library").set_input_files(upload(image_upload()))
        plain.locator("#save-entry").click()
        expect(plain).to_have_url(re.compile(r"/entries/\d+/$"))
        expect(plain.locator(".large-photo")).to_have_count(1)
    finally:
        context.close()


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
    other.locator("#account-menu > summary").click()
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


def test_edit_photo_limit_counts_kept_photos_and_can_be_corrected(
    page, live_server, user, image_upload
):
    entry_url = open_entry_edit(
        page,
        live_server,
        user,
        [upload(image_upload(), "first.jpg"), upload(image_upload(), "second.jpg")],
    )
    page.locator("#id_photos_camera").set_input_files(upload(image_upload(), "camera.jpg"))
    page.locator("#id_photos").set_input_files(
        [upload(image_upload(), f"library-{number}.jpg") for number in range(9)]
    )
    expect(page.locator("[data-photo-limit-warning]")).to_be_visible()
    expect(page.locator("[data-photo-count]")).to_have_text("Valda bilder: 10 av 8")
    expect(page.locator("[data-photo-previews] figure")).to_have_count(10)
    page.locator("#entry-form button[type=submit]").click()
    expect(page.locator("#entry-errors")).to_contain_text("10")
    expect(page.locator("[data-photo-previews] figure")).to_have_count(10)
    for capacity, removal in enumerate(page.locator('input[name="remove_photos"]').all(), start=9):
        removal.check()
        expect(page.locator("[data-photo-count]")).to_have_text(f"Valda bilder: 10 av {capacity}")
    expect(page.locator("[data-photo-limit-warning]")).to_be_hidden()
    page.locator("#entry-form button[type=submit]").click()
    expect(page).to_have_url(entry_url)
    expect(page.locator(".large-photo")).to_have_count(10)


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
    other.locator("#account-menu > summary").click()
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


@pytest.mark.parametrize("photo_count", [0, 1, 3])
def test_quick_capture_saves_without_navigation_and_resets_only_after_success(
    page, live_server, user, image_upload, photo_count
):
    login(page, live_server, user)
    page.goto(live_server.url + "/")
    key = page.locator("#id_submission_key").input_value()
    if photo_count:
        page.locator("[data-photo-chooser] > summary").click()
        with page.expect_file_chooser() as picker:
            page.locator("[data-pick-photo=library]").click()
        picker.value.set_files(
            [upload(image_upload(), f"synthetic-{number}.jpg") for number in range(photo_count)]
        )
    if photo_count != 1:
        if photo_count == 0:
            initial_height = page.locator("#id_body").bounding_box()["height"]
            page.locator("#id_body").fill("Synthetic long note\n" * 8)
            assert page.locator("#id_body").bounding_box()["height"] > initial_height
        page.locator("#id_body").fill("Synthetic quick observation")
    page.locator("#save-entry").click()
    expect(page.locator("#quick-receipt")).to_contain_text("har sparats")
    expect(page.locator("#diary-results .diary-card")).to_have_count(1)
    expect(page).to_have_url(live_server.url + "/")
    expect(page.locator("#id_body")).to_have_value("")
    assert page.locator("#id_submission_key").input_value() != key
    assert page.locator("#id_library").evaluate("input => input.files.length") == 0
    expect(page.locator("#photo-previews figure")).to_have_count(0)
    expect(page.locator("#diary-results .diary-card h2")).to_be_focused()
    expect(page.locator("#diary-results .photo-grid img")).to_have_count(photo_count)
    page.reload()
    box = page.locator("#diary-results .diary-card").bounding_box()
    assert box["y"] < 844
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_quick_capture_lost_response_keeps_draft_and_retries_without_duplicates(
    page, live_server, user, image_upload
):
    login(page, live_server, user)
    page.goto(live_server.url + "/")
    page.locator("#id_body").fill("Retry a committed quick capture")
    page.locator("#id_library").set_input_files(upload(image_upload()))
    key = page.locator("#id_submission_key").input_value()

    def lose_response(route):
        if route.request.method == "POST":
            route.fetch()
            route.abort()
        else:
            route.continue_()

    page.route("**/capture/", lose_response)
    page.locator("#save-entry").click()
    expect(page.locator("#capture-errors")).not_to_be_empty()
    expect(page.locator("#id_body")).to_have_value("Retry a committed quick capture")
    expect(page.locator("#id_submission_key")).to_have_value(key)
    assert page.locator("#id_library").evaluate("input => input.files.length") == 1
    page.unroute("**/capture/")
    page.locator("#save-entry").click()
    expect(page.locator("#quick-receipt")).to_contain_text("redan")
    expect(page.locator("#diary-results .diary-card")).to_have_count(1)
    expect(page.locator("#diary-results .photo-grid img")).to_have_count(1)


def test_diary_filters_chips_and_history_preserve_the_capture_draft(
    page, live_server, user, image_upload
):
    login(page, live_server, user)
    page.locator("#id_body").fill("Synthetic searchable tomato")
    page.locator("#save-entry").click()
    expect(page).to_have_url(re.compile(r"/entries/\d+/$"))
    page.goto(live_server.url + "/")
    page.locator("#id_body").fill("Unsubmitted draft")
    page.locator("#id_library").set_input_files(upload(image_upload()))
    key = page.locator("#id_submission_key").input_value()
    page.locator("#open-filters").click()
    expect(page.locator("#id_q")).to_be_focused()
    page.locator("#id_q").fill("tomato")
    expect(page).to_have_url(live_server.url + "/?q=tomato")
    expect(page.locator("#id_q")).to_be_focused()
    expect(page.locator(".filter-chip")).to_have_count(1)
    page.keyboard.press("Shift+Tab")
    expect(page.locator("[data-dialog-close]")).to_be_focused()
    for _ in range(14):
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.closest('#filter-dialog') !== null")
    page.keyboard.press("Escape")
    expect(page.locator("#open-filters")).to_be_focused()
    page.locator(".filter-chip").click()
    expect(page).to_have_url(live_server.url + "/")
    expect(page.locator(".filter-chip")).to_have_count(0)
    page.go_back()
    expect(page).to_have_url(live_server.url + "/?q=tomato")
    expect(page.locator(".filter-chip")).to_have_count(1)
    expect(page.locator("#id_q")).to_have_value("tomato")
    expect(page.locator("#id_body")).to_have_value("Unsubmitted draft")
    expect(page.locator("#id_submission_key")).to_have_value(key)
    assert page.locator("#id_library").evaluate("input => input.files.length") == 1


def test_filter_failure_is_visible_in_the_drawer_and_retry_keeps_the_draft(page, live_server, user):
    login(page, live_server, user)
    page.goto(live_server.url + "/")
    page.locator("#id_body").fill("Keep filtering draft")
    page.locator("#open-filters").click()
    page.route("**/?q=*", lambda route: route.abort())
    page.locator("#id_q").fill("synthetic")
    expect(page.locator("#filter-drawer-status")).not_to_be_empty()
    expect(page.locator("#id_body")).to_have_value("Keep filtering draft")
    page.unroute("**/?q=*")
    page.locator("#id_q").press("Enter")
    expect(page).to_have_url(live_server.url + "/?q=synthetic")
    expect(page.locator("#filter-drawer-status")).to_be_empty()


def test_account_disclosure_and_language_change_keep_the_filtered_page(page, live_server, user):
    login(page, live_server, user)
    page.goto(live_server.url + "/?q=synthetic")
    trigger = page.locator("#account-menu > summary")
    trigger.click()
    expect(page.locator('#account-menu a[href="/operator/"]')).to_have_count(0)
    page.keyboard.press("Escape")
    expect(trigger).to_be_focused()
    trigger.click()
    page.locator("#language").select_option("en")
    expect(page.locator("html")).to_have_attribute("lang", "en")
    expect(page).to_have_url(live_server.url + "/?q=synthetic")
    expect(page.locator(".site-header nav [aria-current=page]")).to_have_text("Diary")


def test_quick_add_and_filters_work_without_javascript(page, live_server, user, image_upload):
    context = page.context.browser.new_context(
        java_script_enabled=False, viewport={"width": 390, "height": 844}
    )
    try:
        plain = context.new_page()
        plain.goto(live_server.url + "/accounts/login/")
        plain.locator("#id_username").fill(user.username)
        plain.locator("#id_password").fill("Synthetic-password-42")
        plain.locator('form:has(#id_username) button[type="submit"]').click()
        expect(plain.locator("#id_library")).to_be_visible()
        plain.locator("#id_body").fill("Synthetic no-JavaScript quick note")
        plain.locator("#id_library").set_input_files(upload(image_upload()))
        plain.locator("#save-entry").click()
        expect(plain).to_have_url(re.compile(r"/#entry-\d+$"))
        expect(plain.locator(".diary-card")).to_have_count(1)
        expect(plain.locator(".diary-card .photo-grid img")).to_have_count(1)
        plain.locator("#diary-filters > summary").click()
        plain.locator("#id_q").fill("does not match")
        plain.locator("[data-filter-submit]").click()
        expect(plain.locator(".diary-card")).to_have_count(0)
        plain.locator(".filter-chip").click()
        expect(plain.locator(".diary-card")).to_have_count(1)
        plain.locator("#account-menu > summary").click()
        expect(plain.locator("#account-menu .language-form button")).to_be_visible()
    finally:
        context.close()
