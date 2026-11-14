"""Compact member details, disclosure interaction and retryable member loading."""

from datetime import date
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import pytest
from browser_helpers import browser_page, login
from django.urls import reverse
from playwright.sync_api import expect

from diary.services import create_entry
from garden.services import (
    create_area,
    create_crop,
    create_planting,
    create_planting_event,
    create_variety,
)
from garden.services.cycles import create_planting_category

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def detail_batch(user, image_upload):
    crop = create_crop(actor=user, name="Synthetic tomato").record
    amber = create_variety(actor=user, crop=crop, name="Amber").record
    ruby = create_variety(actor=user, crop=crop, name="Ruby").record
    area = create_area(actor=user, name="Synthetic bed").record
    category = create_planting_category(actor=user, name="Synthetic category").record
    first = create_planting(
        actor=user,
        crop=crop,
        variety=amber,
        area=area,
        start_year=2025,
        lifespan="persistent",
        label="Synthetic cycle",
        status="ongoing",
        started_on=date(2025, 3, 1),
        category_ids=[category.pk],
        quantity=0,
        notes="Amber notes",
        evaluation="Amber evaluation",
        uploads=[image_upload()],
    ).record
    second = create_planting(
        actor=user,
        crop=crop,
        variety=ruby,
        start_year=2025,
        lifespan="persistent",
        batch_planting=first,
        notes="Ruby notes",
        evaluation="Ruby evaluation",
        uploads=[image_upload()],
    ).record
    for day in (1, 2):
        create_planting_event(
            actor=user,
            planting=second,
            kind="sowing",
            planned_date=date(2025, 3, day),
            actual_date=date(2025, 4, day),
            note=f"Ruby event {day}",
        )
    for day in range(1, 8):
        create_entry(
            author=user,
            date=date(2026, 6, day),
            body=f"Ruby observation {day}",
            uploads=[],
            submission_key=uuid4(),
            links=[second],
        )
    return first, second, category


def open_cycle(page, live_server, first):
    path = (
        reverse("garden:planting-status", args=["ongoing"])
        + "?"
        + urlencode({"year": 2026, "variety": first.variety_id})
    )
    page.goto(live_server.url + path)
    page.locator(".planting-list .record-name").press("Enter")
    expect(page.locator(".planting-detail")).to_have_attribute(
        "data-selected-planting", str(first.pk)
    )
    return path


@pytest.mark.parametrize("language", ["sv", "en"])
@pytest.mark.parametrize("width", [320, 1280])
@pytest.mark.parametrize("javascript", [False, True])
def test_member_switcher_preserves_scope_counts_actions_and_disclosures(
    live_server, user, browser_endpoint, detail_batch, language, width, javascript, tmp_path
):
    first, second, category = detail_batch
    first_thumbnail = first.photos.get().thumbnail_url
    second_thumbnail = second.photos.get().thumbnail_url
    with browser_page(
        browser_endpoint,
        viewport={"width": width, "height": 844},
        java_script_enabled=javascript,
    ) as page:
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        login(page, live_server, user, language)
        path = open_cycle(page, live_server, first)
        original_url = page.url
        panel = page.locator(".planting-detail")
        expect(page.locator('.main-navigation [aria-current="page"]')).to_have_attribute(
            "href", "/garden/plantings/"
        )
        expect(panel.get_by_role("heading", name="Synthetic cycle", exact=True)).to_have_count(1)
        expect(panel.locator(".record-fields")).to_contain_text("Amber evaluation")
        expect(panel.locator(".planting-summary")).to_contain_text("Synthetic bed")
        quantity = (
            panel.locator(".record-fields dt")
            .filter(has_text="Antal" if language == "sv" else "Quantity")
            .locator("xpath=following-sibling::dd[1]")
        )
        expect(quantity).to_have_text("0")
        events = "Odlingshändelser" if language == "sv" else "Cultivation events"
        observations = "Dagboksobservationer" if language == "sv" else "Diary observations"
        expect(panel.get_by_role("heading", name=f"{events} (0)", exact=True)).to_be_visible()
        expect(panel.get_by_role("heading", name=f"{observations} (0)", exact=True)).to_be_visible()
        expect(panel.locator(".event-table")).to_have_count(0)
        expect(panel.locator(".register-photos img")).to_have_attribute("src", first_thumbnail)
        complete = panel.locator(".planting-actions .btn-primary")
        completion_url = urlsplit(complete.get_attribute("href"))
        assert completion_url.path == reverse("garden:cycle-complete", args=[first.cycle_id])
        assert parse_qs(completion_url.query)["next"] == [path]
        ruby = panel.locator(f'[data-planting-member="{second.pk}"]')
        ruby.press("Enter")
        expect(panel).to_have_attribute("data-selected-planting", str(second.pk))
        if javascript:
            expect(page).to_have_url(original_url)
            expect(ruby).to_be_focused()
        expect(ruby).to_have_attribute("aria-current", "true")
        expect(panel.locator(".record-fields")).to_contain_text("Ruby evaluation")
        expect(panel.locator(".record-fields")).not_to_contain_text("Amber evaluation")
        expect(quantity).to_have_text("—")
        expect(panel.locator(".record-fields")).to_contain_text(category.name)
        expect(panel.locator(".record-fields")).to_contain_text("2025-03-01")
        expect(panel.locator(".register-photos img")).to_have_attribute("src", second_thumbnail)
        expect(panel.locator(f'a[href^="{second.get_absolute_url()}edit/"]')).to_be_visible()
        expect(panel.locator(f'a[href="{second.get_absolute_url()}events/new/"]')).to_be_visible()
        expect(panel.get_by_role("heading", name=f"{events} (2)", exact=True)).to_be_visible()
        event_disclosure = panel.locator(".event-disclosure")
        expect(event_disclosure).not_to_have_attribute("open", "")
        expect(event_disclosure.locator("table")).to_be_hidden()
        event_disclosure.locator(":scope > summary").press("Space")
        expect(event_disclosure.locator("tbody tr")).to_have_count(2)
        expect(event_disclosure.locator("tbody")).to_contain_text("Ruby event 2")
        observation_disclosure = panel.locator(".observation-disclosure")
        expect(observation_disclosure.locator(":scope > summary")).to_have_text(
            f"{observations} (7)"
        )
        expect(observation_disclosure.locator(".observation-list")).to_be_hidden()
        observation_disclosure.locator(":scope > summary").press("Enter")
        expect(observation_disclosure.locator(".observation-list > li")).to_have_count(5)
        expect(observation_disclosure.locator('a[href^="/?planting="]')).to_have_attribute(
            "href", "/?planting=" + str(second.pk)
        )
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(tmp_path / "planting-details.png"), full_page=True)
        panel.locator(f'[data-planting-member="{first.pk}"]').press("Enter")
        expect(panel).to_have_attribute("data-selected-planting", str(first.pk))
        expect(panel.locator(".event-table")).to_have_count(0)
        expect(panel.locator(".observation-disclosure")).to_have_count(0)
        expect(panel.locator(".register-photos img")).to_have_attribute("src", first_thumbnail)
        if javascript:
            page.locator("[data-record-return]").click()
            expect(page.locator(".planting-list .record-name")).to_be_focused()
            expect(page).to_have_url(live_server.url + path)
        else:
            page.get_by_role(
                "link",
                name="Tillbaka till listan" if language == "sv" else "Back to list",
                exact=True,
            ).click()
            expect(page).to_have_url(live_server.url + path)
        assert errors == []


@pytest.mark.parametrize("failure", ["server", "network"])
def test_failed_member_load_keeps_the_selection_and_can_be_retried(
    live_server, user, browser_endpoint, detail_batch, failure
):
    first, second, _ = detail_batch
    with browser_page(browser_endpoint) as page:
        login(page, live_server, user, "en")
        open_cycle(page, live_server, first)
        panel = page.locator(".planting-detail")

        def fail(route):
            if failure == "server":
                route.fulfill(status=500, body="Synthetic failure")
            else:
                route.abort()

        page.route(f"**/cycles/{first.cycle_id}/?member={second.pk}&**", fail, times=1)
        switch = panel.locator(f'[data-planting-member="{second.pk}"]')
        switch.click()
        expect(panel.locator("[data-member-status]")).to_have_text(
            "This variety could not be loaded. Try again."
        )
        expect(panel).to_have_attribute("data-selected-planting", str(first.pk))
        expect(panel.locator(".record-fields")).to_contain_text("Amber notes")
        expect(panel.locator(f'[data-planting-member="{first.pk}"]')).to_have_attribute(
            "aria-current", "true"
        )
        expect(page.locator(".register-details[aria-busy]")).to_have_count(0)
        switch.click()
        expect(panel).to_have_attribute("data-selected-planting", str(second.pk))
        expect(panel.locator("[data-member-status]")).to_be_hidden()


def test_unknown_single_variety_has_no_switcher_and_keeps_start_available(
    live_server, user, browser_endpoint
):
    crop = create_crop(actor=user, name="Synthetic crop").record
    record = create_planting(actor=user, crop=crop, start_year=2026).record
    with browser_page(browser_endpoint) as page:
        login(page, live_server, user, "en")
        page.goto(live_server.url + record.get_absolute_url())
        panel = page.locator(".planting-detail")
        expect(panel.locator(".planting-switcher")).to_have_count(0)
        expect(panel.locator(".record-fields")).to_contain_text("Unknown variety")
        expect(panel.locator(".planting-summary")).to_contain_text("Unassigned area")
        expect(panel.locator(".planting-actions .btn-primary")).to_have_text("Start cycle")


@pytest.mark.parametrize("language", ["sv", "en"])
@pytest.mark.parametrize("width", [320, 1280])
@pytest.mark.parametrize("javascript", [False, True])
def test_existing_photos_are_maintained_from_the_planting_edit_form(
    live_server, user, image_upload, browser_endpoint, language, width, javascript, tmp_path
):
    crop = create_crop(actor=user, name="Synthetic crop").record
    record = create_planting(
        actor=user, crop=crop, start_year=2026, uploads=[image_upload()]
    ).record
    photo = record.photos.get()
    thumbnail = photo.thumbnail_url
    original_download = photo.original_url
    origin = reverse("garden:planting-status", args=["planned"], query={"year": 2026})
    edit_url = reverse("garden:planting-edit", args=[record.pk], query={"next": origin})
    edit_photo_path = reverse("garden:photo-edit", args=[photo.pk])
    delete_photo_path = reverse("garden:photo-delete", args=[photo.pk])
    with browser_page(
        browser_endpoint,
        viewport={"width": width, "height": 844},
        java_script_enabled=javascript,
    ) as page:
        login(page, live_server, user, language)
        page.goto(live_server.url + record.get_absolute_url())
        expect(page.locator(".register-photos img")).to_have_attribute("src", thumbnail)
        expect(page.locator(f'a[href^="{edit_photo_path}"]')).to_have_count(0)
        expect(page.locator(f'a[href^="{delete_photo_path}"]')).to_have_count(0)
        page.goto(live_server.url + edit_url)
        group = page.locator("#planting-notes-photos")
        expect(group).to_have_attribute("open", "")
        expect(group.locator(".register-photos img")).to_have_attribute("src", thumbnail)
        version = page.locator("#id_expected_updated_at").input_value()
        group.locator(".register-photos figure > a").first.click()
        expect(page.locator(".photo-primary-actions a")).to_have_attribute(
            "href", original_download
        )
        page.locator("main > a").click()
        expect(page).to_have_url(live_server.url + edit_url)
        group.locator(f'a[href^="{edit_photo_path}"]').click()
        page.locator("#id_caption").fill("Synthetic saved caption")
        page.locator("#id_position").fill("2")
        page.locator("main button[type=submit]").click()
        expect(page).to_have_url(live_server.url + edit_url)
        expect(group.locator("figcaption")).to_have_text("Synthetic saved caption")
        expect(page.locator("#id_expected_updated_at")).not_to_have_value(version)
        page.locator("#id_notes").fill("Synthetic note after photo editing")
        page.locator("#register-form").get_by_role(
            "button", name="Spara" if language == "sv" else "Save", exact=True
        ).click()
        expect(page).to_have_url(live_server.url + origin)
        page.locator(".planting-list .record-name").click()
        expect(page.locator(".register-photos figcaption")).to_have_text("Synthetic saved caption")
        expect(page.locator(f'a[href^="{edit_photo_path}"]')).to_have_count(0)
        page.goto(live_server.url + edit_url)
        group.locator(f'a[href^="{delete_photo_path}"]').click()
        page.get_by_role(
            "link", name="Avbryt" if language == "sv" else "Cancel", exact=True
        ).click()
        expect(page).to_have_url(live_server.url + edit_url)
        expect(group.locator(".register-photos figure")).to_have_count(1)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(tmp_path / "planting-photo-edit.png"), full_page=True)
        group.locator(f'a[href^="{delete_photo_path}"]').click()
        page.locator("#id_confirm").check()
        page.locator("main button[type=submit]").click()
        expect(page).to_have_url(live_server.url + edit_url)
        expect(group.locator(".register-photos figure")).to_have_count(0)
        expect(page.locator("#id_notes")).to_have_value("Synthetic note after photo editing")
    record.refresh_from_db()
    assert record.photos.count() == 0
    assert record.notes == "Synthetic note after photo editing"
