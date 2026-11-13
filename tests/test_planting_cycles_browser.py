"""Real category navigation, shared transitions, correction and deletion in Chromium."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import close_old_connections
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from core.writes import validated_write
from garden.models import CycleSubmission, PlantingCycle
from garden.services import create_crop, create_planting, create_variety
from garden.services.cycles import create_planting_category

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def cycle(user):
    crop = create_crop(actor=user, name="Tomato").record
    first = create_variety(actor=user, crop=crop, name="Golden").record
    second = create_variety(actor=user, crop=crop, name="Red").record
    one = create_planting(
        actor=user,
        crop=crop,
        variety=first,
        start_year=2025,
        lifespan="persistent",
        label="Category trial",
    ).record
    create_planting(
        actor=user,
        crop=crop,
        variety=second,
        start_year=2025,
        lifespan="persistent",
        batch_planting=one,
    )
    return one.cycle_id


@pytest.fixture
def page(browser_endpoint, live_server, user, cycle):
    with (
        sync_playwright() as playwright,
        playwright.chromium.connect(browser_endpoint) as browser,
        browser.new_context() as context,
    ):
        page = context.new_page()
        page.set_default_timeout(10000)
        yield page


def login(page, live_server, user, language="en"):
    page.context.add_cookies(
        [{"name": "django_language", "value": language, "url": live_server.url}]
    )
    page.goto(live_server.url + "/accounts/login/")
    page.locator("#id_username").fill(user.username)
    page.locator("#id_password").fill("Synthetic-password-42")
    page.locator('.login-panel button[type="submit"]').click()
    expect(page).to_have_url(live_server.url + "/")


def save(page):
    page.locator('#register-form button[type="submit"]:not([name="choose_crop"])').click()


def stored_cycle(pk):
    def read():
        close_old_connections()
        try:
            shared = PlantingCycle.objects.get(pk=pk)
            return {
                "status": shared.status,
                "end_years": list(shared.members.values_list("end_year", flat=True)),
                "categories": shared.categories.count(),
                "receipts": CycleSubmission.objects.count(),
            }
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(read).result()


@pytest.mark.parametrize("language", ["en", "sv"])
@pytest.mark.parametrize("width", [390, 1280])
def test_categories_batch_actions_edit_correction_and_safe_deletion(
    page, live_server, user, cycle, language, width
):
    page.set_viewport_size({"width": width, "height": 844})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, live_server, user, language)
    root = live_server.url + "/garden/plantings/?year=2026"
    page.goto(root)
    expect(page.locator(".category-card")).to_have_count(3)
    for status in PlantingCycle.Status.values:
        source = f"/static/images/registers/{status}.webp"
        card = page.locator(f'.category-card-link[href*="/status/{status}/"]')
        image = card.locator("img")
        expect(image).to_have_attribute("src", source)
        expect(image).to_have_attribute("alt", "")
        image.evaluate("image => image.decode()")
        assert image.evaluate("image => [image.naturalWidth, image.naturalHeight]") == [1000, 500]
        card.press("Enter")
        heading_image = page.locator(".register-illustration")
        expect(heading_image).to_have_attribute("src", source)
        expect(heading_image).to_have_attribute("alt", "")
        heading_image.evaluate("image => image.decode()")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.goto(root)
    page.get_by_role(
        "link", name="New category" if language == "en" else "Ny kategori", exact=True
    ).click()
    page.locator("#id_name").fill("For Sale")
    save(page)
    expect(page).to_have_url(root)
    planned = "Planned" if language == "en" else "Planerad"
    page.locator(".category-card-link").filter(has_text=planned).press("Enter")
    expect(page.locator(".planting-list > li")).to_have_count(1)
    toggle = page.get_by_role("button", name="Category trial", exact=True)
    toggle.press("Enter")
    pane = page.locator(f"#details-cycle-{cycle}")
    pane.get_by_role(
        "link", name="Edit cycle" if language == "en" else "Redigera odlingsomgång", exact=True
    ).click()
    page.get_by_label("For Sale", exact=True).check()
    save(page)
    toggle.click()
    pane.get_by_role(
        "link", name="Start cycle" if language == "en" else "Starta odlingsomgång", exact=True
    ).click()
    expect(page.locator("#id_date")).to_have_value(timezone.localdate().isoformat())
    page.locator("#id_date").fill("2025-03-01")
    save(page)
    expect(page.locator(".planting-list > li")).to_have_count(0)
    expect(
        page.get_by_role(
            "link", name="Open record" if language == "en" else "Öppna post", exact=True
        )
    ).to_be_visible()
    page.goto(root)
    expect(page.locator(".category-card").filter(has_text="For Sale")).to_contain_text("1 · 2026")
    page.locator(".category-card-link").filter(has_text="For Sale").click()
    category_url = page.url
    toggle = page.get_by_role("button", name="Category trial", exact=True)
    toggle.click()
    pane.get_by_role(
        "link", name="Complete cycle" if language == "en" else "Avsluta odlingsomgång", exact=True
    ).click()
    page.locator("#id_date").fill("2027-09-01")
    save(page)
    expect(page.locator(".planting-list > li")).to_have_count(1)
    expect(page.locator(".planting-list")).to_contain_text("2025–2027")
    toggle.click()
    pane.get_by_role(
        "link", name="Edit cycle" if language == "en" else "Redigera odlingsomgång", exact=True
    ).click()
    page.locator("#id_status").select_option("ongoing")
    expect(page.locator("#id_completed_on")).to_be_hidden()
    save(page)
    expect(page).to_have_url(category_url)
    expect(page.locator(".planting-list > li")).to_have_count(1)
    page.goto(root)
    card = page.locator(".category-card").filter(has_text="For Sale")
    card.locator('a[href*="/delete/"]').click()
    page.locator("#id_confirm").check()
    page.locator('.register-form-panel button[type="submit"]').click()
    expect(page.locator(".category-card")).to_have_count(3)
    shared = stored_cycle(cycle)
    assert shared["status"] == "ongoing" and shared["end_years"] == [None, None]
    assert shared["categories"] == 0
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert errors == []


def test_lost_transition_response_retries_same_action_without_duplicates(
    page, live_server, user, cycle
):
    login(page, live_server, user)
    page.goto(live_server.url + f"/garden/plantings/cycles/{cycle}/start/")
    key = page.locator("#id_submission_key").input_value()

    def lose_response(route):
        route.fetch()
        route.abort()

    page.route(f"**/cycles/{cycle}/start/", lose_response, times=1)
    save(page)
    expect(page.locator("#register-errors")).to_contain_text("The connection failed")
    expect(page.locator("#id_submission_key")).to_have_value(key)
    save(page)
    expect(page).to_have_url(live_server.url + f"/garden/plantings/cycles/{cycle}/")
    assert stored_cycle(cycle)["receipts"] == 1
    assert stored_cycle(cycle)["status"] == "ongoing"


@pytest.mark.parametrize("status", ["planned", "ongoing", "completed"])
@pytest.mark.parametrize("javascript", [False, True])
def test_create_from_custom_category_keeps_initial_status_and_membership(
    page, live_server, user, status, javascript
):
    login(page, live_server, user)
    context = page.context.browser.new_context(
        java_script_enabled=javascript, storage_state=page.context.storage_state()
    )
    try:
        creation = context.new_page()
        creation.goto(live_server.url + "/garden/plantings/?year=2026")
        creation.get_by_role("link", name="New category", exact=True).click()
        creation.locator("#id_name").fill("For Sale")
        save(creation)
        creation.locator(".category-card-link").filter(has_text="For Sale").click()
        category_url = creation.url
        creation.locator("[data-filter-create]").click()
        expect(creation.locator("#id_status")).to_have_value("planned")
        expect(creation.get_by_label("For Sale", exact=True)).to_be_checked()
        creation.locator("#id_crop").select_option(label="Tomato")
        creation.locator("#id_label").fill("New category cycle")
        creation.locator("#id_status").select_option(status)
        if status != "planned":
            creation.locator("#id_started_on").fill("2026-03-01")
        if status == "completed":
            creation.locator("#id_completed_on").fill("2026-09-01")
        save(creation)
        expect(creation).to_have_url(category_url)
        expect(creation.locator(".planting-list > li")).to_have_count(1)
        creation.get_by_role(
            "button" if javascript else "link", name="New category cycle", exact=True
        ).click()
        details = creation.locator(".record-details").first
        expect(details).to_contain_text(status.capitalize())
        expect(details).to_contain_text("For Sale")
    finally:
        context.close()


def test_category_navigation_and_date_form_without_javascript(page, live_server, user, cycle):
    login(page, live_server, user)
    context = page.context.browser.new_context(
        java_script_enabled=False, storage_state=page.context.storage_state()
    )
    try:
        plain = context.new_page()
        plain.goto(live_server.url + "/garden/plantings/?year=2026")
        expect(
            plain.locator('.category-card-link[href*="/status/planned/"] img')
        ).to_have_attribute("src", "/static/images/registers/planned.webp")
        plain.locator(".category-card-link").filter(has_text="Planned").click()
        expect(plain.locator(".register-illustration")).to_have_attribute(
            "src", "/static/images/registers/planned.webp"
        )
        plain.get_by_role("link", name="Category trial", exact=True).click()
        expect(plain.locator("#id_status")).to_have_count(0)
        plain.get_by_role("link", name="Start cycle", exact=True).click()
        plain.locator("#id_date").fill("2025-03-01")
        save(plain)
        assert stored_cycle(cycle)["status"] == "ongoing"
    finally:
        context.close()


@pytest.mark.parametrize("scope", ["planting", "crop"])
@pytest.mark.parametrize("javascript", [False, True])
def test_all_years_cards_and_category_return_keep_historical_annuals(
    browser_endpoint, live_server, user, scope, javascript
):
    crop = create_crop(actor=user, name="Tomato").record
    category = create_planting_category(actor=user, name="For Sale").record
    create_planting(
        actor=user,
        crop=crop,
        start_year=timezone.localdate().year - 2,
        label="Historical annual",
        category_ids=[category.pk],
    )
    path = (
        "/garden/plantings/history/" if scope == "planting" else f"/garden/crops/{crop.pk}/history/"
    )
    with (
        sync_playwright() as playwright,
        playwright.chromium.connect(browser_endpoint) as browser,
        browser.new_context(java_script_enabled=javascript) as context,
    ):
        page = context.new_page()
        login(page, live_server, user)
        page.goto(live_server.url + path)
        if scope == "crop":
            page.get_by_role("link", name="Planting categories", exact=True).click()
        planned = page.locator(".category-card-link").filter(has_text="Planned")
        expect(planned).to_contain_text("1 · All years")
        planned.click()
        expect(page.locator(".result-count")).to_contain_text("1 planting cycle · All years")
        expect(page.locator(".planting-list")).to_contain_text("Historical annual")
        page.get_by_role("link", name="Planting categories", exact=True).click()
        custom = page.locator(".category-card-link").filter(has_text="For Sale")
        expect(custom).to_contain_text("1 · All years")
        custom.click()
        expect(page.locator(".result-count")).to_contain_text("1 planting cycle · All years")
        expect(page.locator(".planting-list")).to_contain_text("Historical annual")


def test_lost_unknown_start_edit_retries_after_another_tab_completes_cycle(
    browser_endpoint, live_server, user
):
    crop = create_crop(actor=user, name="Tomato").record
    category = create_planting_category(actor=user, name="For Sale").record
    member = create_planting(actor=user, crop=crop, start_year=2025, lifespan="persistent").record
    cycle = member.cycle_id
    with validated_write():
        PlantingCycle.objects.filter(pk=cycle).update(status="ongoing")
    receipts = CycleSubmission.objects.count()
    with (
        sync_playwright() as playwright,
        playwright.chromium.connect(browser_endpoint) as browser,
        browser.new_context() as context,
    ):
        page = context.new_page()
        login(page, live_server, user)
        page.goto(live_server.url + f"/garden/plantings/cycles/{cycle}/edit/")
        expect(page.locator("#id_started_on")).to_have_value("")
        page.get_by_label(category.name, exact=True).check()
        key = page.locator("#id_submission_key").input_value()

        def lose_response(route):
            route.fetch()
            route.abort()

        page.route(f"**/cycles/{cycle}/edit/", lose_response, times=1)
        save(page)
        expect(page.locator("#register-errors")).to_contain_text("The connection failed")
        other = context.new_page()
        other.goto(live_server.url + f"/garden/plantings/cycles/{cycle}/complete/")
        other.locator("#id_date").fill("2027-09-01")
        save(other)
        expect(other).to_have_url(live_server.url + f"/garden/plantings/cycles/{cycle}/")
        expect(page.locator("#id_submission_key")).to_have_value(key)
        save(page)
        expect(page).to_have_url(live_server.url + f"/garden/plantings/cycles/{cycle}/")
        expect(page.get_by_role("status")).to_contain_text("Record already saved.")
        shared = stored_cycle(cycle)
        assert shared["receipts"] == receipts + 2 and shared["status"] == "completed"
        assert shared["categories"] == 1 and shared["end_years"] == [2027]
