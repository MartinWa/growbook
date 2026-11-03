"""Chromium register interactions; not evidence of device-native pickers or routing."""

import os
import re

import pytest
from playwright.sync_api import expect, sync_playwright

from garden.services import create_area, create_crop

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def registers(user, image_upload):
    # Seed before Playwright starts its sync-API event loop; keep Django's async guard.
    first = create_crop(actor=user, name="Ärtor", notes="Full first crop note").record
    second = create_crop(actor=user, name="Bean", notes="Full second crop note").record
    parent = create_area(actor=user, name="Parent").record
    child = create_area(
        actor=user,
        name="Child",
        parent=parent,
        notes="Full notes",
        uploads=[image_upload(), image_upload()],
    ).record
    grandchild = create_area(actor=user, name="Grandchild", parent=child).record
    return {
        "first": first,
        "second": second,
        "parent": parent,
        "child": child,
        "grandchild": grandchild,
    }


@pytest.fixture
def page(live_server, settings, user, registers):
    settings.WHITENOISE_USE_FINDERS = True
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=os.environ.get("GROWBOOK_BROWSER_CHANNEL", "chromium")
        )
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.set_default_timeout(8000)
        yield page
        context.close()
        browser.close()


def login(page, live_server, user):
    page.goto(live_server.url + "/accounts/login/")
    page.locator("#id_username").fill(user.username)
    page.locator("#id_password").fill("Synthetic-password-42")
    page.get_by_role("button", name="Logga in", exact=True).click()
    expect(page).to_have_url(live_server.url + "/")


def image_file(image_upload, name="synthetic.jpg"):
    return {"name": name, "mimeType": "image/jpeg", "buffer": image_upload().read()}


def test_shared_photo_picker_accumulates_native_choices_and_retains_them_on_validation(
    page, live_server, user, image_upload
):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, live_server, user)
    page.goto(live_server.url + "/garden/areas/new/")
    chooser = page.locator("[data-photo-chooser]")
    expect(page.locator("[data-native-photos]")).to_be_hidden()
    expect(page.locator("[data-photo-previews] figure")).to_have_count(0)
    page.locator("#id_name").fill("Parent")
    with page.expect_file_chooser() as selection:
        chooser.click()
    assert selection.value.element.get_attribute("id") == "id_photos"
    assert selection.value.is_multiple()
    selection.value.set_files(image_file(image_upload, "camera.jpg"))
    expect(chooser).to_be_focused()
    with page.expect_file_chooser() as selection:
        chooser.click()
    selection.value.set_files(image_file(image_upload, "library.jpg"))
    page.locator("#id_photos").set_input_files(image_file(image_upload, "extra.jpg"))
    expect(page.locator("[data-photo-previews] figure")).to_have_count(3)
    page.get_by_role("button", name="Ta bort: extra.jpg", exact=True).click()
    expect(page.get_by_role("button", name="Ta bort: library.jpg", exact=True)).to_be_focused()
    expect(page.locator("[data-photo-count]")).to_have_text("Valda bilder: 2 av 10")
    key = page.locator("#id_submission_key").input_value()
    page.locator("#register-form button[type=submit]").click()
    expect(page.locator("#id_name_errors")).to_contain_text("Namnet används redan")
    expect(page.locator("#id_name")).to_have_attribute("aria-invalid", "true")
    page.locator("#register-errors").get_by_role("link", name="Namn", exact=True).click()
    expect(page.locator("#id_name")).to_be_focused()
    expect(page.locator("[data-photo-previews] figure")).to_have_count(2)
    assert page.locator("#id_photos").evaluate("input => input.files.length") == 2
    expect(page.locator("#id_submission_key")).to_have_value(key)
    page.locator("#id_name").fill("Shared picker area")
    page.locator("#register-form button[type=submit]").click()
    expect(page).to_have_url(re.compile(r"/garden/areas/\d+/$"))
    expect(page.locator(".register-photos figure")).to_have_count(2)
    assert errors == []


def test_no_js_photo_sources_share_the_existing_upload_field(page, live_server, user, image_upload):
    login(page, live_server, user)
    context = page.context.browser.new_context(
        java_script_enabled=False, storage_state=page.context.storage_state()
    )
    plain = context.new_page()
    plain.goto(live_server.url + "/garden/areas/new/")
    expect(plain.locator("[data-native-photos]")).to_be_visible()
    expect(plain.locator("[data-photo-chooser]")).to_be_hidden()
    plain.locator("#id_name").fill("No-JS camera and library area")
    plain.locator("#id_photos_camera").set_input_files(image_file(image_upload, "camera.jpg"))
    plain.locator("#id_photos").set_input_files(image_file(image_upload, "library.jpg"))
    plain.locator("#register-form button[type=submit]").click()
    expect(plain).to_have_url(re.compile(r"/garden/areas/\d+/$"))
    expect(plain.locator(".register-photos figure")).to_have_count(2)
    context.close()


def test_photo_picker_fallback_replaces_choices_and_can_clear_them(
    page, live_server, user, image_upload
):
    page.add_init_script(
        """window.DataTransfer = class {
            constructor() { throw new Error('Synthetic unsupported FileList assignment'); }
        };"""
    )
    login(page, live_server, user)
    page.goto(live_server.url + "/garden/areas/new/")
    page.locator("#id_photos").set_input_files(image_file(image_upload, "first.jpg"))
    page.locator("#id_photos").set_input_files(image_file(image_upload, "replacement.jpg"))
    expect(page.locator("[data-photo-previews] figure")).to_have_count(1)
    expect(page.locator("[data-photo-previews] figcaption")).to_have_text("replacement.jpg")
    expect(page.locator("[data-photo-count]")).to_have_text("Valda bilder: 1 av 10")
    page.get_by_role("button", name="Rensa valda bilder", exact=True).click()
    expect(page.locator("[data-photo-previews] figure")).to_have_count(0)
    expect(page.locator("[data-photo-count]")).to_have_text("")
    assert page.locator("#id_photos").evaluate("input => input.files.length") == 0
    expect(page.locator("[data-photo-chooser]")).to_be_focused()


def test_phone_register_wraps_long_names_and_archive_has_no_js_fallback(
    page, live_server, user, registers
):
    page.set_viewport_size({"width": 390, "height": 844})
    login(page, live_server, user)
    page.goto(live_server.url + "/garden/crops/")
    # Exercise wrapping independently of database name limits.
    page.locator(".responsive-table th[scope=row] > a").first.evaluate(
        "link => link.textContent = 'Synthetic'.repeat(20)"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_role("button", name=f"Detaljer: {registers['first'].name}", exact=True).click()
    expect(page.locator(f"#details-crop-{registers['first'].pk}")).to_contain_text(
        "Full first crop note"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    context = page.context.browser.new_context(
        java_script_enabled=False, storage_state=page.context.storage_state()
    )
    plain = context.new_page()
    plain.goto(live_server.url + "/garden/crops/")
    plain.locator(".archive-options > summary").click()
    plain.get_by_role("checkbox", name="Visa även arkiverade poster").check()
    plain.locator(".register-filter").get_by_role("button", name="Visa", exact=True).click()
    expect(plain).to_have_url(live_server.url + "/garden/crops/?archived=1")
    plain.get_by_role("link", name="Dölj arkiverade poster", exact=True).click()
    expect(plain).to_have_url(live_server.url + "/garden/crops/")
    context.close()


def test_register_rows_expand_independently_with_keyboard_focus_and_csp(
    page, live_server, user, registers
):
    first, second = registers["first"], registers["second"]
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console", lambda message: errors.append(message.text) if message.type == "error" else None
    )
    page.add_init_script("""document.addEventListener('securitypolicyviolation', event => {
        console.error('CSP violation: ' + event.effectiveDirective);
    });""")
    login(page, live_server, user)
    page.get_by_role("navigation", name="Huvudmeny").get_by_role(
        "link", name="Trädgård", exact=True
    ).click()
    expect(page.locator("h1")).to_have_text("Trädgård")
    page.locator(".garden-cards").get_by_role("link", name="Grödor", exact=True).click()
    first_toggle = page.get_by_role("button", name=f"Detaljer: {first.name}", exact=True)
    second_toggle = page.get_by_role("button", name=f"Detaljer: {second.name}", exact=True)
    first_details = page.locator(f"#details-crop-{first.pk}")
    second_details = page.locator(f"#details-crop-{second.pk}")
    expect(first_toggle).to_have_attribute("aria-expanded", "false")
    first_toggle.focus()
    first_toggle.press("Enter")
    expect(first_toggle).to_have_attribute("aria-expanded", "true")
    expect(first_details).to_contain_text("Full first crop note")
    expect(first_toggle).to_be_focused()
    second_toggle.focus()
    second_toggle.press("Space")
    expect(second_details).to_contain_text("Full second crop note")
    expect(first_details).to_be_visible()
    expect(second_toggle).to_be_focused()
    first_toggle.click()
    expect(first_details).to_be_hidden()
    expect(second_details).to_be_visible()
    first_toggle.click()
    expect(first_details).to_contain_text("Full first crop note")
    first_details.get_by_role("link", name="Redigera", exact=True).click()
    expect(page).to_have_url(live_server.url + f"/garden/crops/{first.pk}/edit/")
    expect(page.locator("#id_notes")).to_have_value("Full first crop note")
    assert errors == []
    assert page.evaluate("Object.keys(localStorage).length") == 0
    expect(page.get_by_role("heading", name="Redigera gröda", exact=True)).to_be_visible()


def test_inline_details_retry_after_failed_request_and_links_navigate_normally(
    page, live_server, user, registers
):
    area = registers["child"]
    login(page, live_server, user)
    page.goto(live_server.url + "/garden/areas/")
    details = page.locator(f"#details-area-{area.pk}")
    toggle = page.get_by_role("button", name=f"Detaljer: {area.name}", exact=True)
    page.route(
        f"**{area.get_absolute_url()}",
        lambda route: route.fulfill(status=503, body="Synthetic failure"),
    )
    toggle.click()
    expect(details).to_contain_text("Detaljerna kunde inte laddas")
    toggle.click()
    expect(details).to_be_hidden()
    page.unroute(f"**{area.get_absolute_url()}")
    toggle.click()
    expect(details).to_contain_text("Full notes")
    expect(details.locator(".register-photos figure")).to_have_count(2)
    page.locator(".register-table th[scope=row]").get_by_role(
        "link", name="Child", exact=True
    ).click()
    expect(page).to_have_url(live_server.url + area.get_absolute_url())
    expect(page.locator("h1")).to_have_text("Child")


def test_register_session_expiry_new_tab_login_preserves_fields_photos_and_key(
    page, live_server, user, image_upload
):
    login(page, live_server, user)
    page.goto(live_server.url + "/garden/areas/new/")
    page.locator("#id_name").fill("Session recovery bed")
    page.locator("#id_notes").fill("Keep register draft")
    page.locator("#id_photos").set_input_files(image_file(image_upload))
    key = page.locator("#id_submission_key").input_value()
    other = page.context.new_page()
    other.goto(live_server.url + "/")
    other.locator("#account-menu > summary").click()
    other.get_by_role("button", name="Logga ut", exact=True).click()
    page.locator("#register-form").get_by_role("button", name="Spara", exact=True).click()
    expect(page.locator("#register-errors")).to_contain_text("session")
    expect(page.locator("#id_submission_key")).to_have_value(key)
    assert page.locator("#id_photos").evaluate("input => input.files.length") == 1
    expect(page.locator("#id_notes")).to_have_value("Keep register draft")
    other.locator("#id_username").fill(user.username)
    other.locator("#id_password").fill("Synthetic-password-42")
    other.get_by_role("button", name="Logga in", exact=True).click()
    expect(other).to_have_url(live_server.url + "/")
    page.locator("#register-form").get_by_role("button", name="Spara", exact=True).click()
    expect(page).to_have_url(re.compile(r"/garden/areas/\d+/$"))
    expect(page.locator(".register-photos figure")).to_have_count(1)
    other.close()


def test_register_invalid_form_keeps_fields_photos_key_and_focus_then_saves(
    page, live_server, user, image_upload
):
    login(page, live_server, user)
    page.goto(live_server.url + "/garden/areas/new/")
    page.locator("#id_name").fill("Synthetic bed")
    page.locator("#id_notes").fill("Keep my notes — Ärtor")
    page.locator("#id_photos").set_input_files(
        [image_file(image_upload), image_file(image_upload, "second.jpg")]
    )
    key = page.locator("#id_submission_key").input_value()
    page.locator("#register-form").evaluate("form => form.noValidate = true")
    page.locator("#id_length_cm").fill("0")
    with page.expect_response(
        lambda response: (
            response.url.endswith("/garden/areas/new/") and response.request.method == "POST"
        )
    ) as rejected:
        page.locator("#register-form").get_by_role("button", name="Spara", exact=True).click()
    assert rejected.value.status == 422
    expect(page.locator("#register-errors")).not_to_be_empty()
    expect(page.locator("#register-errors")).to_be_focused()
    expect(page.locator("#id_notes")).to_have_value("Keep my notes — Ärtor")
    expect(page.locator("#id_submission_key")).to_have_value(key)
    assert page.locator("#id_photos").evaluate("input => input.files.length") == 2
    expect(
        page.locator("#register-form").get_by_role("button", name="Spara", exact=True)
    ).to_be_enabled()
    page.locator("#id_length_cm").fill("120")
    page.locator("#register-form").get_by_role("button", name="Spara", exact=True).click()
    expect(page).to_have_url(re.compile(r"/garden/areas/\d+/$"))
    expect(page.locator(".register-photos figure")).to_have_count(2)
    expect(page.locator(".record-fields")).to_contain_text("Keep my notes — Ärtor")
    expect(page.get_by_role("status")).to_contain_text("Posten har sparats")
    area_url = page.url
    page.locator(".register-photos figure").first.locator("a").first.click()
    expect(page).to_have_url(re.compile(r"/photos/\d+/$"))
    page.locator(".photo-viewer img").evaluate("image => image.decode()")
    page.get_by_role("link", name="Tillbaka till posten").click()
    expect(page).to_have_url(area_url)


def test_register_unicode_duplicate_feedback_then_edit_archive_and_restore(
    page, live_server, user, registers
):
    crop = registers["first"]
    login(page, live_server, user)
    page.goto(live_server.url + "/garden/crops/new/")
    page.locator("#id_name").fill("A\u0308RTOR")
    page.locator("#id_notes").fill("Draft notes")
    page.locator("#register-form").get_by_role("button", name="Spara", exact=True).click()
    expect(page.locator("#register-errors")).to_contain_text("Namnet används redan")
    expect(page.locator("#register-errors")).to_be_focused()
    expect(page.locator("#id_notes")).to_have_value("Draft notes")
    page.goto(live_server.url + f"/garden/crops/{crop.pk}/edit/")
    page.locator("#id_notes").fill("Edited notes")
    page.locator("#register-form").get_by_role("button", name="Spara", exact=True).click()
    expect(page).to_have_url(live_server.url + crop.get_absolute_url())
    expect(page.locator(".record-fields")).to_contain_text("Edited notes")
    page.get_by_role("button", name="Arkivera", exact=True).click()
    expect(page.get_by_role("button", name="Gör valbar igen", exact=True)).to_be_visible()
    page.goto(live_server.url + "/garden/crops/")
    expect(
        page.locator(".register-table").get_by_role("link", name="Ärtor", exact=True)
    ).to_have_count(0)
    page.locator(".archive-options > summary").click()
    page.get_by_role("checkbox", name="Visa även arkiverade poster").check()
    expect(page).to_have_url(live_server.url + "/garden/crops/?archived=1")
    page.locator(".register-table").get_by_role("link", name="Ärtor", exact=True).click()
    page.get_by_role("button", name="Gör valbar igen", exact=True).click()
    expect(page.get_by_role("button", name="Arkivera", exact=True)).to_be_visible()


def test_register_photo_form_network_error_retry_preserves_selection(
    page, live_server, user, image_upload, registers
):
    crop = registers["first"]
    login(page, live_server, user)
    page.goto(live_server.url + f"/garden/varieties/new/?crop={crop.pk}")
    page.locator("#id_name").fill("Synthetic variety")
    page.locator("#id_photos").set_input_files(image_file(image_upload))
    key = page.locator("#id_submission_key").input_value()
    page.route(
        "**/garden/varieties/new/**",
        lambda route: route.abort() if route.request.method == "POST" else route.continue_(),
    )
    save = page.locator("#register-form").get_by_role("button", name="Spara", exact=True)
    save.click()
    expect(page.locator("#register-errors")).not_to_be_empty()
    expect(save).to_be_enabled()
    expect(page.locator("#id_submission_key")).to_have_value(key)
    assert page.locator("#id_photos").evaluate("input => input.files.length") == 1
    expect(page.locator("#id_crop")).to_have_value(str(crop.pk))
    page.unroute("**/garden/varieties/new/**")
    save.click()
    expect(page).to_have_url(re.compile(r"/garden/varieties/\d+/$"))
    expect(page.locator(".register-photos figure")).to_have_count(1)


def test_register_detail_without_javascript_keeps_links_forms_and_drafts(
    page, live_server, user, registers
):
    area = registers["child"]
    login(page, live_server, user)
    # The base link remains useful even if JavaScript is unavailable.
    context = page.context.browser.new_context(
        java_script_enabled=False, storage_state=page.context.storage_state()
    )
    plain = context.new_page()
    plain.goto(live_server.url + "/garden/areas/")
    plain.get_by_role("link", name=f"Detaljer: {area.name}", exact=True).click()
    expect(plain).to_have_url(live_server.url + area.get_absolute_url())
    expect(plain.locator(".record-fields")).to_contain_text("Full notes")
    plain.get_by_role("link", name="Redigera", exact=True).click()
    plain.locator("#id_name").fill("Draft bed")
    plain.locator("#id_notes").fill("Keep notes")
    # A valid ordinary POST has no dependency on HTMX.
    plain.locator("#register-form").get_by_role("button", name="Spara", exact=True).click()
    expect(plain).to_have_url(live_server.url + area.get_absolute_url())
    expect(plain.locator("h1")).to_have_text("Draft bed")
    plain.goto(live_server.url + "/garden/crops/new/")
    plain.locator("#id_name").fill("A\u0308RTOR")
    plain.locator("#id_notes").fill("Keep no-JS draft")
    plain.locator("#register-form").get_by_role("button", name="Spara", exact=True).click()
    expect(plain.locator("#register-errors")).to_contain_text("Namnet används redan")
    expect(plain.locator("#id_notes")).to_have_value("Keep no-JS draft")
    context.close()


def test_area_parent_options_and_photo_caption_order_delete_controls(
    page, live_server, user, registers
):
    parent, child, grandchild = registers["parent"], registers["child"], registers["grandchild"]
    login(page, live_server, user)
    page.goto(live_server.url + f"/garden/areas/{parent.pk}/edit/")
    options = page.locator("#id_parent option").evaluate_all(
        "options => options.map(option => option.value)"
    )
    assert (
        str(parent.pk) not in options
        and str(child.pk) not in options
        and str(grandchild.pk) not in options
    )
    page.goto(live_server.url + child.get_absolute_url())
    first_photo = page.locator(".register-photos figure").first
    first_photo.get_by_role("link", name="Redigera bilduppgifter").click()
    page.locator("#id_caption").fill("Synthetic caption")
    page.locator("#id_position").fill("5")
    page.locator("main").get_by_role("button", name="Spara", exact=True).click()
    expect(page).to_have_url(live_server.url + child.get_absolute_url())
    expect(page.locator(".register-photos figure").last.locator("figcaption")).to_have_text(
        "Synthetic caption"
    )
    page.locator(".register-photos figure").last.get_by_role("link", name="Radera bild").click()
    page.get_by_role("checkbox", name="Jag bekräftar raderingen.").check()
    page.locator("main").get_by_role("button", name="Radera bild", exact=True).click()
    expect(page).to_have_url(live_server.url + child.get_absolute_url())
    expect(page.locator(".register-photos figure")).to_have_count(1)
