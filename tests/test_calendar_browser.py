"""Calendar interaction: the panel, filters, navigation and both layouts."""

import re
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from browser_helpers import browser_page, login
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from diary.services import create_entry
from garden.services import (
    create_area,
    create_crop,
    create_planting,
    create_planting_event,
)

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: date(2026, 5, 9))


@pytest.fixture
def garden(user):
    crop = create_crop(actor=user, name="Synthetic tomato").record
    area = create_area(actor=user, name="Synthetic bed").record
    other = create_area(actor=user, name="Synthetic far bed").record
    planting = create_planting(
        actor=user, crop=crop, start_year=2026, area=area, label="Synthetic batch"
    ).record
    far = create_planting(actor=user, crop=crop, start_year=2026, area=other).record
    for kind in ("sowing", "transplant", "replanting", "harvest_start"):
        create_planting_event(
            actor=user, planting=planting, kind=kind, planned_date=date(2026, 5, 10)
        )
    create_planting_event(actor=user, planting=far, kind="sowing", planned_date=date(2026, 5, 20))
    entry = create_entry(
        author=user,
        date=date(2026, 5, 9),
        title="Synthetic panel observation",
        body="Synthetic panel note",
        uploads=(),
        submission_key=uuid4(),
        show_in_calendar=True,
    ).entry
    return SimpleNamespace(crop=crop, area=area, other=other, planting=planting, entry=entry)


def month_url():
    return reverse("planner:month", args=[2026, 5])


def test_an_item_opens_the_panel_and_escape_returns_focus(
    browser_endpoint, live_server, user, garden
):
    with browser_page(browser_endpoint, viewport={"width": 1280, "height": 900}) as page:
        login(page, live_server, user, "en")
        page.goto(live_server.url + month_url())
        link = page.locator(f"#calendar-item-entry-{garden.entry.pk}")
        link.focus()
        page.keyboard.press("Enter")
        panel = page.locator("dialog[data-calendar-panel]")
        expect(panel).to_be_visible()
        expect(page.locator("#calendar-panel-title")).to_be_focused()
        expect(page.locator("#calendar-panel-title")).to_have_text("Synthetic panel observation")
        expect(panel.locator(".calendar-panel-note")).to_contain_text("Synthetic panel note")
        expect(panel.get_by_role("link", name="Open observation")).to_have_attribute(
            "href", garden.entry.get_absolute_url()
        )
        assert "item=entry-" in page.url
        page.keyboard.press("Escape")
        expect(panel).to_be_hidden()
        expect(page).not_to_have_url(re.compile(r"item="))
        expect(link).to_be_focused()


def test_more_opens_the_whole_day_and_returns_focus(browser_endpoint, live_server, user, garden):
    with browser_page(browser_endpoint, viewport={"width": 1280, "height": 900}) as page:
        login(page, live_server, user, "en")
        page.goto(live_server.url + month_url())
        more = page.locator("#calendar-more-10")
        expect(more).to_have_text("+1 more")
        more.click()
        panel = page.locator("dialog[data-calendar-panel]")
        expect(panel).to_be_visible()
        expect(panel.locator(".calendar-panel-items .calendar-item-title")).to_have_count(4)
        expect(page.locator("#calendar-panel-title")).to_have_text("Everything on this day")
        assert "day=10" in page.url
        page.locator("[data-calendar-close]").click()
        expect(panel).to_be_hidden()
        expect(page).not_to_have_url(re.compile(r"day="))
        expect(more).to_be_focused()


def test_tab_order_reaches_the_items_and_the_panel(browser_endpoint, live_server, user, garden):
    with browser_page(browser_endpoint, viewport={"width": 1280, "height": 900}) as page:
        login(page, live_server, user, "en")
        page.goto(live_server.url + month_url())
        first = page.locator(f"#calendar-item-entry-{garden.entry.pk}")
        first.focus()
        page.keyboard.press("Tab")
        assert (
            page.evaluate("document.activeElement.closest('[data-date]')?.dataset.date")
            == "2026-05-10"
        )
        page.keyboard.press("Enter")
        expect(page.locator("dialog[data-calendar-panel]")).to_be_visible()
        page.keyboard.press("Tab")
        expect(page.locator("[data-calendar-close]")).to_be_focused()


def test_filters_apply_and_survive_navigation_and_back(browser_endpoint, live_server, user, garden):
    with browser_page(browser_endpoint, viewport={"width": 1280, "height": 900}) as page:
        login(page, live_server, user, "en")
        page.goto(live_server.url + month_url())
        page.locator("[data-filter-disclosure] > summary").click()
        page.get_by_role("checkbox", name="Planting events").check()
        expect(page).to_have_url(re.compile(r"type=event"))
        expect(page.locator(".calendar-grid .calendar-item")).to_have_count(4)
        expect(page.locator(f"#calendar-item-entry-{garden.entry.pk}")).to_have_count(0)
        page.get_by_role("combobox", name="Growing area").select_option(str(garden.other.pk))
        expect(page.locator(".calendar-grid .calendar-item")).to_have_count(1)
        expect(page).to_have_url(re.compile(rf"area={garden.other.pk}"))
        page.locator('[rel="next"]').click()
        expect(page).to_have_url(re.compile(r"/calendar/2026/6/"))
        assert "type=event" in page.url
        assert f"area={garden.other.pk}" in page.url
        page.go_back()
        expect(page).to_have_url(re.compile(r"/calendar/2026/5/"))
        expect(page.locator(".calendar-grid .calendar-item")).to_have_count(1)
        page.locator("[data-filter-disclosure] > summary").click()
        expect(page.get_by_role("checkbox", name="Planting events")).to_be_checked()
        area = page.get_by_role("combobox", name="Growing area")
        expect(area).to_have_value(str(garden.other.pk))


def test_the_month_stacks_on_a_narrow_screen(browser_endpoint, live_server, user, garden):
    with browser_page(browser_endpoint, viewport={"width": 390, "height": 844}) as page:
        login(page, live_server, user, "en")
        page.goto(live_server.url + month_url())
        expect(page.locator(".calendar-grid thead")).to_be_hidden()
        expect(page.locator(".calendar-items .calendar-item-title").first).to_be_visible()
        expect(page.locator(".calendar-side")).to_be_visible()
    with browser_page(browser_endpoint, viewport={"width": 1280, "height": 900}) as page:
        login(page, live_server, user, "en")
        page.goto(live_server.url + month_url())
        expect(page.locator(".calendar-grid thead")).to_be_visible()
        expect(page.locator(".calendar-grid caption")).to_be_visible()


def test_removing_a_filter_chip_keeps_focus_in_the_results(
    browser_endpoint, live_server, user, garden
):
    with browser_page(browser_endpoint, viewport={"width": 1280, "height": 900}) as page:
        login(page, live_server, user, "en")
        page.goto(live_server.url + month_url() + "?type=event")
        page.get_by_role("link", name=re.compile("Remove filter: Item type")).click()
        expect(page).to_have_url(re.compile(r"/calendar/2026/5/$"))
        expect(page.locator("caption[data-result-focus]")).to_be_focused()


def test_a_dismissed_panel_does_not_return_after_a_language_change(
    browser_endpoint, live_server, user, garden
):
    with browser_page(browser_endpoint, viewport={"width": 1280, "height": 900}) as page:
        login(page, live_server, user, "en")
        page.goto(live_server.url + month_url())
        page.locator(f"#calendar-item-entry-{garden.entry.pk}").click()
        panel = page.locator("dialog[data-calendar-panel]")
        expect(panel).to_be_visible()
        page.keyboard.press("Escape")
        expect(panel).to_be_hidden()
        page.locator("#account-menu > summary").click()
        # Choosing a language submits the form and returns to the page it names.
        page.locator("#language").select_option("sv")
        expect(page).to_have_url(re.compile(r"/calendar/2026/5/$"))
        assert "item=" not in page.url
        expect(page.locator("dialog[data-calendar-panel]")).to_have_count(0)


def test_a_task_can_be_added_and_ticked_off_from_the_month(browser_endpoint, live_server, user):
    with browser_page(browser_endpoint, viewport={"width": 1280, "height": 900}) as page:
        login(page, live_server, user, "en")
        page.goto(live_server.url + month_url())
        page.get_by_role("link", name="New task").click()
        page.locator("input[name=planned_date]").fill("2026-05-14")
        page.locator("input[name=title]").fill("Synthetic browser task")
        page.get_by_role("button", name="Save task").click()
        expect(page).to_have_url(re.compile(r"/calendar/2026/5/\?item=task-\d+$"))
        panel = page.locator("dialog[data-calendar-panel]")
        expect(panel).to_be_visible()
        expect(page.locator("#calendar-panel-title")).to_have_text("Synthetic browser task")
        task_id = re.search(r"item=task-(\d+)", page.url).group(1)
        panel.get_by_role("button", name="Mark done").click()
        expect(panel).to_be_visible()
        expect(panel).to_contain_text("Planned for May 14, 2026")
        done_day = page.locator('.calendar-day[data-date="2026-05-09"]')
        expect(done_day).to_contain_text("Done")
        expect(done_day.locator(f"#calendar-item-task-{task_id}")).to_have_count(1)


def test_a_task_panel_states_its_controls_and_keeps_focus_order(
    browser_endpoint, live_server, user
):
    from planner import services

    task = services.create_task(
        actor=user, planned_date=date(2026, 5, 12), title="Synthetic focus task"
    ).task
    with browser_page(browser_endpoint, viewport={"width": 1280, "height": 900}) as page:
        login(page, live_server, user, "en")
        page.goto(live_server.url + month_url())
        link = page.locator(f"#calendar-item-task-{task.pk}")
        link.focus()
        page.keyboard.press("Enter")
        panel = page.locator("dialog[data-calendar-panel]")
        expect(panel).to_be_visible()
        expect(page.locator("#calendar-panel-title")).to_be_focused()
        page.keyboard.press("Tab")
        expect(page.locator("[data-calendar-close]")).to_be_focused()
        page.keyboard.press("Escape")
        expect(panel).to_be_hidden()
        expect(link).to_be_focused()


def test_the_month_vignette_is_decorative_and_desktop_only(browser_endpoint, live_server, user):
    with browser_page(browser_endpoint, viewport={"width": 1280, "height": 900}) as page:
        login(page, live_server, user, "en")
        page.goto(live_server.url + month_url())
        vignette = page.locator(".calendar-vignette")
        expect(vignette).to_be_visible()
        expect(vignette).to_have_attribute("alt", "")
        expect(vignette).to_have_attribute("aria-hidden", "true")
        assert vignette.evaluate("image => image.naturalWidth") == 720
        expect(page.locator(".calendar-heading-side .btn-primary")).to_be_visible()
    with browser_page(browser_endpoint, viewport={"width": 390, "height": 844}) as page:
        login(page, live_server, user, "en")
        page.goto(live_server.url + month_url())
        expect(page.locator(".calendar-vignette")).to_be_hidden()
