"""Chromium form/history interaction, not target-device or private-routing evidence."""

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
def page(live_server, settings, user, catalog, browser_endpoint):
    settings.WHITENOISE_USE_FINDERS = True
    with (
        sync_playwright() as playwright,
        playwright.chromium.connect(browser_endpoint, timeout=20000) as browser,
        browser.new_context(viewport={"width": 1280, "height": 1000}) as context,
    ):
        page = context.new_page()
        page.set_default_timeout(10000)
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
    page.get_by_role("button", name="Logga in", exact=True).click()
    expect(page).to_have_url(live_server.url + "/")


def image_file(image_upload, name="synthetic.jpg"):
    return {"name": name, "mimeType": "image/jpeg", "buffer": image_upload().read()}


def open_optional(page):
    for selector in ("#planting-more-details", "#planting-notes-photos"):
        group = page.locator(selector)
        if group.get_attribute("open") is None:
            group.locator(":scope > summary").click()


def save(page):
    page.locator("#register-form").get_by_role("button", name="Spara", exact=True).click()


def test_minimal_planting_and_optional_error_reveal(page, live_server, user, catalog):
    login(page, live_server, user)
    page.goto(live_server.url + "/garden/plantings/new/")
    expect(page.locator("#id_label")).to_be_visible()
    expect(page.locator("#id_notes")).to_be_hidden()
    page.locator("#id_crop").select_option(str(catalog["crop"].pk))
    expect(page.locator("#id_variety option")).to_have_count(2)
    expect(page.locator("#id_label")).to_have_value("Tomato")
    page.locator("#id_start_year").fill("2027")
    save(page)
    expect(page).to_have_url(re.compile(r"/garden/plantings/\d+/$"))
    expect(page.locator(".record-fields")).to_contain_text("2027")
    page.goto(live_server.url + "/garden/plantings/new/")
    page.locator("#id_crop").select_option(str(catalog["crop"].pk))
    page.locator("#planting-more-details > summary").click()
    page.locator("#id_quantity").fill("-1")
    page.locator("#planting-more-details > summary").click()
    page.locator("#register-form").evaluate("form => form.noValidate = true")
    save(page)
    expect(page.locator("#id_quantity")).to_be_visible()
    expect(page.locator("#id_quantity")).to_have_attribute("aria-invalid", "true")
    expect(page.locator("#register-errors")).to_be_focused()
    page.locator("#register-errors").get_by_role("link", name="Antal", exact=True).click()
    expect(page.locator("#id_quantity")).to_be_focused()
    page.locator("#id_quantity").fill("0")
    save(page)
    expect(page).to_have_url(re.compile(r"/garden/plantings/\d+/$"))


def test_phone_filters_retry_back_and_reinitialize_rows(page, live_server, user, catalog):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": 390, "height": 844})
    login(page, live_server, user)
    page.goto(live_server.url + "/garden/plantings/?year=2026")
    expect(page.locator("#id_year")).to_be_hidden()
    expect(page.locator(".filter-chip")).to_have_text("Odlingsår: 2026")
    expect(page.locator("#planting-results .record-details")).to_have_count(0)
    open_filters(page)
    expect(page.locator("#id_year")).to_be_focused()
    page.route("**/garden/plantings/?*", lambda route: route.abort())
    page.locator("#id_year").fill("2027")
    page.locator("#id_year").press("Enter")
    expect(page.locator("#filter-status")).to_contain_text("Filtren kunde inte uppdateras")
    expect(page.locator("#planting-results")).to_contain_text("First batch")
    page.unroute("**/garden/plantings/?*")
    page.locator("[data-filter-retry]").click()
    expect(page).to_have_url(live_server.url + "/garden/plantings/?year=2027")
    expect(page.locator(".filter-chip")).to_have_text("Odlingsår: 2027")
    expect(page.locator("#planting-results")).to_contain_text("Inga odlingar detta odlingsår")
    page.keyboard.press("Escape")
    expect(page.locator("[data-filter-disclosure] > summary")).to_be_focused()
    page.go_back()
    expect(page.locator("h1")).to_contain_text("2026")
    open_filters(page)
    page.locator("#id_year").fill("10000")
    page.locator("#id_year").press("Tab")
    expect(page.locator('#planting-results [data-field-errors="year"]')).to_be_attached()
    expect(page.locator("#filter-status")).to_contain_text("Välj ett giltigt värde")
    expect(page.locator("#id_year")).to_have_attribute("aria-invalid", "true")
    expect(page.locator("#id_year")).to_have_value("10000")
    page.keyboard.press("Escape")
    page.locator("#planting-results .error-summary a").click()
    expect(page.locator("#filter-panel")).to_be_visible()
    expect(page.locator("#id_year")).to_be_focused()
    page.locator("#id_year").fill("2026")
    page.locator("#id_year").press("Enter")
    expect(page.locator("#planting-results .error-summary")).to_have_count(0)
    page.keyboard.press("Escape")
    page.locator(f'[data-area-id="{catalog["parent"].pk}"] > details > summary').click()
    page.get_by_role("button", name="First batch", exact=True).click()
    expect(page.locator(f"#details-planting-{catalog['first'].pk}")).to_contain_text("Golden")
    page.locator(".planting-list > li > [data-register-toggle]").first.evaluate(
        "link => link.textContent = 'Synthetic'.repeat(16)"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert errors == []


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
    open_optional(page)
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


def test_slow_variety_choices_are_superseded_and_save_cancellation_keeps_the_form_locked(
    page, live_server, user, catalog, image_upload
):
    login(page, live_server, user)
    page.goto(live_server.url + "/garden/plantings/new/")
    open_optional(page)
    page.locator("#id_label").fill("Synthetic overlapping choices")
    page.locator("#id_start_year").fill("2027")
    page.locator("#id_notes").fill("Preserve this draft and photo")
    page.locator("#id_photos").set_input_files(image_file(image_upload))
    key = page.locator("#id_submission_key").input_value()
    choices, submissions = [], []
    page.evaluate("window.choiceRequestCount = 0")

    def hold_choices(route):
        choices.append(route)
        page.evaluate("window.choiceRequestCount += 1")

    page.route("**/garden/plantings/varieties/**", hold_choices)
    crop = page.locator("#id_crop")
    crop.select_option(str(catalog["crop"].pk))
    expect(page.locator("#variety-status")).to_have_attribute("aria-busy", "true")
    assert len(choices) == 1
    crop.select_option(str(catalog["other_crop"].pk))
    page.wait_for_function("() => window.choiceRequestCount === 2")
    assert len(choices) == 2
    choices[1].continue_()
    expect(page.locator("#id_variety option")).to_have_text(["Okänd sort", "Blue (Bean)"])
    expect(page.locator("#id_notes")).to_have_value("Preserve this draft and photo")
    crop.select_option(str(catalog["crop"].pk))
    expect(page.locator("#variety-status")).to_have_attribute("aria-busy", "true")
    page.wait_for_function("() => window.choiceRequestCount === 3")
    page.route("**/garden/plantings/new/", lambda route: submissions.append(route))
    save(page)
    form = page.locator("#register-form")
    expect(form).to_have_attribute("aria-busy", "true")
    expect(crop).to_be_disabled()
    expect(page.locator("#id_variety")).to_be_disabled()
    expect(page.locator("#id_photos")).to_be_disabled()
    expect(page.locator("[data-photo-chooser]")).to_be_disabled()
    expect(page.locator("#id_notes")).not_to_be_editable()
    form.evaluate("form => form.requestSubmit()")
    assert len(submissions) == 1
    submissions[0].fulfill(
        status=503,
        content_type="text/html",
        body='<div class="error-summary">Synthetic temporary failure</div>',
    )
    expect(page.locator("#register-errors")).to_contain_text("Synthetic temporary failure")
    expect(crop).to_be_enabled()
    expect(page.locator("#id_variety")).to_be_enabled()
    expect(page.locator("#id_photos")).to_be_enabled()
    expect(page.locator("#id_submission_key")).to_have_value(key)
    assert page.locator("#id_photos").evaluate("input => input.files.length") == 1
    assert len(submissions) == 1
    for request in (choices[0], choices[-1]):
        request.abort()
    page.unroute("**/garden/plantings/varieties/**")
    page.unroute("**/garden/plantings/new/")
    page.locator("[data-variety-retry]").click()
    expect(page.locator("#id_variety option")).to_have_text(["Okänd sort", "Golden (Tomato)"])
    save(page)
    expect(page).to_have_url(re.compile(r"/garden/plantings/\d+/$"))
    expect(page.locator("h1")).to_contain_text("Synthetic overlapping choices")
    expect(page.locator(".record-fields")).to_contain_text("Preserve this draft and photo")
    expect(page.locator(".register-photos figure")).to_have_count(1)


def test_year_area_history_selects_shared_details_with_keyboard(page, live_server, user, catalog):
    login(page, live_server, user)
    page.goto(live_server.url + f"/garden/areas/{catalog['parent'].pk}/history/?year=2026")
    page.locator(f'[data-area-id="{catalog["parent"].pk}"] > details > summary').click()
    first, second = catalog["first"], catalog["second"]
    first_toggle = page.get_by_role("button", name="First batch", exact=True)
    second_toggle = page.get_by_role("button", name="Persistent batch", exact=True)
    first_details = page.locator(f"#details-planting-{first.pk}")
    second_details = page.locator(f"#details-planting-{second.pk}")
    expect(page.locator("#planting-results .record-details")).to_have_count(0)
    first_toggle.focus()
    first_toggle.press("Enter")
    expect(first_details).to_contain_text("Synthetic overwintered sowing")
    expect(first_details).to_contain_text("2025-12-20")
    expect(first_details.locator(".record-fields")).to_contain_text("Greenhouse / Bed")
    expect(first_toggle).to_be_focused()
    second_toggle.focus()
    second_toggle.press("Space")
    expect(second_details).to_contain_text("Synthetic cumulative note")
    expect(first_details).to_be_hidden()
    expect(first_toggle).to_have_attribute("aria-expanded", "false")
    expect(second_toggle).to_be_focused()
    expect(page.locator("[data-record-detail-pane] .register-expanded:visible")).to_have_count(1)
    first_toggle.click()
    expect(first_details).to_be_visible()
    expect(second_details).to_be_hidden()
    page.get_by_role("link", name="Nästa år", exact=True).click()
    expect(page.locator("h1")).to_contain_text("2027")
    expect(page.locator("#planting-results .empty-state")).to_contain_text(
        "Inga odlingar detta odlingsår"
    )
    page.get_by_role("link", name="Föregående år", exact=True).click()
    page.locator(f'[data-area-id="{catalog["parent"].pk}"] > details > summary').click()
    page.get_by_role("button", name="First batch", exact=True).click()
    page.locator(f"#details-planting-{first.pk} .record-fields").get_by_role(
        "link", name="Golden", exact=True
    ).click()
    expect(page).to_have_url(
        live_server.url + f"/garden/varieties/{catalog['variety'].pk}/history/?year=2026"
    )
    page.get_by_role("button", name="First batch", exact=True).click()
    expect(page.locator(f"#details-planting-{first.pk}")).to_contain_text(
        "Synthetic overwintered sowing"
    )


def test_all_years_area_links_keep_historical_rows_and_allow_year_switching(
    page, live_server, user, catalog
):
    login(page, live_server, user)
    page.goto(live_server.url + "/garden/plantings/history/")
    page.locator(f'[data-area-id="{catalog["parent"].pk}"] > details > summary').press("Enter")
    results = page.locator("#planting-results")
    expect(page.locator("#id_year")).to_have_value("")
    expect(results).to_contain_text("First batch")
    expect(results).to_contain_text("Persistent batch")
    page.get_by_role("button", name="First batch", exact=True).click()
    page.locator(f"#details-planting-{catalog['first'].pk}").get_by_role(
        "link", name="Greenhouse / Bed", exact=True
    ).click()
    expect(page).to_have_url(live_server.url + f"/garden/areas/{catalog['child'].pk}/history/")
    expect(page.locator("#id_year")).to_have_value("")
    expect(results).to_contain_text("First batch")
    expect(results).not_to_contain_text("Persistent batch")
    page.get_by_role("link", name="Årsöversikt", exact=True).click()
    open_filters(page)
    page.locator("#id_year").fill("2027")
    page.locator("#id_year").press("Enter")
    expect(results.locator(".empty-state")).to_contain_text("Inga odlingar matchar filtren.")
    page.locator(".year-navigation").get_by_role("link", name="Alla år", exact=True).click()
    expect(page.locator("#id_year")).to_have_value("")
    expect(results).to_contain_text("First batch")
    page.get_by_role("button", name="First batch", exact=True).click()
    page.locator(f"#details-planting-{catalog['first'].pk}").get_by_role(
        "link", name="Greenhouse / Bed", exact=True
    ).click()
    expect(page.locator("#id_year")).to_have_value("")
    expect(results).to_contain_text("First batch")


@pytest.mark.parametrize("javascript", [False, True])
def test_scoped_planting_chips_remove_year_and_boolean_choices_without_losing_scope(
    page, live_server, user, catalog, javascript
):
    login(page, live_server, user)
    context = page.context.browser.new_context(
        java_script_enabled=javascript,
        storage_state=page.context.storage_state(),
        viewport={"width": 390, "height": 844},
    )
    try:
        view = context.new_page()
        path = f"/garden/crops/{catalog['crop'].pk}/history/"
        view.goto(live_server.url + path + "?year=2026&unknown_variety=on")
        expect(view.locator("#id_year")).to_be_hidden()
        expect(view.locator(".filter-chip")).to_have_count(3)
        expect(view.locator(".filter-chips span.filter-chip")).to_contain_text("Tomato")
        results = view.locator("#planting-results")
        expect(results).not_to_contain_text("First batch")
        expect(results).to_contain_text("Persistent batch")
        view.locator("a.filter-chip").filter(has_text="2026").click()
        expect(view.locator("#id_year")).to_have_value("")
        expect(view.locator(".filter-chip")).to_have_count(2)
        expect(view.locator("#id_year")).to_be_hidden()
        view.locator("a.filter-chip").click()
        expect(view.locator(".filter-chip")).to_have_count(1)
        expect(results).to_contain_text("First batch")
        expect(results).to_contain_text("Persistent batch")
        assert view.url.startswith(live_server.url + path)
        expect(view.locator("#id_crop")).to_have_value(str(catalog["crop"].pk))
        if javascript:
            expect(view.locator("[data-result-focus]")).to_be_focused()
        view.go_back()
        expect(view.locator(".filter-chip")).to_have_count(2)
        expect(results).not_to_contain_text("First batch")
        # Initial invalid URLs reveal the form even without JavaScript.
        view.goto(live_server.url + path + "?year=10000")
        expect(view.locator("#id_year")).to_be_visible()
        expect(view.locator("#id_year")).to_have_attribute("aria-invalid", "true")
        expect(view.locator("#id_year_errors")).not_to_be_empty()
    finally:
        context.close()


def test_empty_scoped_history_action_restores_rows_and_preserves_scope(
    page, live_server, user, catalog
):
    login(page, live_server, user)
    path = f"/garden/areas/{catalog['child'].pk}/history/"
    page.goto(live_server.url + path + "?year=2027")
    empty = page.locator("#planting-results .empty-state")
    expect(page.locator("[data-filter-create]")).to_be_hidden()
    expect(empty.locator(".brand-mark")).to_be_visible()
    action = empty.get_by_role("link", name="Alla år", exact=True)
    expect(action).to_have_attribute("href", path)
    action.focus()
    action.press("Enter")
    expect(page).to_have_url(live_server.url + path)
    expect(page.locator("#id_year")).to_have_value("")
    expect(page.locator("#planting-results")).to_contain_text("First batch")
    expect(page.locator("#planting-results")).not_to_contain_text("Persistent batch")
    expect(empty).to_have_count(0)
    expect(page.locator("[data-filter-create]")).to_be_visible()
    expect(page.locator("[data-result-focus]")).to_be_focused()


def test_invalid_planting_retains_zero_text_photos_key_focus_and_can_retry(
    page, live_server, user, catalog, image_upload
):
    login(page, live_server, user)
    page.goto(live_server.url + f"/garden/plantings/new/?crop={catalog['crop'].pk}&year=2026")
    open_optional(page)
    page.locator("#id_label").fill("Retry batch")
    page.locator("#id_quantity").fill("0")
    page.locator("#id_evaluation").fill("Synthetic evaluation")
    page.locator("#id_photos").set_input_files(
        [image_file(image_upload), image_file(image_upload, "second.jpg")]
    )
    key = page.locator("#id_submission_key").input_value()
    page.locator("#register-form").evaluate("form => form.noValidate = true")
    page.locator("#id_lifespan").select_option("persistent")
    page.locator("#id_end_year").fill("2027")
    page.locator("#id_lifespan").select_option("annual")
    expect(page.locator("#id_end_year")).to_have_value("2027")
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
    open_optional(page)
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
    other.locator("#account-menu > summary").click()
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
    open_optional(plain)
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
    plain.goto(live_server.url + "/garden/plantings/history/")
    plain.locator("[data-filter-disclosure] > summary").click()
    plain.locator("#id_year").fill("2027")
    plain.locator("[data-filter-form]").get_by_role("button", name="Visa", exact=True).click()
    expect(plain.locator("#planting-results")).to_contain_text("Inga odlingar detta odlingsår")
    plain.goto(live_server.url + "/garden/plantings/history/?year=10000")
    expect(plain.locator("#id_year")).to_be_visible()
    expect(plain.locator("#id_year")).to_have_value("10000")
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
