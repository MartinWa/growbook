"""Feedback flows in Chromium, including ordinary HTML and contextual retries."""

from urllib.parse import parse_qs, urlsplit

import pytest
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from garden.models import Planting, Variety
from garden.services import (
    create_area,
    create_category,
    create_crop,
    create_planting,
    create_variety,
)

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]

COPY = {
    "en": {
        "login": "Log in",
        "new": "New planting",
        "save": "Save",
        "cancel": "Cancel",
        "choices": "Update variety choices",
        "variety": "New variety",
    },
    "sv": {
        "login": "Logga in",
        "new": "Ny odling",
        "save": "Spara",
        "cancel": "Avbryt",
        "choices": "Uppdatera sortval",
        "variety": "Ny sort",
    },
}


def login(page, live_server, user, language):
    page.context.add_cookies(
        [{"name": "django_language", "value": language, "url": live_server.url}]
    )
    page.goto(live_server.url + "/accounts/login/")
    page.locator("#id_username").fill(user.username)
    page.locator("#id_password").fill("Synthetic-password-42")
    page.get_by_role("button", name=COPY[language]["login"], exact=True).click()
    expect(page).to_have_url(live_server.url + "/")


def open_details(page, selector="#planting-more-details"):
    group = page.locator(selector)
    if group.get_attribute("open") is None:
        group.locator(":scope > summary").click()


@pytest.mark.parametrize(
    "javascript,language,width",
    [(True, "en", 1280), (True, "sv", 320), (False, "en", 1280), (False, "sv", 320)],
)
def test_area_creation_cancel_validation_labels_and_year_navigation(
    live_server, user, browser_endpoint, image_upload, javascript, language, width
):
    crop = create_crop(actor=user, name="Tomato").record
    variety = create_variety(actor=user, crop=crop, name="Golden").record
    parent = create_area(actor=user, name="Garden").record
    area = create_area(actor=user, name="Bed", parent=parent).record
    year = timezone.localdate().year
    past = create_planting(
        actor=user, crop=crop, area=area, start_year=year - 1, uploads=[image_upload()]
    ).record
    future = create_planting(actor=user, crop=crop, area=area, start_year=year + 1).record
    destination = area.get_absolute_url() + "?archived=1"
    with (
        sync_playwright() as playwright,
        playwright.chromium.connect(browser_endpoint, timeout=20000) as browser,
        browser.new_context(
            java_script_enabled=javascript, viewport={"width": width, "height": 1000}
        ) as context,
    ):
        page = context.new_page()
        page.set_default_timeout(10000)
        login(page, live_server, user, language)
        page.goto(live_server.url + destination)
        expect(
            page.locator(f'.area-plantings a[href="{past.get_absolute_url()}"] img')
        ).to_be_visible()
        expect(
            page.locator(f'.area-plantings a[href="{future.get_absolute_url()}"]')
        ).to_be_visible()
        years = page.get_by_role(
            "navigation", name="Growing years" if language == "en" else "Odlingsår"
        )
        links = years.locator("a").evaluate_all("links => links.map(link => link.href)")
        assert {str(year - 1), str(year), str(year + 1)} <= {
            value for link in links for value in parse_qs(urlsplit(link).query).get("year", [])
        }
        page.get_by_role("link", name=COPY[language]["new"], exact=True).click()
        expect(page.locator("#id_area")).to_have_value(str(area.pk))
        expect(page.locator(f'#id_area option[value="{parent.pk}"]')).to_have_count(0)
        page.get_by_role("link", name=COPY[language]["cancel"], exact=True).click()
        expect(page).to_have_url(live_server.url + destination)
        page.get_by_role("link", name=COPY[language]["new"], exact=True).click()
        page.locator("#id_crop").select_option(str(crop.pk))
        open_details(page)
        if javascript:
            expect(page.locator("#id_label")).to_have_value("Tomato")
            expect(page.locator("#id_variety option")).to_have_count(2)
        else:
            page.get_by_role("button", name=COPY[language]["choices"], exact=True).click()
        page.locator("#id_variety").select_option(str(variety.pk))
        if javascript:
            expect(page.locator("#id_label")).to_have_value("Tomato-Golden")
            page.locator("#id_label").fill("Custom trial")
            page.locator("#id_variety").select_option("")
            page.locator("#id_variety").select_option(str(variety.pk))
            expect(page.locator("#id_label")).to_have_value("Custom trial")
        page.locator("#id_start_year").fill(str(year + 1))
        page.locator("#id_quantity").fill("-1")
        key = page.locator("#id_submission_key").input_value()
        page.locator("#register-form").evaluate("form => form.noValidate = true")
        page.get_by_role("button", name=COPY[language]["save"], exact=True).click()
        expect(page.locator('#register-errors [data-field-errors="quantity"]')).not_to_be_empty()
        expect(page.locator("#id_submission_key")).to_have_value(key)
        expect(page.locator('#register-form input[name="next"]')).to_have_value(destination)
        expect(
            page.get_by_role("link", name=COPY[language]["cancel"], exact=True)
        ).to_have_attribute("href", destination)
        page.locator("#id_quantity").fill("0")
        page.get_by_role("button", name=COPY[language]["save"], exact=True).click()
        expect(page).to_have_url(live_server.url + destination)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    saved = Planting.objects.exclude(pk__in=[past.pk, future.pk]).get()
    assert saved.area_id == area.pk and saved.quantity == 0
    assert saved.label == ("Custom trial" if javascript else "Tomato-Golden")


@pytest.mark.parametrize("javascript", [False, True])
def test_category_variety_save_and_cancel_return_to_filtered_category(
    live_server, user, browser_endpoint, javascript
):
    crop = create_crop(actor=user, name="Tomato").record
    category = create_category(actor=user, name="Vegetables", crops=[crop]).record
    destination = category.get_absolute_url() + "?q=tom&archived=1"
    with (
        sync_playwright() as playwright,
        playwright.chromium.connect(browser_endpoint, timeout=20000) as browser,
        browser.new_context(java_script_enabled=javascript) as context,
    ):
        page = context.new_page()
        login(page, live_server, user, "en")
        page.goto(live_server.url + category.get_absolute_url())
        page.locator("#id_q").fill("tom")
        if not javascript:
            page.get_by_role("button", name="Apply", exact=True).click()
        page.wait_for_url(lambda url: parse_qs(urlsplit(url).query).get("q") == ["tom"])
        page.locator("[data-archive-toggle]").click()
        expect(page).to_have_url(live_server.url + destination)
        page.get_by_role("link", name="New variety", exact=True).click()
        page.get_by_role("link", name="Cancel", exact=True).click()
        expect(page).to_have_url(live_server.url + destination)
        page.get_by_role("link", name="New variety", exact=True).click()
        page.locator("#id_crop").select_option(str(crop.pk))
        page.locator("#id_name").fill("Golden")
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page).to_have_url(live_server.url + destination)
        expect(page.locator("#id_q")).to_have_value("tom")
    assert Variety.objects.get().crop_id == crop.pk


@pytest.mark.parametrize("javascript", [False, True])
def test_add_variety_to_batch_preserves_independent_photos_and_retries(
    live_server, user, browser_endpoint, image_upload, javascript
):
    crop = create_crop(actor=user, name="Tomato").record
    first = create_variety(actor=user, crop=crop, name="Golden").record
    second = create_variety(actor=user, crop=crop, name="Red").record
    original = create_planting(
        actor=user,
        crop=crop,
        variety=first,
        start_year=2027,
        quantity=0,
        evaluation="First evaluation",
        uploads=[image_upload()],
    ).record
    with (
        sync_playwright() as playwright,
        playwright.chromium.connect(browser_endpoint, timeout=20000) as browser,
        browser.new_context(java_script_enabled=javascript) as context,
    ):
        page = context.new_page()
        login(page, live_server, user, "en")
        page.goto(live_server.url + original.get_absolute_url())
        page.get_by_role("link", name="Add variety to batch", exact=True).click()
        expect(page.locator("#id_variety")).to_be_visible()
        expect(page.locator("#id_crop")).to_have_value(str(crop.pk))
        expect(page.locator("#id_start_year")).to_have_value("2027")
        if javascript:
            page.locator("#id_crop").select_option(str(crop.pk))
            status = page.locator("#variety-status")
            expect(status).to_have_text(status.get_attribute("data-choices-ready"))
            expect(page.locator("#id_variety option")).to_have_count(2)
        else:
            page.get_by_role("button", name="Update variety choices", exact=True).click()
        expect(page.locator("#id_variety")).to_have_js_property("required", True)
        expect(page.locator(f'#id_variety option[value="{first.pk}"]')).to_have_count(0)
        page.locator("#id_variety").select_option(str(second.pk))
        page.locator("#id_quantity").fill("12")
        open_details(page, "#planting-notes-photos")
        page.locator("#id_evaluation").fill("Second evaluation")
        page.locator("#id_photos").set_input_files(
            {"name": "synthetic.jpg", "mimeType": "image/jpeg", "buffer": image_upload().read()}
        )
        if javascript:

            def lose_saved_response(route):
                route.fetch()
                route.abort()

            page.route("**/garden/plantings/new/", lose_saved_response, times=1)
            page.get_by_role("button", name="Save", exact=True).click()
            expect(page.locator("#register-errors")).to_contain_text("The connection failed")
            assert page.locator("#id_photos").evaluate("input => input.files.length") == 1
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page).to_have_url(live_server.url + original.get_absolute_url())
        expect(page.locator(".batch-varieties")).to_contain_text("Red")
        expect(page.locator(".record-fields")).to_contain_text("First evaluation")
        expect(page.locator(".register-photos figure")).to_have_count(1)
        page.goto(live_server.url + "/garden/plantings/?year=2027")
        summary = page.locator(".planting-batch > summary")
        summary.focus()
        summary.press("Enter")
        row = page.locator(".planting-batch [data-register-toggle]").filter(has_text="Tomato-Red")
        expect(row).to_be_visible()
        row.click()
        expect(page.locator(".record-fields")).to_contain_text("Second evaluation")
        if javascript:
            page.locator("[data-record-return]").click()
            expect(row).to_be_focused()
        else:
            expect(page.locator(".register-photos figure")).to_have_count(1)
    added = Planting.objects.exclude(pk=original.pk).get()
    assert added.batch_key == original.batch_key and added.quantity == 12
    assert added.photos.count() == original.photos.count() == 1
    assert added.label == "Tomato-Red"
