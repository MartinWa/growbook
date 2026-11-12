"""Card navigation, linked content and area forms in the container's Chromium."""

from datetime import date
from uuid import uuid4

import pytest
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from diary.services import create_entry
from garden.services import archive_area, create_area, create_crop, create_planting

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def areas(user, image_upload):
    root = create_area(
        actor=user, name="Main garden", notes="Synthetic parent notes", uploads=[image_upload()]
    ).record
    child = create_area(
        actor=user,
        name="Child bed",
        parent=root,
        area_type="Synthetic custom type",
        notes="Synthetic bed notes",
        uploads=[image_upload(), image_upload()],
    ).record
    retired = create_area(actor=user, name="Retired bed", parent=root).record
    crop = create_crop(actor=user, name="Synthetic crop").record
    year = timezone.localdate().year
    planting = create_planting(
        actor=user, crop=crop, area=child, start_year=year, label="Synthetic batch"
    ).record
    grandchild = create_area(actor=user, name="Inner section", parent=child).record
    create_planting(
        actor=user, crop=crop, area=grandchild, start_year=year, label="Inner section batch"
    )
    observation = create_entry(
        author=user,
        date=date(2026, 5, 2),
        body="Synthetic linked observation",
        uploads=[],
        submission_key=uuid4(),
        links=[child, planting],
    ).entry
    archive_area(actor=user, pk=retired.pk)
    return {
        "root": root,
        "child": child,
        "grandchild": grandchild,
        "retired": retired,
        "planting": planting,
        "observation": observation,
        "year": year,
        "root_image": root.photos.get().display_url,
        "child_image": child.photos.first().display_url,
    }


def login(page, live_server, user):
    page.goto(live_server.url + "/accounts/login/")
    page.locator("#id_username").fill(user.username)
    page.locator("#id_password").fill("Synthetic-password-42")
    page.locator('.login-panel button[type="submit"]').click()
    expect(page).to_have_url(live_server.url + "/")


@pytest.mark.parametrize("language", ["sv", "en"])
@pytest.mark.parametrize("javascript", [False, True])
@pytest.mark.parametrize("width", [320, 1280])
def test_area_cards_open_child_first_pages_and_existing_planting_diary_records(
    live_server, user, areas, browser_endpoint, language, javascript, width, tmp_path
):
    with (
        sync_playwright() as playwright,
        playwright.chromium.connect(browser_endpoint, timeout=20000) as browser,
        browser.new_context(
            viewport={"width": width, "height": 1000}, java_script_enabled=javascript
        ) as context,
    ):
        context.add_cookies(
            [{"name": "django_language", "value": language, "url": live_server.url}]
        )
        page = context.new_page()
        page.set_default_timeout(8000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        login(page, live_server, user)
        page.goto(live_server.url + reverse("garden:area-list"))
        expect(page.locator(".area-card")).to_have_count(1)
        root_image = page.locator(".area-card img")
        expect(root_image).to_have_attribute("src", areas["root_image"])
        root_image.evaluate("image => image.decode()")
        root_link = page.get_by_role("link", name=areas["root"].name, exact=True)
        root_link.focus()
        root_link.press("Enter")
        expect(page).to_have_url(live_server.url + areas["root"].get_absolute_url())
        expect(page.locator("h1")).to_have_text(areas["root"].name)
        expect(page.locator(".register-illustration")).to_have_attribute("src", areas["root_image"])
        children = page.locator(".area-child-section")
        expect(children.locator(".area-card")).to_have_count(1)
        expect(children.locator("img")).to_have_attribute("src", areas["child_image"])
        children.locator("img").evaluate("image => image.decode()")
        assert children.bounding_box()["y"] < page.locator(".area-information").bounding_box()["y"]
        expect(page.locator(".area-plantings")).not_to_contain_text("Synthetic batch")
        expect(page.locator(".area-plantings")).not_to_contain_text("Inner section batch")
        expect(page.locator(".diary-observations")).to_contain_text("Synthetic linked observation")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(tmp_path / "parent-area.png"), full_page=True)
        children.get_by_role(
            "button", name="Visa arkiverade" if language == "sv" else "Include archived"
        ).press("Space")
        expect(children.locator(".area-card")).to_have_count(2)
        expect(page).to_have_url(live_server.url + areas["root"].get_absolute_url() + "?archived=1")
        children.get_by_role("link", name=areas["child"].name, exact=True).press("Enter")
        child_url = live_server.url + areas["child"].get_absolute_url() + "?archived=1"
        expect(page).to_have_url(child_url)
        expect(page.locator(".record-fields")).to_contain_text("Synthetic bed notes")
        expect(page.locator(".record-fields")).not_to_contain_text("Synthetic parent notes")
        expect(page.locator(".register-photos figure")).to_have_count(2)
        expect(page.locator(".area-card")).to_contain_text(areas["grandchild"].name)
        expect(page.locator(".area-navigation a")).to_have_count(2)
        expect(page.locator(".area-plantings")).to_contain_text("Synthetic batch")
        expect(page.locator(".area-plantings")).not_to_contain_text("Inner section batch")
        page.locator(".area-card a").press("Enter")
        expect(page.locator("h1")).to_have_text(areas["grandchild"].name)
        expect(page.locator(".area-plantings")).to_contain_text("Inner section batch")
        expect(page.locator(".area-plantings")).not_to_contain_text("Synthetic batch")
        page.go_back()
        expect(page).to_have_url(child_url)
        page.get_by_role(
            "link",
            name=f"{'Odlingar' if language == 'sv' else 'Plantings'} {areas['year']} (1)",
            exact=True,
        ).press("Enter")
        expect(page.locator("#id_area_only")).to_have_value("1")
        expect(page.locator("#planting-results")).to_contain_text("Synthetic batch")
        expect(page.locator("#planting-results")).not_to_contain_text("Inner section batch")
        page.locator("[data-filter-disclosure] > summary").press("Enter")
        page.locator("#id_year").fill(str(areas["year"] - 1))
        if javascript:
            page.locator("#id_year").press("Enter")
        else:
            page.locator("[data-filter-submit]").click()
        expect(page.locator("#planting-results")).not_to_contain_text("Synthetic batch")
        expect(page.locator("#id_area_only")).to_have_value("1")
        page.get_by_role(
            "link", name="Alla år" if language == "sv" else "All years", exact=True
        ).click()
        expect(page.locator("#planting-results")).to_contain_text("Synthetic batch")
        expect(page.locator("#planting-results")).not_to_contain_text("Inner section batch")
        expect(page.locator("#id_area_only")).to_have_value("1")
        page.goto(child_url)
        assert page.locator("[data-record-workspace]").count() == 0
        for label in (
            ("Odlingshistorik", "Planting history"),
            ("Öppna post", "Open record"),
            ("Tillbaka till listan", "Back to list"),
        ):
            expect(
                page.get_by_role("link", name=label[0 if language == "sv" else 1], exact=True)
            ).to_have_count(0)
        page.locator(".area-plantings").get_by_role(
            "link", name="Synthetic batch", exact=True
        ).press("Enter")
        expect(page).to_have_url(live_server.url + areas["planting"].get_absolute_url())
        page.go_back()
        expect(page).to_have_url(child_url)
        page.locator(".diary-observations .observation-list a").press("Enter")
        expect(page).to_have_url(live_server.url + areas["observation"].get_absolute_url())
        assert errors == []


@pytest.mark.parametrize("javascript", [False, True])
@pytest.mark.parametrize("removal", ["chip", "selector"])
def test_clearing_area_filter_restores_descendants_for_another_area(
    live_server, user, areas, browser_endpoint, javascript, removal
):
    with (
        sync_playwright() as playwright,
        playwright.chromium.connect(browser_endpoint, timeout=20000) as browser,
        browser.new_context(java_script_enabled=javascript) as context,
    ):
        page = context.new_page()
        page.set_default_timeout(8000)
        login(page, live_server, user)
        page.goto(live_server.url + areas["child"].get_absolute_url())
        page.get_by_role("link", name=f"Odlingar {areas['year']} (1)", exact=True).click()
        results = page.locator("#planting-results")
        expect(results).to_contain_text("Synthetic batch")
        expect(results).not_to_contain_text("Inner section batch")
        disclosure = page.locator("[data-filter-disclosure]")
        if removal == "chip":
            page.locator(".filter-chip[href]").filter(has_text=areas["child"].name).click()
        else:
            disclosure.locator(":scope > summary").click()
            page.locator("#id_area").select_option("")
            if not javascript:
                page.locator("[data-filter-submit]").click()
        expect(page.locator("#id_area_only")).to_have_value("")
        expect(results).to_contain_text("Inner section batch")
        if removal == "chip" or not javascript:
            disclosure.locator(":scope > summary").click()
        page.locator("#id_area").select_option(str(areas["root"].pk))
        if not javascript:
            page.locator("[data-filter-submit]").click()
        expect(page.locator("#id_area")).to_have_value(str(areas["root"].pk))
        expect(page.locator("#id_area_only")).to_have_value("")
        expect(results).to_contain_text("Synthetic batch")
        expect(results).to_contain_text("Inner section batch")


@pytest.mark.parametrize("language,raised_bed", [("sv", "Pallkrage"), ("en", "Raised bed")])
@pytest.mark.parametrize("javascript", [False, True])
def test_area_type_suggestions_and_custom_text_survive_editing(
    live_server, user, areas, browser_endpoint, language, raised_bed, javascript
):
    with (
        sync_playwright() as playwright,
        playwright.chromium.connect(browser_endpoint, timeout=20000) as browser,
        browser.new_context(java_script_enabled=javascript) as context,
    ):
        context.add_cookies(
            [{"name": "django_language", "value": language, "url": live_server.url}]
        )
        page = context.new_page()
        page.set_default_timeout(8000)
        login(page, live_server, user)
        page.goto(live_server.url + areas["child"].get_absolute_url() + "?archived=1")
        page.get_by_role(
            "link", name="Redigera" if language == "sv" else "Edit", exact=True
        ).click()
        expect(page.locator("#id_area_type")).to_have_value("Synthetic custom type")
        expect(page.locator(f'#area-types option[value="{raised_bed}"]')).to_have_count(1)
        page.locator("#id_area_type").fill(raised_bed)
        page.locator("#register-form button[type=submit]").click()
        expect(page).to_have_url(
            live_server.url + areas["child"].get_absolute_url() + "?archived=1"
        )
        expect(page.locator(".record-fields")).to_contain_text(raised_bed)
        expect(page.locator(".register-photos figure")).to_have_count(2)
        page.get_by_role(
            "link", name="Redigera" if language == "sv" else "Edit", exact=True
        ).click()
        page.locator("#id_area_type").fill("Another synthetic custom type")
        page.locator("#register-form button[type=submit]").click()
        expect(page.locator(".record-fields")).to_contain_text("Another synthetic custom type")
        expect(page.locator(".area-card")).to_contain_text(areas["grandchild"].name)


@pytest.mark.parametrize("width", [320, 1280])
def test_long_child_card_lists_keep_keyboard_navigation_and_browser_back_usable(
    live_server, user, browser_endpoint, width
):
    root = create_area(actor=user, name="Synthetic parent").record
    for index in range(30):
        create_area(
            actor=user, parent=root, name=f"Synthetic bed {index:02}", notes=f"Notes {index:02}"
        )
    with (
        sync_playwright() as playwright,
        playwright.chromium.connect(browser_endpoint, timeout=20000) as browser,
        browser.new_context(viewport={"width": width, "height": 844}) as context,
    ):
        page = context.new_page()
        page.set_default_timeout(8000)
        login(page, live_server, user)
        page.goto(live_server.url + root.get_absolute_url())
        expect(page.locator(".area-card")).to_have_count(30)
        for index in (0, 29):
            link = page.locator(".area-card").get_by_role(
                "link", name=f"Synthetic bed {index:02}", exact=True
            )
            link.scroll_into_view_if_needed()
            link.focus()
            link.press("Enter")
            expect(page.locator("h1")).to_have_text(f"Synthetic bed {index:02}")
            expect(page.locator(".record-fields")).to_contain_text(f"Notes {index:02}")
            page.go_back()
            expect(page).to_have_url(live_server.url + root.get_absolute_url())
            expect(link).to_be_in_viewport()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
