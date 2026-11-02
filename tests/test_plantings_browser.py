"""Chromium form/history interaction, not target-device or private-routing evidence."""

import os
import re

import pytest
from playwright.sync_api import expect, sync_playwright

from garden.services import (
    archive_variety,
    create_area,
    create_crop,
    create_planting,
    create_planting_event,
    create_variety,
)

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def catalog(user, image_upload):
    from datetime import date

    crop = create_crop(actor=user, name="Tomato").record
    other_crop = create_crop(actor=user, name="Bean").record
    variety = create_variety(actor=user, crop=crop, name="Golden").record
    other_variety = create_variety(actor=user, crop=other_crop, name="Blue").record
    archived = create_variety(actor=user, crop=crop, name="Archived trial").record
    archive_variety(actor=user, pk=archived.pk)
    parent = create_area(actor=user, name="Greenhouse").record
    child = create_area(actor=user, name="Bed", parent=parent).record
    first = create_planting(
        actor=user,
        crop=crop,
        variety=variety,
        area=child,
        start_year=2026,
        label="First batch",
        notes="Synthetic first note",
        uploads=[image_upload()],
    ).record
    second = create_planting(
        actor=user,
        crop=crop,
        area=parent,
        start_year=2024,
        lifespan="persistent",
        label="Persistent batch",
        notes="Synthetic cumulative note",
        end_year=2026,
    ).record
    create_planting_event(
        actor=user,
        planting=first,
        kind="sowing",
        planned_date=date(2025, 12, 20),
        actual_date=date(2026, 1, 2),
        note="Synthetic overwintered sowing",
    )
    return {
        "crop": crop,
        "other_crop": other_crop,
        "variety": variety,
        "other_variety": other_variety,
        "parent": parent,
        "child": child,
        "first": first,
        "second": second,
    }


@pytest.fixture
def page(live_server, settings, user, catalog):
    settings.WHITENOISE_USE_FINDERS = True
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=os.environ.get("GROWBOOK_BROWSER_CHANNEL", "chromium")
        )
        context = browser.new_context(viewport={"width": 1280, "height": 1000})
        page = context.new_page()
        page.set_default_timeout(10000)
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


def save(page):
    page.locator("#register-form").get_by_role("button", name="Spara", exact=True).click()


def test_dependent_variety_switch_does_not_reload_or_lose_draft_and_is_csp_safe(
    page, live_server, user, catalog, image_upload
):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console", lambda message: errors.append(message.text) if message.type == "error" else None
    )
    page.add_init_script("""document.addEventListener('securitypolicyviolation', event => {
        console.error('CSP violation: ' + event.effectiveDirective);
    });""")
    login(page, live_server, user)
    page.goto(live_server.url + "/garden/plantings/new/")
    page.locator("#id_label").fill("Dependent choices batch")
    page.locator("#id_start_year").fill("2027")
    page.locator("#id_notes").fill("Keep draft — Ärtor")
    page.locator("#id_photos").set_input_files(image_file(image_upload))
    key = page.locator("#id_submission_key").input_value()
    crop, variety = page.locator("#id_crop"), page.locator("#id_variety")
    crop.focus()
    crop.select_option(str(catalog["crop"].pk))
    expect(variety.locator("option")).to_have_text(["Okänd sort", "Golden (Tomato)"])
    expect(crop).to_be_focused()
    variety.select_option(str(catalog["variety"].pk))
    crop.focus()
    crop.select_option(str(catalog["other_crop"].pk))
    expect(variety.locator("option")).to_have_text(["Okänd sort", "Blue (Bean)"])
    expect(variety).to_have_value("")
    expect(crop).to_be_focused()
    expect(page).to_have_url(live_server.url + "/garden/plantings/new/")
    expect(page.locator("#id_notes")).to_have_value("Keep draft — Ärtor")
    expect(page.locator("#id_submission_key")).to_have_value(key)
    assert page.locator("#id_photos").evaluate("input => input.files.length") == 1
    expect(page.locator("#variety-status")).to_have_text("Sortvalen har uppdaterats.")
    variety.select_option(str(catalog["other_variety"].pk))
    save(page)
    expect(page).to_have_url(re.compile(r"/garden/plantings/\d+/$"))
    expect(page.locator(".record-fields")).to_contain_text("Blue")
    expect(page.locator(".register-photos figure")).to_have_count(1)
    assert errors == []
    assert page.evaluate("Object.keys(localStorage).length") == 0


def test_year_area_history_rows_expand_independently_with_keyboard(
    page, live_server, user, catalog
):
    login(page, live_server, user)
    page.goto(live_server.url + f"/garden/areas/{catalog['parent'].pk}/history/?year=2026")
    first, second = catalog["first"], catalog["second"]
    first_toggle = page.get_by_role("button", name="Detaljer: First batch", exact=True)
    second_toggle = page.get_by_role("button", name="Detaljer: Persistent batch", exact=True)
    first_details = page.locator(f"#details-planting-{first.pk}")
    second_details = page.locator(f"#details-planting-{second.pk}")
    expect(page.locator(".register-table").first).to_contain_text("Greenhouse / Bed")
    first_toggle.focus()
    first_toggle.press("Enter")
    expect(first_details).to_contain_text("Synthetic overwintered sowing")
    expect(first_details).to_contain_text("2025-12-20")
    expect(first_toggle).to_be_focused()
    second_toggle.focus()
    second_toggle.press("Space")
    expect(second_details).to_contain_text("Synthetic cumulative note")
    expect(first_details).to_be_visible()
    expect(second_toggle).to_be_focused()
    first_toggle.click()
    expect(first_details).to_be_hidden()
    expect(second_details).to_be_visible()
    page.get_by_role("link", name="Nästa år", exact=True).click()
    expect(page.locator("h1")).to_contain_text("2027")
    expect(page.locator(".register-table")).to_contain_text("Inga odlingar matchar filtren.")
    page.get_by_role("link", name="Föregående år", exact=True).click()
    page.locator(".register-table th[scope=row]").get_by_role(
        "link", name="First batch", exact=True
    ).click()
    page.locator(".record-fields").get_by_role("link", name="Golden", exact=True).click()
    expect(page).to_have_url(
        live_server.url + f"/garden/varieties/{catalog['variety'].pk}/history/"
    )
    page.get_by_role("button", name="Detaljer: First batch", exact=True).click()
    expect(page.locator(f"#details-planting-{first.pk}")).to_contain_text(
        "Synthetic overwintered sowing"
    )


def test_invalid_planting_retains_zero_text_photos_key_focus_and_can_retry(
    page, live_server, user, catalog, image_upload
):
    login(page, live_server, user)
    page.goto(live_server.url + f"/garden/plantings/new/?crop={catalog['crop'].pk}&year=2026")
    page.locator("#id_label").fill("Retry batch")
    page.locator("#id_quantity").fill("0")
    page.locator("#id_evaluation").fill("Synthetic evaluation")
    page.locator("#id_photos").set_input_files(
        [image_file(image_upload), image_file(image_upload, "second.jpg")]
    )
    key = page.locator("#id_submission_key").input_value()
    page.locator("#register-form").evaluate("form => form.noValidate = true")
    page.locator("#id_end_year").fill("2027")
    save(page)
    expect(page.locator("#register-errors")).to_contain_text("En ettårig odling har inget slutår.")
    expect(page.locator("#register-errors")).to_be_focused()
    expect(page.locator("#id_evaluation")).to_have_value("Synthetic evaluation")
    expect(page.locator("#id_quantity")).to_have_value("0")
    expect(page.locator("#id_submission_key")).to_have_value(key)
    assert page.locator("#id_photos").evaluate("input => input.files.length") == 2
    page.locator("#id_end_year").fill("")
    save(page)
    expect(page).to_have_url(re.compile(r"/garden/plantings/\d+/$"))
    expect(page.locator(".record-fields")).to_contain_text("Synthetic evaluation")
    expect(page.locator(".record-fields")).to_contain_text("Okänd sort")
    expect(page.locator(".register-photos figure")).to_have_count(2)
    page.get_by_role("link", name="Redigera odling", exact=True).click()
    page.locator("#id_lifespan").select_option("persistent")
    page.locator("#id_end_year").fill("2027")
    save(page)
    expect(page).to_have_url(re.compile(r"/garden/plantings/\d+/$"))
    expect(page.locator(".record-fields")).to_contain_text("2027")
    expect(page.locator(".register-photos figure")).to_have_count(2)


def test_dependent_choice_network_failure_retries_and_session_expiry_preserves_form(
    page, live_server, user, catalog, image_upload
):
    login(page, live_server, user)
    page.goto(live_server.url + "/garden/plantings/new/")
    page.locator("#id_label").fill("Session recovery batch")
    page.locator("#id_notes").fill("Keep session draft")
    page.locator("#id_photos").set_input_files(image_file(image_upload))
    key = page.locator("#id_submission_key").input_value()
    page.route("**/garden/plantings/varieties/**", lambda route: route.abort())
    page.locator("#id_crop").select_option(str(catalog["crop"].pk))
    expect(page.locator("#variety-status")).to_contain_text("Sortvalen kunde inte laddas")
    page.unroute("**/garden/plantings/varieties/**")
    page.get_by_role("button", name="Försök ladda sortval igen", exact=True).click()
    expect(page.locator("#id_variety option")).to_have_count(2)
    other = page.context.new_page()
    other.goto(live_server.url + "/")
    other.get_by_role("button", name="Logga ut", exact=True).click()
    page.locator("#id_crop").select_option(str(catalog["other_crop"].pk))
    expect(page.locator("#variety-status")).to_contain_text("session")
    save(page)
    expect(page.locator("#register-errors")).to_contain_text("session")
    expect(page.locator("#id_submission_key")).to_have_value(key)
    expect(page.locator("#id_notes")).to_have_value("Keep session draft")
    assert page.locator("#id_photos").evaluate("input => input.files.length") == 1
    other.locator("#id_username").fill(user.username)
    other.locator("#id_password").fill("Synthetic-password-42")
    other.get_by_role("button", name="Logga in", exact=True).click()
    expect(other).to_have_url(live_server.url + "/")
    page.get_by_role("button", name="Försök ladda sortval igen", exact=True).click()
    expect(page.locator("#id_variety option")).to_have_count(2)
    save(page)
    expect(page).to_have_url(re.compile(r"/garden/plantings/\d+/$"))
    expect(page.locator(".register-photos figure")).to_have_count(1)
    other.close()


def test_no_javascript_dependent_choices_drafts_and_early_plan_save(
    page, live_server, user, catalog
):
    login(page, live_server, user)
    context = page.context.browser.new_context(
        java_script_enabled=False, storage_state=page.context.storage_state()
    )
    plain = context.new_page()
    plain.goto(live_server.url + "/garden/plantings/new/")
    plain.locator("#id_label").fill("No-JS batch")
    plain.locator("#id_notes").fill("Keep no-JS draft")
    key = plain.locator("#id_submission_key").input_value()
    plain.locator("#id_crop").select_option(str(catalog["crop"].pk))
    plain.get_by_role("button", name="Uppdatera sortval", exact=True).click()
    expect(plain.locator("#id_variety option")).to_have_count(2)
    plain.locator("#id_variety").select_option(str(catalog["variety"].pk))
    plain.locator("#id_crop").select_option(str(catalog["other_crop"].pk))
    plain.get_by_role("button", name="Uppdatera sortval", exact=True).click()
    expect(plain.locator("#id_variety option")).to_have_text(["Okänd sort", "Blue (Bean)"])
    expect(plain.locator("#id_variety")).to_have_value("")
    expect(plain.locator("#id_notes")).to_have_value("Keep no-JS draft")
    expect(plain.locator("#id_submission_key")).to_have_value(key)
    save(plain)
    expect(plain).to_have_url(re.compile(r"/garden/plantings/\d+/$"))
    expect(plain.locator(".record-fields")).to_contain_text("Keep no-JS draft")
    context.close()


def test_repeated_event_form_dates_edit_and_confirmed_delete(page, live_server, user, catalog):
    login(page, live_server, user)
    record = catalog["first"]
    page.goto(live_server.url + record.get_absolute_url())
    page.get_by_role("link", name="Ny odlingshändelse", exact=True).click()
    page.locator("#register-form").evaluate("form => form.noValidate = true")
    save(page)
    expect(page.locator("#register-errors")).to_contain_text("Ange ett planerat datum")
    expect(page.locator("#register-errors")).to_be_focused()
    page.locator("#id_kind").select_option("sowing")
    page.locator("#id_planned_date").fill("2025-12-31")
    page.locator("#id_actual_date").fill("2027-01-01")
    page.locator("#id_note").fill("Repeated synthetic sowing")
    save(page)
    expect(page).to_have_url(re.compile(r"/garden/plantings/\d+/#event-\d+$"))
    expect(page.locator(".event-table tbody tr")).to_have_count(2)
    row = page.locator(".event-table tbody tr").filter(has_text="Repeated synthetic sowing")
    expect(row).to_contain_text("2027-01-01")
    row.get_by_role("link", name=re.compile("Redigera:")).click()
    expect(page.locator("#id_planned_date")).to_have_value("2025-12-31")
    page.locator("#id_actual_date").fill("2027-01-02")
    save(page)
    expect(row).to_contain_text("2027-01-02")
    row.get_by_role("link", name=re.compile("Radera:")).click()
    page.get_by_role("checkbox", name="Jag bekräftar raderingen.").check()
    page.locator("main").get_by_role("button", name="Radera odlingshändelse", exact=True).click()
    expect(page).to_have_url(live_server.url + record.get_absolute_url())
    expect(page.locator(".event-table tbody tr")).to_have_count(1)
