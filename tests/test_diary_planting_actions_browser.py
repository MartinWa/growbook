"""Real capture actions, dates, evaluations, drafts and native form fallbacks."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import close_old_connections
from playwright.sync_api import expect, sync_playwright

from diary.models import DiaryEntry
from garden.models import Planting, PlantingCycle, PlantingEvent
from garden.services import create_crop, create_planting, create_variety

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def catalog(user):
    crop = create_crop(actor=user, name="Tomato").record
    first = create_variety(actor=user, crop=crop, name="Golden").record
    second = create_variety(actor=user, crop=crop, name="Red").record
    one = create_planting(
        actor=user,
        crop=crop,
        variety=first,
        start_year=2024,
        lifespan="persistent",
        evaluation="First result",
    ).record
    two = create_planting(
        actor=user,
        crop=crop,
        variety=second,
        start_year=2025,
        batch_planting=one,
        evaluation="Second result",
    ).record
    return crop.pk, first.pk, one.pk, two.pk


@pytest.fixture
def page(browser_endpoint, live_server, catalog):
    with (
        sync_playwright() as playwright,
        playwright.chromium.connect(browser_endpoint) as browser,
        browser.new_context() as context,
    ):
        page = context.new_page()
        page.set_default_timeout(10000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        yield page
        assert errors == []


def login(page, live_server, user):
    page.context.add_cookies([{"name": "django_language", "value": "en", "url": live_server.url}])
    page.goto(live_server.url + "/accounts/login/")
    page.locator("#id_username").fill(user.username)
    page.locator("#id_password").fill("Synthetic-password-42")
    page.locator('.login-panel button[type="submit"]').click()
    expect(page).to_have_url(live_server.url + "/")


def state():
    def read():
        close_old_connections()
        try:
            return {
                "entries": DiaryEntry.objects.count(),
                "plantings": list(
                    Planting.objects.values("id", "start_year", "evaluation", "end_year")
                ),
                "events": list(PlantingEvent.objects.values("kind", "actual_date", "planned_date")),
                "statuses": list(PlantingCycle.objects.values_list("status", flat=True)),
                "diary_photos": list(DiaryEntry.objects.values_list("photos__original", flat=True)),
                "planting_photos": list(
                    Planting.objects.values_list("photos__original", flat=True)
                ),
            }
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(read).result()


def open_action(page, action):
    page.locator("[data-planting-action] summary").press("Enter")
    page.locator('[name="planting_action"]').select_option(action)


@pytest.mark.parametrize("language", ["sv", "en"])
@pytest.mark.parametrize("javascript", [False, True])
def test_planting_disclosure_supports_keyboard_and_reflows(
    browser_endpoint, live_server, user, catalog, language, javascript
):
    with (
        sync_playwright() as playwright,
        playwright.chromium.connect(browser_endpoint) as browser,
        browser.new_context(java_script_enabled=javascript) as context,
    ):
        page = context.new_page()
        login(page, live_server, user)
        context.add_cookies(
            [{"name": "django_language", "value": language, "url": live_server.url}]
        )
        page.reload()
        disclosure = page.locator("[data-planting-action]")
        summary = disclosure.locator(":scope > summary")
        selector = page.locator('[name="planting_action"]')
        expect(summary).to_have_accessible_name(
            "Koppla till en odling" if language == "sv" else "Link to a planting"
        )
        page.locator("#id_body").fill("Synthetic unfinished observation")
        for width in (320, 390, 1280):
            page.set_viewport_size({"width": width, "height": 844})
            box = summary.bounding_box()
            assert box["width"] >= 44 and box["height"] >= 44
            assert summary.evaluate("element => getComputedStyle(element).cursor") == "pointer"
            closed_arrow = summary.locator(".filter-arrow").evaluate(
                "element => getComputedStyle(element).transform"
            )
            expect(selector).to_be_hidden()
            summary.focus()
            summary.press("Enter")
            expect(selector).to_be_visible()
            assert (
                summary.locator(".filter-arrow").evaluate(
                    "element => getComputedStyle(element).transform"
                )
                != closed_arrow
            )
            summary.press("Tab")
            expect(selector).to_be_focused()
            summary.focus()
            summary.press("Space")
            expect(selector).to_be_hidden()
            expect(summary).to_be_focused()
            expect(page.locator("#id_body")).to_have_value("Synthetic unfinished observation")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("language", ["sv", "en"])
def test_new_dates_overrides_errors_photos_and_success_reset(
    page, live_server, user, catalog, image_upload, tmp_path, language
):
    login(page, live_server, user)
    page.context.add_cookies(
        [{"name": "django_language", "value": language, "url": live_server.url}]
    )
    page.reload()
    expect(page.locator('[name="event-kind"] option[value="replanting"]')).to_have_text(
        "Omplantering" if language == "sv" else "Replanting"
    )
    diary_button = page.get_by_role(
        "button", name="Lägg till bilder" if language == "sv" else "Add photos", exact=True
    )
    planting_button = page.get_by_role(
        "button",
        name="Lägg till bilder till odlingen" if language == "sv" else "Add planting photos",
        exact=True,
    )
    expect(page.locator("#capture-form [data-photo-chooser]:visible")).to_have_count(1)
    expect(diary_button).to_be_visible()
    expect(planting_button).to_be_hidden()
    expect(page.locator("[data-planting-action]")).not_to_have_attribute("open", "")
    open_action(page, "new")
    expect(page.locator("#capture-form [data-photo-chooser]:visible")).to_have_count(2)
    expect(planting_button).to_be_visible()
    expect(page.locator(".capture-tools [data-photo-chooser]")).to_have_count(1)
    expect(page.locator(".capture-actions [data-photo-chooser]")).to_have_count(0)
    expect(
        page.locator("[data-new-planting] [data-photo-picker] [data-photo-chooser]")
    ).to_be_visible()
    page.locator('[name="planting-variety"]').select_option(str(catalog[1]))
    expect(page.locator('[name="planting-crop"]')).to_have_value(str(catalog[0]))
    page.locator("#id_date").fill("2025-12-31")
    expect(page.locator('[name="planting-started_on"]')).to_have_value("2025-12-31")
    expect(page.locator('[name="planting-start_year"]')).to_have_value("2025")
    page.locator('[name="planting-started_on"]').fill("2025-11-01")
    page.locator("#id_date").fill("2026-01-02")
    expect(page.locator('[name="planting-started_on"]')).to_have_value("2025-11-01")
    expect(page.locator('[name="planting-start_year"]')).to_have_value("2026")
    photo = tmp_path / "synthetic.jpg"
    photo.write_bytes(image_upload().read())
    with page.expect_file_chooser() as diary_selection:
        diary_button.click()
    assert diary_selection.value.element.get_attribute("id") == "id_library"
    diary_selection.value.set_files(str(photo))
    planting_photo = tmp_path / "synthetic-planting.png"
    planting_photo.write_bytes(image_upload(format="PNG").read())
    with page.expect_file_chooser() as planting_selection:
        planting_button.click()
    assert planting_selection.value.element.get_attribute("id") == "id_planting-photos"
    planting_selection.value.set_files(str(planting_photo))
    page.locator('[name="planting_action"]').select_option("event")
    expect(planting_button).to_be_hidden()
    expect(page.locator(".capture-tools [data-photo-chooser]")).to_have_count(1)
    page.locator('[name="planting_action"]').select_option("new")
    expect(
        page.locator("[data-new-planting] [data-photo-picker] [data-photo-chooser]")
    ).to_be_visible()
    page.locator('[name="planting-end_year"]').fill("2025")
    page.locator("#save-entry").click()
    expect(page.locator("#capture-errors")).to_contain_text(
        "ettårig" if language == "sv" else "annual"
    )
    expect(page.locator("#photo-previews figure")).to_have_count(1)
    expect(page.locator("[data-new-planting] [data-photo-previews] figure")).to_have_count(1)
    expect(
        page.locator("[data-new-planting] [data-photo-picker] [data-photo-chooser]")
    ).to_be_visible()
    expect(page.locator('[name="planting-started_on"]')).to_have_value("2025-11-01")
    page.locator('[name="planting-end_year"]').fill("")
    page.locator("#save-entry").click()
    expect(page.locator("[data-capture-saved]")).to_be_visible()
    expect(page.locator('[name="planting_action"]')).to_have_value("")
    current = state()
    assert current["entries"] == 1 and len(current["plantings"]) == 3
    assert sum(bool(photo) for photo in current["diary_photos"]) == 1
    assert sum(bool(photo) for photo in current["planting_photos"]) == 1
    assert set(filter(None, current["diary_photos"])).isdisjoint(
        filter(None, current["planting_photos"])
    )
    expect(page.locator("#capture-form [data-photo-chooser]:visible")).to_have_count(1)


def test_cycle_load_draft_evaluations_date_defaults_and_reopen(page, live_server, user, catalog):
    login(page, live_server, user)
    page.locator("#id_body").fill("Completion observation")
    page.locator("#id_date").fill("2026-01-02")
    open_action(page, "cycle")
    page.locator('[name="action_planting"]').select_option(str(catalog[2]))
    expect(page.locator('[name="cycle-expected_updated_at"]')).not_to_have_value("")
    page.locator('[data-cycle-preset="complete"]').click()
    expect(page.locator('[name="cycle-completed_on"]')).to_have_value("2026-01-02")
    page.locator("#id_date").fill("2026-01-03")
    expect(page.locator('[name="cycle-completed_on"]')).to_have_value("2026-01-03")
    expect(page.locator(f'[name="cycle-evaluation_{catalog[2]}"]')).to_have_value("First result")
    page.locator(f'[name="cycle-evaluation_{catalog[2]}"]').fill("Better")
    page.locator(f'[name="cycle-evaluation_{catalog[3]}"]').fill("")
    page.locator("#save-entry").click()
    expect(page.locator("[data-capture-saved]")).to_be_visible()
    current = state()
    assert current["statuses"] == ["completed"]
    assert [p["evaluation"] for p in sorted(current["plantings"], key=lambda p: p["id"])] == [
        "Better",
        "",
    ]
    page.locator("#id_body").fill("Reopening")
    previous = page.locator("[data-capture-saved]").get_attribute("data-capture-saved")
    open_action(page, "cycle")
    page.locator('[name="action_planting"]').select_option(str(catalog[2]))
    expect(page.locator('[name="cycle-status"]')).to_have_value("completed")
    page.locator('[data-cycle-preset="reopen"]').click()
    page.locator("#save-entry").click()
    expect(page.locator("[data-capture-saved]")).not_to_have_attribute(
        "data-capture-saved", previous
    )
    assert state()["statuses"] == ["ongoing"]
    assert all(p["end_year"] is None for p in state()["plantings"])


def test_event_independent_dates_and_choice_loading_failure_preserves_draft(
    page, live_server, user, catalog
):
    login(page, live_server, user)
    page.locator("#id_body").fill("Event observation")
    open_action(page, "cycle")
    page.route("**/capture/", lambda route: route.fulfill(status=503, body="Unavailable"))
    page.locator('[name="action_planting"]').select_option(str(catalog[2]))
    expect(page.locator("[data-action-status]")).to_contain_text("could not be loaded")
    expect(page.locator("#id_body")).to_have_value("Event observation")
    expect(page.locator("#save-entry")).to_be_disabled()
    page.unroute("**/capture/")
    page.locator('[name="planting_action"]').select_option("event")
    page.locator('[name="event-kind"]').select_option("replanting")
    page.locator("#id_date").fill("2026-01-02")
    expect(page.locator('[name="event-actual_date"]')).to_have_value("2026-01-02")
    page.locator('[name="event-planned_date"]').fill("2025-12-31")
    page.locator("#save-entry").click()
    expect(page.locator("[data-capture-saved]")).to_be_visible()
    assert state()["statuses"] == ["planned"]
    assert state()["events"][0]["planned_date"].isoformat() == "2025-12-31"
    assert state()["events"][0]["kind"] == "replanting"


@pytest.mark.parametrize("action", ["new", "event", "cycle"])
def test_no_javascript_native_planting_actions(
    browser_endpoint, live_server, user, catalog, action
):
    with (
        sync_playwright() as playwright,
        playwright.chromium.connect(browser_endpoint) as browser,
        browser.new_context(java_script_enabled=False) as context,
    ):
        page = context.new_page()
        login(page, live_server, user)
        page.locator("#id_body").fill("Native completion")
        open_action(page, action)
        if action == "new":
            page.locator('[name="planting-variety"]').select_option(str(catalog[1]))
        elif action == "event":
            page.locator('[name="action_planting"]').select_option(str(catalog[2]))
            page.locator('[name="event-kind"]').select_option("replanting")
        else:
            page.locator('[name="action_planting"]').select_option(str(catalog[2]))
            page.locator('[name="choose_action"]').click()
            expect(page.locator("#id_body")).to_have_value("Native completion")
            page.locator('[name="cycle-status"]').select_option("completed")
            page.locator('[name="cycle-completed_on"]').fill("2026-01-02")
        with page.expect_navigation():
            page.locator("#save-entry").click()
        assert not page.locator("#capture-errors").inner_text()
        expect(page.locator("article[id^='entry-']")).to_have_count(1)
        current = state()
        assert current["entries"] == 1
        if action == "new":
            assert len(current["plantings"]) == 3
        elif action == "event":
            assert len(current["events"]) == 1
            assert current["events"][0]["kind"] == "replanting"
        else:
            assert current["statuses"] == ["completed"]


def test_lost_action_response_retry_runs_once(page, live_server, user, catalog):
    login(page, live_server, user)
    page.locator("#id_body").fill("Lost response observation")
    open_action(page, "new")
    page.locator('[name="planting-crop"]').select_option(str(catalog[0]))
    original_key = page.locator('[name="submission_key"]').input_value()

    def lose_response(route):
        response = route.fetch()
        assert response.status == 200
        route.fulfill(status=503, body="Synthetic lost response")

    page.route("**/capture/", lose_response)
    page.locator("#save-entry").click()
    expect(page.locator("#capture-errors")).to_contain_text("Synthetic lost response")
    expect(page.locator('[name="submission_key"]')).to_have_value(original_key)
    page.unroute("**/capture/")
    page.locator("#save-entry").click()
    expect(page.locator("[data-capture-saved]")).to_contain_text("already saved")
    assert state()["entries"] == 1 and len(state()["plantings"]) == 3
