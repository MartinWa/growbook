"""Real management and scoped deletion navigation with and without JavaScript."""

from datetime import date

import pytest
from browser_helpers import browser_page, login, wait_for_image
from django.urls import reverse
from playwright.sync_api import expect
from test_planting_category_images import transparent_upload

from garden.services import create_category, create_crop, create_planting, create_variety
from garden.services.cycles import create_planting_category

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize("javascript", [True, False])
@pytest.mark.parametrize("scope", ["category", "all-crops", "uncategorized", "planned", "custom"])
@pytest.mark.parametrize("new_tab", [False, True])
def test_delete_from_filtered_category_preserves_confirmation_cancel_and_return(
    live_server, user, browser_endpoint, javascript, scope, new_tab
):
    crop = create_crop(actor=user, name="Tomato").record
    if scope in ("category", "all-crops", "uncategorized"):
        create_variety(actor=user, crop=crop, name="Golden")
        category = (
            create_category(actor=user, name="Vegetables", crops=[crop]).record
            if scope == "category"
            else None
        )
        origin = (
            category.get_absolute_url() if category else reverse("garden:" + scope)
        ) + "?q=gold&archived=1"
        detail_name, delete_name = "Golden", "Delete"
    else:
        category = create_planting_category(actor=user, name="Summer").record
        create_planting(
            actor=user, crop=crop, start_year=2024, label="Trial", category_ids=[category.pk]
        )
        origin = (
            category.get_absolute_url()
            if scope == "custom"
            else reverse("garden:planting-status", args=["planned"])
        ) + f"?all_years=1&crop={crop.pk}&unassigned=1"
        detail_name, delete_name = "Trial", "Delete planting"
    with browser_page(browser_endpoint, java_script_enabled=javascript) as page:
        login(page, live_server, user, "en")
        page.goto(live_server.url + origin)
        if (
            scope in ("category", "all-crops", "uncategorized")
            and page.locator(f"#crop-group-{crop.pk}").get_attribute("open") is None
        ):
            page.locator(f"#crop-group-{crop.pk} > summary").click()
        name = page.locator(".record-name").filter(has_text=detail_name).first
        if new_tab:
            target = name.get_attribute("href")
            detail = page.context.new_page()
            detail.goto(live_server.url + target)
        else:
            name.click()
            detail = page
        detail.get_by_role("link", name=delete_name, exact=True).click()
        expect(detail.locator('#main input[name="next"]')).to_have_value(origin)
        detail.get_by_role("link", name="Cancel", exact=True).click()
        expect(detail).to_have_url(live_server.url + origin)
        if (
            scope in ("category", "all-crops", "uncategorized")
            and detail.locator(f"#crop-group-{crop.pk}").get_attribute("open") is None
        ):
            detail.locator(f"#crop-group-{crop.pk} > summary").click()
        detail.locator(".record-name").filter(has_text=detail_name).first.click()
        detail.get_by_role("link", name=delete_name, exact=True).click()
        detail.locator("#id_confirm").check()
        detail.get_by_role(
            "button", name="Delete record" if delete_name == "Delete" else delete_name, exact=True
        ).click()
        expect(detail).to_have_url(live_server.url + origin)
        expect(detail.locator("[data-register-toggle]")).to_have_count(0)


@pytest.mark.parametrize("javascript", [True, False])
@pytest.mark.parametrize("language", ["en", "sv"])
def test_planting_category_management_image_retry_replace_remove_and_delete(
    live_server, user, browser_endpoint, javascript, language
):
    category = create_planting_category(actor=user, name="Summer").record
    upload = {
        "name": "synthetic.png",
        "mimeType": "image/png",
        "buffer": transparent_upload().read(),
    }
    with browser_page(
        browser_endpoint, java_script_enabled=javascript, viewport={"width": 390, "height": 844}
    ) as page:
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        login(page, live_server, user, language)
        origin = live_server.url + "/garden/plantings/?year=2024&unassigned=1"
        page.goto(origin)
        page.get_by_role(
            "link",
            name="Manage categories" if language == "en" else "Hantera kategorier",
            exact=True,
        ).click()
        management = page.url
        page.get_by_role(
            "link", name="Edit: Summer" if language == "en" else "Redigera: Summer", exact=True
        ).click()
        page.locator("#id_name").fill(" ")
        page.locator("#id_photos").set_input_files(upload)
        key = page.locator("#id_submission_key").input_value()
        page.locator('#register-form button[type="submit"]').click()
        expect(page.locator("#id_name_errors")).not_to_be_empty()
        expect(page.locator("#id_submission_key")).to_have_value(key)
        if javascript:
            assert page.locator("#id_photos").evaluate("input => input.files.length") == 1
        else:
            page.locator("#id_photos").set_input_files(upload)
        page.locator("#id_name").fill("Summer")
        page.locator('#register-form button[type="submit"]').click()
        expect(page).to_have_url(management)
        page.get_by_role(
            "link", name="Back to list" if language == "en" else "Tillbaka till listan", exact=True
        ).click()
        expect(page).to_have_url(origin)
        card = page.locator(".category-card").filter(has_text="Summer")
        image = card.locator("img")
        wait_for_image(image)
        source = image.get_attribute("src")
        card.locator("a").click()
        expect(page.locator(".register-illustration")).to_have_attribute("src", source)
        wait_for_image(page.locator(".register-illustration"))
        assert (
            page.locator(".register-illustration").evaluate("""image => {
            const canvas = document.createElement('canvas');
            canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
            const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
            return context.getImageData(0, 0, 1, 1).data[3];
        }""")
            == 0
        )
        page.goto(management)
        page.get_by_role(
            "link", name="Edit: Summer" if language == "en" else "Redigera: Summer", exact=True
        ).click()
        expect(page.locator(".category-current-image")).to_be_visible()
        previous_image = page.locator(".category-current-image").get_attribute("src")
        page.locator("#id_photos").set_input_files(upload)
        page.locator("#id_name").fill(" ")
        page.locator('#register-form button[type="submit"]').click()
        expect(page.locator("#id_name_errors")).not_to_be_empty()
        expect(page.locator(".category-current-image")).to_have_attribute("src", previous_image)
        if not javascript:
            page.locator("#id_photos").set_input_files(upload)
        page.locator("#id_name").fill("Summer")
        page.locator('#register-form button[type="submit"]').click()
        expect(page).to_have_url(management)
        expect(page.locator(".category-current-image")).not_to_have_attribute("src", previous_image)
        page.get_by_role(
            "link", name="Edit: Summer" if language == "en" else "Redigera: Summer", exact=True
        ).click()
        page.locator("#id_remove_image").check()
        page.locator('#register-form button[type="submit"]').click()
        expect(page).to_have_url(management)
        page.get_by_role("link", name="Summer", exact=True).click()
        expect(page.locator(".register-illustration")).to_have_attribute(
            "src", "/static/images/registers/plantings.webp"
        )
        page.goto(management)
        page.get_by_role(
            "link", name="Delete: Summer" if language == "en" else "Radera: Summer", exact=True
        ).click()
        page.locator("#id_confirm").check()
        page.locator('.register-form-panel button[type="submit"]').click()
        expect(page).to_have_url(management)
        expect(page.locator(f"#managed-{category.pk}")).to_have_count(0)
        assert not errors


@pytest.mark.parametrize("javascript", [True, False])
def test_completed_experiences_full_page_and_inline_panel(
    live_server, user, browser_endpoint, javascript
):
    crop = create_crop(actor=user, name="Tomato").record
    variety = create_variety(
        actor=user, crop=crop, name="Golden", experiences="Manual experience"
    ).record
    member = create_planting(
        actor=user,
        crop=crop,
        variety=variety,
        start_year=2024,
        evaluation="Completed trial result",
        status="completed",
        completed_on=date(2024, 9, 1),
    ).record
    with browser_page(browser_endpoint, java_script_enabled=javascript) as page:
        login(page, live_server, user, "en")
        page.goto(live_server.url + reverse("garden:all-crops"))
        page.locator(f"#crop-group-{crop.pk} > summary").click()
        page.locator(".record-name").filter(has_text="Golden").click()
        expect(page.locator(".record-fields")).to_contain_text("Manual experience")
        expect(page.locator(".record-fields")).to_contain_text("Completed trial result")
        expect(
            page.locator(f'.record-fields a[href="{member.get_absolute_url()}"]')
        ).to_be_visible()
        if javascript:
            page.get_by_role("link", name="Open record: Golden", exact=True).click()
            expect(page.locator(".record-fields")).to_contain_text("Completed trial result")
