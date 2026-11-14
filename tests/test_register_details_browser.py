"""Shared details remain reachable in long lists at phone and desktop widths."""

import pytest
from browser_helpers import browser_page, login
from django.urls import reverse
from playwright.sync_api import expect

from garden.services import create_crop, create_planting, create_variety

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.fixture(params=["variety", "planting"])
def long_list(request, user):
    kind = request.param
    disclosure = None
    records = []
    crop = create_crop(actor=user, name="Synthetic crop").record
    if kind == "variety":
        disclosure = f"crop-group-{crop.pk}"
    for index in range(30):
        name = f"Synthetic item {index:02}"
        note = f"Synthetic details for item {index:02}."
        if kind == "variety":
            record = create_variety(actor=user, name=name, crop=crop, description=note).record
        else:
            record = create_planting(
                actor=user, crop=crop, start_year=2026, label=name, notes=note
            ).record
        records.append(record)
    return {
        "path": (
            reverse("garden:all-crops")
            if kind == "variety"
            else reverse("garden:planting-status", args=["planned"])
        )
        + ("?year=2026" if kind == "planting" else ""),
        "kind": kind,
        "records": records,
        "disclosure": disclosure,
    }


@pytest.fixture
def page(live_server, user, long_list, browser_endpoint):
    with browser_page(browser_endpoint, viewport={"width": 1280, "height": 844}) as page:
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        login(page, live_server, user, "en")
        yield page
        assert errors == []


@pytest.mark.parametrize("width", [390, 1280])
def test_long_lists_reveal_selected_details_and_return_to_the_selected_name(
    page, live_server, long_list, width
):
    page.set_viewport_size({"width": width, "height": 844})
    page.goto(live_server.url + long_list["path"])
    disclosure = long_list["disclosure"]
    if disclosure:
        page.locator(f"#{disclosure} > summary").press("Enter")
    return_button = page.get_by_role("button", name="Back to list", exact=True)
    expect(return_button).to_be_hidden()
    for index in (0, 29):
        record = long_list["records"][index]
        toggle = page.get_by_role("button", name=f"Synthetic item {index:02}", exact=True)
        toggle.scroll_into_view_if_needed()
        toggle.focus()
        toggle.press("Enter")
        kind = "variety" if long_list["kind"] == "variety" else "cycle"
        pk = record.pk if kind == "variety" else record.cycle_id
        details = page.locator(f"#details-{kind}-{pk} .register-details")
        expect(details).to_contain_text(f"Synthetic details for item {index:02}.")
        expect(details.locator("h2").first).to_be_in_viewport()
        if (width < 900 and index == 0) or (width >= 900 and index == 29):
            expect(details).to_be_focused()
        else:
            expect(toggle).to_be_focused()
        if index == 29 and disclosure:
            page.locator(f"#{disclosure} > summary").press("Enter")
            expect(page.locator(f"#{disclosure}")).not_to_have_attribute("open", "")
        expect(return_button).to_be_visible()
        return_button.click()
        expect(toggle).to_be_focused()
        expect(toggle).to_be_in_viewport()
        expect(toggle).to_have_attribute("aria-expanded", "true")
        if disclosure:
            expect(page.locator(f"#{disclosure}")).to_have_attribute("open", "")
    toggle.press("Enter")
    expect(details).to_be_hidden()
    expect(return_button).to_be_hidden()
    expect(page.locator("[data-record-placeholder]")).to_be_visible()
