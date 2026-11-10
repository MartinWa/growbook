"""Real Chromium/form checks; not evidence of device-native photo pickers."""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from io import BytesIO
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

import pytest
from django.db import close_old_connections, connection
from PIL import Image
from playwright.sync_api import expect, sync_playwright

from diary.services import create_entry
from garden.models import Crop
from garden.services import create_crop, delete_crop

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def page(live_server, settings, user, browser_endpoint):
    settings.WHITENOISE_USE_FINDERS = True
    with (
        sync_playwright() as playwright,
        playwright.chromium.connect(browser_endpoint, timeout=20000) as browser,
        browser.new_context(viewport={"width": 390, "height": 844}) as context,
    ):
        page = context.new_page()
        page.set_default_timeout(8000)
        yield page


def open_filters(page):
    disclosure = page.locator("[data-filter-disclosure]")
    summary = disclosure.locator(":scope > summary")
    if disclosure.get_attribute("open") is None:
        summary.click()
    summary.press("Tab")


def login(page, live_server, user):
    page.goto(live_server.url + "/accounts/login/")
    page.locator("#id_username").fill(user.username)
    page.locator("#id_password").fill("Synthetic-password-42")
    page.locator('button[type="submit"]').filter(has_text="Logga in").click()
    expect(page).to_have_url(live_server.url + "/")
    page.goto(live_server.url + "/")
    page.wait_for_function("window.htmx !== undefined")


def upload(file, name="synthetic.jpg"):
    return {"name": name, "mimeType": "image/jpeg", "buffer": file.read()}


def open_entry_edit(page, live_server, user, photos=()):
    login(page, live_server, user)
    page.locator("#id_body").fill("Synthetic saved observation")
    if photos:
        page.locator("#id_library").set_input_files(photos)
    page.locator("#save-entry").click()
    page.locator("#quick-receipt a").click()
    expect(page).to_have_url(re.compile(r"/entries/\d+/$"))
    entry_url = page.url
    page.goto(entry_url + "edit/")
    expect(page.locator("#entry-form")).to_be_visible()
    return entry_url


def test_diary_has_one_composer_without_more_options(page, live_server, user):
    login(page, live_server, user)
    expect(page.locator("#capture-form")).to_be_visible()
    expect(page.locator("[data-more-options]")).to_have_count(0)
    expect(page.locator(".photo-limits")).to_have_count(0)
    expect(page.locator("[data-photo-limit-warning]")).to_be_hidden()
    page.goto(live_server.url + "/capture/")
    expect(page).to_have_url(live_server.url + "/#id_body")
    expect(page.locator("#capture-form")).to_be_visible()


@pytest.mark.parametrize("path", ["/?q=synthetic", "/garden/plantings/?year=2026"])
@pytest.mark.parametrize("javascript", [False, True])
def test_filters_collapse_above_full_width_content_and_keep_choices_on_resize(
    page, live_server, user, path, javascript
):
    login(page, live_server, user)
    page.locator("#id_body").fill("Synthetic layout observation")
    page.locator("#save-entry").click()
    expect(page.locator("#diary-results .diary-card")).to_have_count(1)
    context = page.context.browser.new_context(
        java_script_enabled=javascript,
        storage_state=page.context.storage_state(),
        viewport={"width": 1280, "height": 1000},
    )
    try:
        view = context.new_page()
        view.goto(live_server.url + path)
        disclosure = view.locator("[data-filter-disclosure]")
        summary = disclosure.locator(":scope > summary")
        field = view.locator("#id_q" if path.startswith("/?") else "#id_year")
        content = view.locator(".diary-card" if path.startswith("/?") else "#planting-results")
        for width in (1280, 900, 899, 390, 320):
            view.set_viewport_size({"width": width, "height": 1000})
            expect(field).to_be_hidden()
            expect(view.locator(".filter-chip")).to_be_visible()
            summary.focus()
            summary.press("Enter")
            expect(field).to_be_visible()
            summary.press("Tab")
            expect(field).to_be_focused()
            panel_box, content_box = (
                view.locator("#filter-panel").bounding_box(),
                content.bounding_box(),
            )
            assert panel_box["y"] + panel_box["height"] <= content_box["y"]
            assert abs(panel_box["width"] - content_box["width"]) < 2
            assert abs(panel_box["x"] - content_box["x"]) < 2
            clear = view.locator("#filter-panel .form-actions a")
            expect(clear).to_have_class(re.compile(r"\bbtn\b"))
            clear_box = clear.bounding_box()
            help_box = view.locator(".filter-help > summary").bounding_box()
            actions_box = view.locator(".filter-actions").bounding_box()
            assert abs(clear_box["y"] - help_box["y"]) < 2
            assert (
                abs(help_box["x"] + help_box["width"] - actions_box["x"] - actions_box["width"]) < 2
            )
            assert view.evaluate("document.documentElement.scrollWidth <= innerWidth")
            # Crossing the former breakpoint keeps the same open disclosure and input.
            view.set_viewport_size({"width": 390 if width >= 900 else 1280, "height": 1000})
            expect(field).to_be_visible()
            expect(field).to_have_value("synthetic" if path.startswith("/?") else "2026")
            if javascript:
                field.press("Escape")
                expect(summary).to_be_focused()
            else:
                summary.press("Space")
            expect(field).to_be_hidden()
    finally:
        context.close()


@pytest.mark.parametrize("language", ["sv", "en"])
def test_release_footer_links_update_indicator_and_no_javascript(
    page, live_server, user, settings, language, tmp_path
):
    settings.APP_VERSION = "0.1.9"
    settings.UPDATE_CHECK_ENABLED = True
    page.context.add_cookies(
        [{"name": "django_language", "value": language, "url": live_server.url}]
    )
    checks, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def status(route):
        checks.append(route.request.url)
        route.fulfill(json={"update_available": True})

    page.route("**/release-status/", status)
    page.goto(live_server.url + "/accounts/login/")
    expect(page.locator("[data-update-indicator]")).to_be_hidden()
    assert checks == []
    page.locator("#id_username").fill(user.username)
    page.locator("#id_password").fill("Synthetic-password-42")
    page.locator('.login-panel button[type="submit"]').click()
    expect(page).to_have_url(live_server.url + "/")
    footer = page.locator(".app-footer")
    version = footer.locator("[data-release-link]")
    indicator = footer.locator("[data-update-indicator]")
    label = "Uppdatering tillgänglig" if language == "sv" else "Update available"
    expect(indicator).to_be_visible()
    expect(indicator).to_have_attribute("title", label)
    expect(version).to_have_accessible_name("0.1.9 " + label)
    expect(version).to_have_attribute("href", "https://github.com/MartinWa/growbook/releases")
    github = footer.get_by_role("link", name="GitHub", exact=True)
    expect(github).to_have_attribute("href", "https://github.com/MartinWa/growbook")
    version.focus()
    page.keyboard.press("Tab")
    expect(github).to_be_focused()
    for width in (320, 1280):
        page.set_viewport_size({"width": width, "height": 844})
        footer.scroll_into_view_if_needed()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert footer.evaluate("element => getComputedStyle(element).position") == "static"
    footer.screenshot(path=str(tmp_path / "release-footer.png"))
    assert errors == []

    plain_context = page.context.browser.new_context(
        java_script_enabled=False, viewport={"width": 320, "height": 844}
    )
    try:
        plain_context.add_cookies(page.context.cookies())
        plain = plain_context.new_page()
        plain.goto(live_server.url + "/")
        expect(plain.locator("[data-release-link]")).to_have_text("0.1.9", use_inner_text=True)
        expect(plain.locator("[data-update-indicator]")).to_be_hidden()
        expect(plain.locator(".app-footer").get_by_role("link", name="GitHub")).to_be_visible()
    finally:
        plain_context.close()


@pytest.mark.parametrize("result", [False, None, "unavailable"])
def test_release_check_without_known_update_keeps_footer_quiet(
    page, live_server, user, settings, result
):
    settings.APP_VERSION = "0.1.9"
    settings.UPDATE_CHECK_ENABLED = True
    page.route(
        "**/release-status/",
        lambda route: (
            route.fulfill(status=503, body="Unavailable")
            if result == "unavailable"
            else route.fulfill(json={"update_available": result})
        ),
    )
    login(page, live_server, user)
    with page.expect_response("**/release-status/"):
        page.goto(live_server.url + "/")
    expect(page.locator("[data-release-link]")).to_have_text("0.1.9", use_inner_text=True)
    expect(page.locator("[data-update-indicator]")).to_be_hidden()


@pytest.mark.parametrize("language", ["sv", "en"])
def test_development_footer_is_readable_and_keeps_exact_version_in_tooltip(
    page, live_server, user, settings, language, tmp_path
):
    settings.APP_VERSION = "0.1.8-2-gabcdef0-dirty"
    settings.UPDATE_CHECK_ENABLED = True
    checks = []
    page.on(
        "request",
        lambda request: (
            checks.append(request.url) if request.url.endswith("/release-status/") else None
        ),
    )
    login(page, live_server, user)
    page.context.add_cookies(
        [{"name": "django_language", "value": language, "url": live_server.url}]
    )
    page.goto(live_server.url + "/")
    footer = page.locator(".app-footer")
    expect(footer).to_have_text("GrowBook 0.1.8 · GitHub", use_inner_text=True)
    version = footer.locator("[data-release-link]")
    expect(version).to_have_accessible_name("0.1.8")
    expect(version).to_have_attribute("title", "0.1.8-2-gabcdef0-dirty")
    expect(version).not_to_have_attribute("data-release-status-url", re.compile(".+"))
    expect(footer.locator("[data-update-indicator]")).to_be_hidden()
    for width in (320, 1280):
        page.set_viewport_size({"width": width, "height": 844})
        footer.scroll_into_view_if_needed()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    footer.screenshot(path=str(tmp_path / "development-footer.png"))
    assert checks == []


def test_apple_hdr_library_photo_saves_from_the_edit_form(
    page, live_server, user, apple_hdr_upload
):
    entry_url = open_entry_edit(page, live_server, user)
    page.locator("input[type=file]:not([capture])").set_input_files(upload(apple_hdr_upload()))
    expect(page.locator("[data-photo-previews] figure")).to_have_count(1)
    page.locator('#entry-form button[type="submit"]').click()
    expect(page).to_have_url(entry_url)
    image = page.locator(".large-photo img")
    image.evaluate("image => image.decode()")
    assert image.evaluate("image => [image.naturalWidth, image.naturalHeight]") == [80, 60]


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
    page.locator("#quick-receipt a").click()
    expect(page).to_have_url(re.compile(r"/entries/\d+/$"))
    entry_url = page.url
    photo_link = page.locator(".large-photo > a")
    viewer_path = photo_link.get_attribute("href")
    expect(photo_link).to_have_attribute("href", re.compile(r"/photos/\d+/$"))
    photo_link.click()
    expect(page).to_have_url(live_server.url + viewer_path)
    options = page.locator(".photo-options > summary")
    expect(options).to_have_text("Bildalternativ")
    original = page.get_by_role("link", name="Ladda ner original", exact=True)
    expect(original).to_be_hidden()
    options.focus()
    page.keyboard.press("Enter")
    expect(original).to_be_visible()
    page.keyboard.press("Tab")
    expect(original).to_be_focused()
    page.keyboard.press("Escape")
    expect(original).to_be_hidden()
    expect(options).to_be_focused()
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
    page.locator(".photo-options > summary").click()
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
    expect(page.locator("#photo-count")).to_have_text("Valda bilder: 3")
    page.locator("#id_body").fill("Synthetic browser observation — Ärtor")
    page.locator("#save-entry").click()
    page.locator("#quick-receipt a").click()
    expect(page).to_have_url(re.compile(r"/entries/\d+/$"))
    expect(page.locator(".large-photo")).to_have_count(3)
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
    page.locator("#quick-receipt a").click()
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
    page.locator("#quick-receipt a").click()
    expect(page).to_have_url(re.compile(r"/entries/\d+/$"))
    expect(page.locator(".entry-body")).to_have_text("Keep this draft")
    expect(page.locator(".large-photo")).to_have_count(1)


@pytest.mark.parametrize("language", ["sv", "en"])
def test_capture_foundation_reflows_and_supports_keyboard(
    page, live_server, user, image_upload, language
):
    # Enable interception before keyboard activation; a last-moment subscription can race.
    page.on("filechooser", lambda chooser: None)
    login(page, live_server, user)
    page.context.add_cookies(
        [{"name": "django_language", "value": language, "url": live_server.url}]
    )
    page.reload()
    expect(page.locator("html")).to_have_attribute("lang", language)
    for width in (320, 390, 1280):
        page.set_viewport_size({"width": width, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        for selector in (
            "#save-entry",
            "[data-tag-trigger]",
            "[data-photo-chooser]",
            "#id_date",
            "#id_body",
        ):
            box = page.locator(selector).bounding_box()
            assert box["width"] >= 44 and box["height"] >= 44
        actions = page.locator(".capture-actions").bounding_box()
        save = page.locator("#save-entry").bounding_box()
        assert abs(save["x"] + save["width"] - actions["x"] - actions["width"]) < 1
    tag = page.locator("[data-tag-trigger]")
    photos = page.locator("[data-photo-chooser]")
    assert tag.bounding_box()["y"] == photos.bounding_box()["y"] == save["y"]
    expect(page.locator(".about-label")).to_have_count(0)
    page.locator("#id_body").focus()
    page.keyboard.press("Tab")
    expect(tag).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.locator("[data-tag-search]")).to_be_focused()
    page.keyboard.press("Escape")
    expect(tag).to_be_focused()
    page.keyboard.press("Tab")
    expect(photos).to_be_focused()
    with page.expect_file_chooser() as selection:
        page.keyboard.press("Enter")
    assert selection.value.element.get_attribute("id") == "id_library"
    assert selection.value.element.get_attribute("capture") is None
    page.keyboard.press("Tab")
    expect(page.locator("#save-entry")).to_be_focused()
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
    expected = "Valda bilder: 1" if language == "sv" else "Photos selected: 1"
    expect(page.locator("[data-photo-count]")).to_have_text(expected)
    removal = page.locator("[data-photo-previews] button").bounding_box()
    assert removal["width"] >= 44 and removal["height"] >= 44
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
        expect(plain).to_have_url(re.compile(r"/#entry-\d+$"))
        plain.locator(".diary-card .entry-heading a").click()
        expect(plain.locator(".large-photo")).to_have_count(1)
    finally:
        context.close()


@pytest.mark.parametrize("limit", ["count", "file", "total"])
def test_photo_limits_appear_only_after_exceeding_a_selection_limit(
    page, live_server, user, image_upload, settings, limit
):
    image = image_upload().read()
    settings.PHOTO_MAX_COUNT = 1 if limit == "count" else 10
    settings.PHOTO_MAX_FILE_BYTES = len(image) - 1 if limit == "file" else len(image) * 2
    settings.PHOTO_MAX_TOTAL_BYTES = len(image) * 3 // 2 if limit == "total" else len(image) * 10
    login(page, live_server, user)
    warning = page.locator("[data-photo-limit-warning]")
    expect(warning).to_be_hidden()
    expect(page.locator(".photo-limit-line, .photo-limits")).to_have_count(0)
    file = {"name": "synthetic.jpg", "mimeType": "image/jpeg", "buffer": image}
    page.locator("#id_library").set_input_files([file] if limit == "file" else [file, file])
    expect(warning).to_be_visible()
    expect(warning).to_contain_text("JPEG, PNG, WebP")
    page.locator("[data-photo-previews] button").first.click()
    expect(warning).to_be_hidden()
    if limit != "file":
        expect(page.locator("[data-photo-count]")).to_have_text("Valda bilder: 1")


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
    page.locator("#quick-receipt a").click()
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
    page.locator("#quick-receipt a").click()
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
    page.locator("#quick-receipt a").click()
    expect(page).to_have_url(re.compile(r"/entries/\d+/$"))
    entry_url = page.url

    page.locator('a[href$="/edit/"]').first.click()
    expect(page.locator("#entry-form")).to_be_visible()
    page.locator("#id_body").fill("Browser edited observation")
    page.get_by_role("button", name="Lägg till taggar").click()
    page.get_by_role("searchbox", name="Sök taggar").fill("Synthetic browser crop")
    page.get_by_role("checkbox", name="Synthetic browser crop").check()
    page.get_by_role("button", name="Klart", exact=True).click()
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
    page.locator("button.btn-error").click()
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
    expect(page.locator("[data-photo-count]")).to_have_text("Valda bilder: 10")
    expect(page.locator("[data-photo-previews] figure")).to_have_count(10)
    page.locator("#entry-form button[type=submit]").click()
    expect(page.locator("#entry-errors")).to_contain_text("10")
    expect(page.locator("[data-photo-previews] figure")).to_have_count(10)
    for capacity, removal in enumerate(page.locator('input[name="remove_photos"]').all(), start=9):
        removal.check()
        expect(page.locator("[data-photo-count]")).to_have_text("Valda bilder: 10")
        if capacity < 10:
            expect(page.locator("[data-photo-limit-warning]")).to_be_visible()
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
    page.locator("#id_title").fill("Retry the edited heading")
    page.locator("#id_body").fill("Retry the edited observation")
    page.locator("#id_photos").set_input_files(upload(image_upload()))
    version = page.locator("#id_expected_updated_at").input_value()
    key = page.locator("#id_submission_key").input_value()
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
    expect(page.locator("[data-tag-trigger]")).to_be_disabled()
    expect(page.locator("#id_title")).not_to_be_editable()
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
    expect(page.locator("#id_title")).to_be_editable()
    expect(page.locator("#id_title")).to_have_value("Retry the edited heading")
    expect(page.locator("#id_body")).to_be_editable()
    expect(page.locator("#id_body")).to_have_value("Retry the edited observation")
    expect(page.locator("#id_expected_updated_at")).to_have_value(version)
    expect(page.locator("#id_submission_key")).to_have_value(key)
    assert page.locator("#id_photos").evaluate("input => input.files.length") == 1

    page.unroute(entry_url + "edit/")
    save.click()
    expect(page).to_have_url(entry_url)
    expect(page.locator(".entry-title")).to_have_text("Retry the edited heading")
    expect(page.locator(".entry-body")).to_have_text("Retry the edited observation")
    expect(page.locator(".large-photo")).to_have_count(1)


@pytest.mark.parametrize("photo_count", [0, 1, 3])
def test_edit_lost_response_retries_committed_save_without_reapplying_photos(
    page, live_server, user, image_upload, photo_count
):
    entry_url = open_entry_edit(page, live_server, user, photos=[upload(image_upload())])
    edit_url = entry_url + "edit/"
    key = page.locator("#id_submission_key").input_value()
    version = page.locator("#id_expected_updated_at").input_value()
    page.locator("#id_title").fill("Heading after a lost response")
    page.locator('input[name="remove_photos"]').check()
    page.locator("#id_body").fill("" if photo_count else "Retry after a lost response")
    if photo_count:
        page.locator("#id_photos").set_input_files(
            [upload(image_upload(), f"synthetic-{number}.jpg") for number in range(photo_count)]
        )
    saved_responses = []

    def lose_response(route):
        if route.request.method == "POST":
            response = route.fetch()
            saved_responses.append(response.status)
            route.abort()
        else:
            route.continue_()

    page.route(edit_url, lose_response)
    page.locator("#entry-form button[type=submit]").click()
    expect(page.locator("#entry-errors")).not_to_be_empty()
    expect(page.locator("#entry-form button[type=submit]")).to_be_enabled()
    assert saved_responses == [204]
    expect(page.locator("#id_submission_key")).to_have_value(key)
    expect(page.locator("#id_expected_updated_at")).to_have_value(version)
    expect(page.locator("#id_title")).to_have_value("Heading after a lost response")
    expect(page.locator('input[name="remove_photos"]')).to_be_checked()
    assert page.locator("#id_photos").evaluate("input => input.files.length") == photo_count

    page.unroute(edit_url)
    with page.expect_response(
        lambda response: response.url == edit_url and response.request.method == "POST"
    ) as replayed:
        page.locator("#entry-form button[type=submit]").click()
    assert replayed.value.status == 204
    expect(page).to_have_url(entry_url)
    expect(page.locator(".entry-title")).to_have_text("Heading after a lost response")
    expect(page.locator(".large-photo")).to_have_count(photo_count)


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
    page.locator("#id_title").fill("Synthetic quick heading")
    if photo_count:
        with page.expect_file_chooser() as picker:
            page.locator("[data-photo-chooser]").click()
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
    expect(page.locator("#id_title")).to_have_value("")
    expect(page.locator("#diary-results .entry-title")).to_have_text("Synthetic quick heading")
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
    page.locator("#id_title").fill("Retry a committed heading")
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
    expect(page.locator("#id_title")).to_have_value("Retry a committed heading")
    expect(page.locator("#id_body")).to_have_value("Retry a committed quick capture")
    expect(page.locator("#id_submission_key")).to_have_value(key)
    assert page.locator("#id_library").evaluate("input => input.files.length") == 1
    page.unroute("**/capture/")
    page.locator("#save-entry").click()
    expect(page.locator("#quick-receipt")).to_contain_text("redan")
    expect(page.locator("#diary-results .diary-card")).to_have_count(1)
    expect(page.locator("#diary-results .entry-title")).to_have_text("Retry a committed heading")
    expect(page.locator("#diary-results .photo-grid img")).to_have_count(1)


@pytest.mark.parametrize("failure", [None, 503, "network"])
def test_slow_capture_drops_repeated_submits_and_keeps_controls_locked(
    page, live_server, user, image_upload, failure
):
    login(page, live_server, user)
    page.set_viewport_size({"width": 1280, "height": 844})
    page.locator("#id_body").fill("Synthetic slow save")
    page.locator("#id_library").set_input_files(upload(image_upload()))
    key = page.locator("#id_submission_key").input_value()
    pending = []
    page.route("**/capture/", lambda route: pending.append(route))
    page.locator("#save-entry").click()
    expect(page.locator("#capture-form")).to_have_attribute("aria-busy", "true")
    expect(page.locator("[data-photo-chooser]")).to_be_disabled()
    expect(page.locator("#photo-previews button")).to_be_disabled()
    expect(page.locator("[data-tag-trigger]")).to_be_disabled()
    # Exercise the submit event path as well as the disabled button.
    page.locator("#capture-form").evaluate(
        "form => { form.requestSubmit(); form.requestSubmit(); }"
    )
    assert len(pending) == 1
    # Results can change independently without unlocking the payload being saved.
    open_filters(page)
    page.locator("#id_q").fill("slow")
    page.locator("#id_q").press("Enter")
    expect(page).to_have_url(live_server.url + "/?q=slow")
    expect(page.locator("#save-entry")).to_be_disabled()
    expect(page.locator("[data-photo-chooser]")).to_be_disabled()
    expect(page.locator("#id_library")).to_be_disabled()
    expect(page.locator("#id_body")).not_to_be_editable()
    if failure:
        if failure == "network":
            pending[0].abort()
        else:
            pending[0].fulfill(
                status=failure,
                content_type="text/html",
                body='<div class="error-summary">Synthetic temporary failure</div>',
            )
        expect(page.locator("#capture-errors")).not_to_be_empty()
        expect(page.locator("#save-entry")).to_be_enabled()
        expect(page.locator("#id_body")).to_have_value("Synthetic slow save")
        expect(page.locator("#id_submission_key")).to_have_value(key)
        assert page.locator("#id_library").evaluate("input => input.files.length") == 1
        assert len(pending) == 1
        page.unroute("**/capture/")
        page.locator("#save-entry").click()
    else:
        pending[0].continue_()
    expect(page.locator("#quick-receipt")).to_contain_text("har sparats")
    expect(page.locator("#save-entry")).to_be_enabled()
    expect(page.locator("#capture-errors")).to_be_empty()
    expect(page.locator("#diary-results .diary-card")).to_have_count(1)
    expect(page.locator("#diary-results .photo-grid img")).to_have_count(1)
    expect(page.locator('input[name="filter_query"]')).to_have_value("q=slow")
    assert len(pending) == 1
    page.unroute("**/capture/")


def test_superseded_filter_link_keeps_focus_on_the_new_search_and_preserves_draft(
    page, live_server, user, image_upload
):
    login(page, live_server, user)
    page.set_viewport_size({"width": 1280, "height": 844})
    open_filters(page)
    page.locator("#id_q").fill("first")
    page.locator("#id_q").press("Enter")
    expect(page).to_have_url(live_server.url + "/?q=first")
    page.locator("#id_body").fill("Synthetic unsubmitted draft")
    page.locator("#id_library").set_input_files(upload(image_upload()))
    key = page.locator("#id_submission_key").input_value()
    pending = []
    page.route(live_server.url + "/", lambda route: pending.append(route))
    page.locator(".filter-chip").click()
    expect(page.locator("#diary-filter-form")).to_have_attribute("aria-busy", "true")
    assert len(pending) == 1
    page.locator("#id_q").fill("second")
    page.locator("#id_q").press("Enter")
    expect(page).to_have_url(live_server.url + "/?q=second")
    expect(page.locator("#id_q")).to_be_focused()
    expect(page.locator(".filter-chip")).to_contain_text("second")
    expect(page.locator("#filter-status")).to_be_empty()
    expect(page.locator("#id_body")).to_have_value("Synthetic unsubmitted draft")
    expect(page.locator("#id_submission_key")).to_have_value(key)
    assert page.locator("#id_library").evaluate("input => input.files.length") == 1
    pending[0].abort()
    page.unroute(live_server.url + "/")
    page.go_back()
    expect(page.locator(".filter-chip")).to_contain_text("first")
    expect(page.locator("#id_q")).to_have_value("first")
    expect(page.locator('input[name="filter_query"]')).to_have_value("q=first")


@pytest.mark.parametrize("late_failure", [False, True])
def test_back_during_slow_filter_body_ignores_the_old_completion(
    page, live_server, user, image_upload, late_failure
):
    login(page, live_server, user)
    page.set_viewport_size({"width": 1280, "height": 844})
    open_filters(page)
    page.locator("#id_q").fill("first")
    page.locator("#id_q").press("Enter")
    expect(page).to_have_url(live_server.url + "/?q=first")
    page.locator("#id_body").fill("Synthetic history draft")
    page.locator("#id_library").set_input_files(upload(image_upload()))
    key = page.locator("#id_submission_key").input_value()
    page.evaluate(
        """lateFailure => {
            const originalFetch = window.fetch.bind(window);
            window.slowFilter = { held: false, completed: false };
            window.fetch = async (url, options) => {
                const response = await originalFetch(url, options);
                if (new URL(url, location.href).searchParams.get('q') === 'second') {
                    const body = await response.text();
                    response.text = () => new Promise((resolve, reject) => {
                        window.releaseSlowFilter = () => {
                            if (lateFailure) reject(new Error('Synthetic late body failure'));
                            else resolve(body);
                        };
                        window.slowFilter.held = true;
                    }).finally(() => { window.slowFilter.completed = true; });
                }
                return response;
            };
        }""",
        late_failure,
    )
    page.locator("#id_q").fill("second")
    page.locator("#id_q").press("Enter")
    page.wait_for_function("() => window.slowFilter.held")
    page.go_back()
    expect(page).to_have_url(live_server.url + "/")
    expect(page.locator("#diary-filter-form")).not_to_have_attribute("aria-busy", "true")
    page.evaluate("window.releaseSlowFilter()")
    page.wait_for_function("() => window.slowFilter.completed")
    expect(page.locator("#id_q")).to_have_value("")
    expect(page.locator(".filter-chip")).to_have_count(0)
    expect(page.locator("#filter-status")).to_be_empty()
    expect(page.locator('input[name="filter_query"]')).to_have_value("")
    expect(page.locator("#id_body")).to_have_value("Synthetic history draft")
    expect(page.locator("#id_submission_key")).to_have_value(key)
    assert page.locator("#id_library").evaluate("input => input.files.length") == 1
    expect(page).to_have_url(live_server.url + "/")
    page.go_forward()
    expect(page).to_have_url(live_server.url + "/?q=first")
    expect(page.locator(".filter-chip")).to_contain_text("first")


def test_session_expires_during_filter_request_and_new_tab_login_preserves_the_draft(
    page, live_server, user, image_upload
):
    login(page, live_server, user)
    page.locator("#id_body").fill("Synthetic recovered filter draft")
    page.locator("#id_library").set_input_files(upload(image_upload()))
    key = page.locator("#id_submission_key").input_value()
    pending = []
    page.route("**/?q=*", lambda route: pending.append(route))
    open_filters(page)
    page.locator("#id_q").fill("recovered")
    page.locator("#id_q").press("Enter")
    expect(page.locator("#diary-filter-form")).to_have_attribute("aria-busy", "true")
    assert len(pending) == 1
    other = page.context.new_page()
    other.goto(live_server.url + "/")
    other.locator("#account-menu > summary").click()
    other.get_by_role("button", name="Logga ut", exact=True).click()
    with page.expect_response(lambda response: "?q=recovered" in response.url) as expired:
        pending[0].continue_()
    assert expired.value.status == 401
    expect(page.locator("#filter-status")).to_contain_text("session")
    expect(page.locator("#id_q")).to_be_focused()
    expect(page.locator("#diary-filter-form")).not_to_have_attribute("aria-busy", "true")
    expect(page).to_have_url(live_server.url + "/")
    page.unroute("**/?q=*")
    other.locator("#id_username").fill(user.username)
    other.locator("#id_password").fill("Synthetic-password-42")
    other.get_by_role("button", name="Logga in", exact=True).click()
    expect(other).to_have_url(live_server.url + "/")
    page.locator("#id_q").press("Enter")
    expect(page).to_have_url(live_server.url + "/?q=recovered")
    expect(page.locator("#filter-status")).to_be_empty()
    expect(page.locator("#id_submission_key")).to_have_value(key)
    expect(page.locator("#id_body")).to_have_value("Synthetic recovered filter draft")
    assert page.locator("#id_library").evaluate("input => input.files.length") == 1
    page.locator("[data-filter-disclosure] > summary").click()
    page.locator("#save-entry").click()
    expect(page.locator("#diary-results .diary-card")).to_have_count(1)
    expect(page.locator("#diary-results .photo-grid img")).to_have_count(1)
    other.close()


@pytest.mark.parametrize("refresh_fails", [False, True])
def test_quick_capture_from_later_page_preserves_filters_and_refreshes_first_page(
    page, live_server, user, image_upload, refresh_fails
):
    def seed_entries():
        close_old_connections()
        try:
            actor = type(user).objects.get(pk=user.pk)
            for number in range(21):
                create_entry(
                    author=actor,
                    date=date(2026, 1, 1),
                    body=f"Synthetic pagination note {number}",
                    uploads=[],
                    submission_key=uuid4(),
                )
            for entry_date, body in (
                (date(2025, 1, 1), "Synthetic pagination note from another year"),
                (date(2026, 1, 1), "Synthetic unrelated observation"),
            ):
                create_entry(
                    author=actor, date=entry_date, body=body, uploads=[], submission_key=uuid4()
                )
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(seed_entries).result(timeout=20)
    login(page, live_server, user)
    filters = "q=pagination&year=2026"
    first_url = live_server.url + "/?" + filters
    later_url = first_url + "&page=2"
    page.goto(later_url)
    expect(page.locator("#diary-result-count")).to_contain_text("21")
    expect(page.locator("#diary-results .diary-card")).to_have_count(1)
    page.locator("#id_date").fill("2026-05-09")
    page.locator("#id_body").fill("Synthetic newest pagination note")
    page.locator("#id_library").set_input_files(upload(image_upload()))
    if refresh_fails:
        page.route(first_url, lambda route: route.abort())
    page.locator("#save-entry").click()
    saved = page.locator("#quick-receipt [data-capture-saved]")
    expect(saved).to_be_attached()
    entry_id = saved.get_attribute("data-capture-saved")
    if refresh_fails:
        expect(page.locator("#filter-status")).not_to_be_empty()
        expect(page).to_have_url(later_url)
        expect(page.locator("#diary-result-count")).to_contain_text("21")
        expect(saved.locator("a")).to_be_focused()
        page.unroute(first_url)
        open_filters(page)
        page.locator("#id_q").press("Enter")
        page.keyboard.press("Escape")
        expect(page.locator("#filter-panel")).not_to_be_visible()
        expect(page.locator("#id_q")).to_have_value("pagination")
    expect(page).to_have_url(first_url)
    expect(page.locator("#diary-result-count")).to_contain_text("22")
    expect(page.locator("#diary-results .diary-card")).to_have_count(20)
    expect(page.locator(f"#entry-{entry_id} .entry-body")).to_have_text(
        "Synthetic newest pagination note"
    )
    expect(page.locator(f"#entry-{entry_id} .photo-grid img")).to_have_count(1)
    if not refresh_fails:
        expect(page.locator(f"#entry-{entry_id} h2")).to_be_focused()
    expect(page.locator('input[name="filter_query"]')).to_have_value(filters)
    expect(page.locator("#id_body")).to_have_value("")
    assert page.locator("#id_library").evaluate("input => input.files.length") == 0
    page.reload()
    expect(page.locator(f"#entry-{entry_id}")).to_be_attached()
    expect(page.locator("#diary-results .diary-card")).to_have_count(20)
    expect(page.locator("#diary-result-count")).to_contain_text("22")


@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("query, expected_count", [("missing-term", 0), ("synthetic", 1)])
def test_quick_capture_during_pending_search_keeps_selected_filters(
    page, live_server, user, width, query, expected_count
):
    login(page, live_server, user)
    page.set_viewport_size({"width": width, "height": 844})
    page.goto(live_server.url + "/")
    page.locator("#id_body").fill("Synthetic unrelated observation")
    page.evaluate(
        """query => {
            const originalFetch = window.fetch.bind(window);
            window.pendingSearch = { held: false, aborted: false };
            window.fetch = (url, options) => {
                if (!window.pendingSearch.held &&
                    new URL(url, location.href).searchParams.get("q") === query) {
                    window.pendingSearch.held = true;
                    return new Promise((resolve, reject) => {
                        options.signal.addEventListener("abort", () => {
                            window.pendingSearch.aborted = true;
                            reject(new DOMException("Aborted", "AbortError"));
                        });
                    });
                }
                return originalFetch(url, options);
            };
        }""",
        query,
    )
    open_filters(page)
    page.locator("#id_q").fill(query)
    page.locator("#id_q").press("Enter")
    page.wait_for_function("window.pendingSearch.held")
    expect(page.locator("#diary-filter-form")).to_have_attribute("aria-busy", "true")
    expect(page).to_have_url(live_server.url + "/")
    if width < 900:
        page.locator("[data-filter-disclosure] > summary").click()
    page.locator("#save-entry").click()
    saved = page.locator("#quick-receipt [data-capture-saved]")
    expect(saved).to_be_attached()
    entry_id = saved.get_attribute("data-capture-saved")
    expect(page).to_have_url(live_server.url + "/?" + urlencode({"q": query}))
    expect(page.locator("#id_q")).to_have_value(query)
    expect(page.locator("#diary-filter-form")).not_to_have_attribute("aria-busy", "true")
    expect(page.locator("#filter-status")).to_be_empty()
    expect(page.locator("#capture-errors")).to_be_empty()
    expect(page.locator("#diary-result-count")).to_have_text(re.compile(rf"^{expected_count} "))
    expect(page.locator("#diary-results .diary-card")).to_have_count(expected_count)
    expect(page.locator(".filter-chip")).to_contain_text(query)
    expect(page.locator('input[name="filter_query"]')).to_have_value(urlencode({"q": query}))
    assert page.evaluate("window.pendingSearch.aborted")
    page.reload()
    expect(page.locator("#diary-results .diary-card")).to_have_count(expected_count)
    page.goto(live_server.url + "/")
    expect(page.locator("#diary-results .diary-card")).to_have_count(1)
    expect(page.locator(f"#entry-{entry_id} .entry-body")).to_have_text(
        "Synthetic unrelated observation"
    )


@pytest.mark.parametrize("width", [390, 1280])
def test_stale_diary_filter_errors_identify_the_field_and_clear_on_retry(
    page, live_server, user, image_upload, width
):
    def change_crop(pk=None):
        close_old_connections()
        try:
            actor = type(user).objects.get(pk=user.pk)
            if pk is None:
                return create_crop(actor=actor, name="Synthetic temporary crop").record.pk
            crop = Crop.objects.get(pk=pk)
            delete_crop(actor=actor, pk=pk, expected_updated_at=crop.updated_at)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        crop_pk = executor.submit(change_crop).result(timeout=20)
        page.set_viewport_size({"width": width, "height": 844})
        login(page, live_server, user)
        page.goto(live_server.url + "/")
        expect(page.locator(f"#id_crop option[value='{crop_pk}']")).to_be_attached()
        executor.submit(change_crop, crop_pk).result(timeout=20)
    page.locator("#id_body").fill("Keep this draft while correcting filters")
    page.locator("#id_library").set_input_files(upload(image_upload()))
    key = page.locator("#id_submission_key").input_value()
    open_filters(page)
    page.locator("#id_crop").select_option(str(crop_pk))
    expect(page.locator("#id_crop")).to_have_attribute("aria-invalid", "true")
    expect(page.locator("#id_crop_errors")).not_to_be_empty()
    expect(page.locator('#diary-results [data-field-errors="crop"]')).not_to_be_empty()
    feedback = "#filter-status"
    expect(page.locator(feedback)).not_to_be_empty()
    if width < 900:
        page.keyboard.press("Escape")
    page.locator('#diary-results .error-summary a[href="#id_crop"]').click()
    expect(page.locator("#id_crop")).to_be_focused()
    page.locator("#id_crop").select_option("")
    expect(page.locator("#id_crop")).not_to_have_attribute("aria-invalid", "true")
    expect(page.locator("#id_crop_errors")).to_be_empty()
    expect(page.locator("#diary-results .error-summary")).to_have_count(0)
    expect(page.locator(feedback)).to_be_empty()
    expect(page.locator("#id_body")).to_have_value("Keep this draft while correcting filters")
    expect(page.locator("#id_submission_key")).to_have_value(key)
    assert page.locator("#id_library").evaluate("input => input.files.length") == 1


def test_diary_filters_chips_and_history_preserve_the_capture_draft(
    page, live_server, user, image_upload
):
    login(page, live_server, user)
    page.locator("#id_body").fill("Synthetic searchable tomato")
    page.locator("#save-entry").click()
    page.locator("#quick-receipt a").click()
    expect(page).to_have_url(re.compile(r"/entries/\d+/$"))
    page.goto(live_server.url + "/")
    page.locator("#id_body").fill("Unsubmitted draft")
    page.locator("#id_library").set_input_files(upload(image_upload()))
    key = page.locator("#id_submission_key").input_value()
    open_filters(page)
    expect(page.locator("#id_q")).to_be_focused()
    page.locator("#id_q").fill("tomato")
    expect(page).to_have_url(live_server.url + "/?q=tomato")
    expect(page.locator("#id_q")).to_be_focused()
    expect(page.locator(".filter-chip")).to_have_count(1)
    page.keyboard.press("Shift+Tab")
    expect(page.locator("[data-filter-disclosure] > summary")).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.locator("#id_q")).to_be_focused()
    # Filters are inline: keyboard users can move into the composer without closing them.
    page.locator(".filter-help > summary").focus()
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.closest('#capture-form') !== null")
    page.locator("#id_q").focus()
    page.keyboard.press("Escape")
    expect(page.locator("[data-filter-disclosure] > summary")).to_be_focused()
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


@pytest.mark.parametrize(
    "query", ["Synthetic garden observation phrase for wrapping " * 4, "W" * 150]
)
def test_filter_chips_wrap_inside_their_targets_and_remain_keyboard_operable(
    page, live_server, user, query
):
    login(page, live_server, user)
    for width in (320, 390, 1280):
        page.set_viewport_size({"width": width, "height": 844})
        page.goto(live_server.url + "/?" + urlencode({"q": query}))
        chip = page.locator(".filter-chip")
        expect(chip).to_have_count(1)
        assert chip.evaluate(
            """link => {
                const box = link.getBoundingClientRect();
                const range = document.createRange();
                range.selectNodeContents(link);
                const content = range.getBoundingClientRect();
                return box.height >= 44 && content.top >= box.top - 1 &&
                    content.bottom <= box.bottom + 1 && content.left >= box.left - 1 &&
                    content.right <= box.right + 1;
            }"""
        )
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    chip.focus()
    chip.press("Enter")
    expect(page).to_have_url(live_server.url + "/")
    expect(page.locator(".filter-chip")).to_have_count(0)


def test_filter_failure_is_visible_and_retry_keeps_the_draft(page, live_server, user):
    login(page, live_server, user)
    page.goto(live_server.url + "/")
    page.locator("#id_body").fill("Keep filtering draft")
    open_filters(page)
    page.route("**/?q=*", lambda route: route.abort())
    page.locator("#id_q").fill("synthetic")
    expect(page.locator("#filter-status")).not_to_be_empty()
    expect(page.locator("#id_body")).to_have_value("Keep filtering draft")
    page.unroute("**/?q=*")
    page.locator("[data-filter-retry]").click()
    expect(page).to_have_url(live_server.url + "/?q=synthetic")
    expect(page.locator("#filter-status")).to_be_empty()


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
    expect(page.locator("header nav [aria-current=page]")).to_have_text("Diary")


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
        plain.locator("#id_title").fill("Synthetic no-JavaScript heading")
        plain.locator("#id_body").fill("Synthetic no-JavaScript quick note")
        plain.locator("#id_library").set_input_files(upload(image_upload()))
        plain.locator("#save-entry").click()
        expect(plain).to_have_url(re.compile(r"/#entry-\d+$"))
        expect(plain.locator(".diary-card")).to_have_count(1)
        expect(plain.locator(".entry-title")).to_have_text("Synthetic no-JavaScript heading")
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


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("language", ["sv", "en"])
def test_optional_titles_are_editable_searchable_and_wrap(page, live_server, user, width, language):
    login(page, live_server, user)
    page.set_viewport_size({"width": width, "height": 1000})
    page.context.add_cookies(
        [{"name": "django_language", "value": language, "url": live_server.url}]
    )
    page.reload()
    label = "Title (optional)" if language == "en" else "Rubrik (valfritt)"
    field = page.get_by_label(label, exact=True)
    expect(field).to_be_visible()
    expect(field).to_have_attribute("maxlength", "200")
    assert field.get_attribute("required") is None
    prefix = "Synthetic Ångström "
    title = prefix + "x" * (200 - len(prefix))
    field.fill(title)
    page.locator("#id_body").fill("Synthetic body for the heading workflow")
    page.locator("#save-entry").click()
    heading = page.locator("#diary-results .entry-title")
    expect(heading).to_have_text(title)
    expect(field).to_have_value("")
    title_box = heading.bounding_box()
    date_box = page.locator("#diary-results .entry-heading time").bounding_box()
    assert title_box["y"] >= date_box["y"] + date_box["height"]
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

    field.fill("Unfinished synthetic heading")
    open_filters(page)
    page.locator("#id_q").fill("ångström")
    expect(page).to_have_url(re.compile(r"q="))
    expect(page.locator(".diary-card")).to_have_count(1)
    expect(field).to_have_value("Unfinished synthetic heading")
    page.locator(".entry-heading h2 a").click()
    entry_url = page.url
    expect(page.locator("h1 .entry-title")).to_have_text(title)
    expect(page).to_have_title(title + " · GrowBook")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.locator(".entry-actions .btn-primary").click()
    expect(page.locator("#id_title")).to_have_value(title)
    page.locator("#id_title").fill("Renamed synthetic heading")
    page.locator("#entry-form button[type=submit]").click()
    expect(page).to_have_url(entry_url)
    expect(page.locator(".entry-title")).to_have_text("Renamed synthetic heading")

    page.goto(live_server.url + "/?" + urlencode({"q": "ångström"}))
    expect(page.locator(".diary-card")).to_have_count(0)
    page.goto(live_server.url + "/?q=renamed")
    expect(page.locator(".diary-card")).to_have_count(1)
    page.locator(".entry-heading h2 a").click()
    page.locator(".entry-actions .btn-primary").click()
    page.locator("#id_title").fill("")
    page.locator("#entry-form button[type=submit]").click()
    expect(page).to_have_url(entry_url)
    expect(page.locator(".entry-title")).to_have_count(0)
    expect(page.locator(".entry-body")).to_have_text("Synthetic body for the heading workflow")
    page.goto(live_server.url + "/?" + urlencode({"q": "Synthetic body"}))
    expect(page.locator(".diary-card")).to_have_count(1)
    expect(page.locator(".entry-title")).to_have_count(0)


@pytest.mark.parametrize("operation", ["capture", "edit"])
def test_title_errors_keep_photos_and_are_accessible_for_retry(
    page, live_server, user, image_upload, operation
):
    if operation == "edit":
        entry_url = open_entry_edit(page, live_server, user)
        post_url = entry_url + "edit/"
        errors = "#id_title_errors"
        photos = "#id_photos"
        save = page.locator("#entry-form button[type=submit]")
    else:
        login(page, live_server, user)
        post_url = live_server.url + "/capture/"
        errors = "#capture-title-errors"
        photos = "#id_library"
        save = page.locator("#save-entry")
    title = page.locator("#id_title")
    title.evaluate("input => input.removeAttribute('maxlength')")
    title.fill("Å" * 201)
    page.locator("#id_body").fill("Synthetic draft to retry")
    page.locator(photos).set_input_files(upload(image_upload()))
    key = page.locator("#id_submission_key").input_value()
    with page.expect_response(
        lambda response: response.url == post_url and response.request.method == "POST"
    ) as rejected:
        save.click()
    assert rejected.value.status == 422
    expect(title).to_have_attribute("aria-invalid", "true")
    assert errors[1:] in title.get_attribute("aria-describedby").split()
    expect(page.locator(errors)).not_to_be_empty()
    expect(title).to_have_value("Å" * 201)
    expect(page.locator("#id_body")).to_have_value("Synthetic draft to retry")
    expect(page.locator("#id_submission_key")).to_have_value(key)
    assert page.locator(photos).evaluate("input => input.files.length") == 1

    title.fill("Corrected synthetic heading")
    save.click()
    if operation == "edit":
        expect(page).to_have_url(entry_url)
        expect(page.locator(".large-photo")).to_have_count(1)
    else:
        expect(page.locator("#quick-receipt")).to_contain_text("har sparats")
        expect(title).to_have_value("")
        expect(title).not_to_have_attribute("aria-invalid", "true")
        expect(page.locator("#diary-results .photo-grid img")).to_have_count(1)
    expect(page.locator(".entry-title")).to_have_text("Corrected synthetic heading")
