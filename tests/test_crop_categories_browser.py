import io
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import pytest
from browser_helpers import browser_page, login, wait_for_image
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from PIL import Image
from playwright.sync_api import expect

from garden.models import Crop, CropCategory, Variety
from garden.services import archive_crop, create_category, create_crop, create_variety

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize("javascript", [False, True])
@pytest.mark.parametrize("editing", [False, True])
def test_archived_crop_error_link_focuses_category_crops(
    live_server, user, browser_endpoint, javascript, editing
):
    crop = create_crop(actor=user, name="Tomato").record
    category = create_category(actor=user, name="Vegetables").record if editing else None

    def archive():
        close_old_connections()
        try:
            archive_crop(actor=type(user).objects.get(pk=user.pk), pk=crop.pk)
        finally:
            connection.close()

    with browser_page(browser_endpoint, java_script_enabled=javascript) as page:
        login(page, live_server, user, "en")
        path = f"/garden/categories/{category.pk}/edit/" if editing else "/garden/categories/new/"
        page.goto(live_server.url + path)
        page.locator("#id_name").fill("Vegetables")
        page.get_by_role("checkbox", name="Tomato", exact=True).check()
        key = page.locator("#id_submission_key").input_value()
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(archive).result(timeout=10)
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.locator('.error-summary [data-field-errors="crop_ids"]')).not_to_be_empty()
        assert page.locator("#id_submission_key").input_value() == key
        expect(page.locator('input[name="crop_ids"]:checked')).to_have_count(1)
        page.locator(".error-summary").get_by_role("link", name="Crops", exact=True).click()
        expect(page.get_by_role("group", name="Crops (optional)", exact=True)).to_be_focused()
        assert page.locator('input[name="crop_ids"]').first.get_attribute("tabindex") is None
    assert not CropCategory.objects.filter(crops=crop).exists()


@pytest.mark.parametrize("width", [320, 768, 1280])
def test_default_illustration_upload_retains_transparency_and_heading_size(
    live_server, user, browser_endpoint, width
):
    source = settings.BASE_DIR / "static/images/registers/crops-and-varieties.webp"
    category = create_category(
        actor=user,
        name="Synthetic illustrated category",
        uploads=[SimpleUploadedFile(source.name, source.read_bytes(), "image/webp")],
    ).record
    with browser_page(browser_endpoint, viewport={"width": width, "height": 1000}) as page:
        login(page, live_server, user, "en")
        page.goto(live_server.url + "/garden/varieties/")
        image = page.locator(".register-illustration")
        wait_for_image(image)
        overview = image.bounding_box()
        page.goto(live_server.url + category.get_absolute_url())
        expect(image).to_have_attribute("src", re.compile(r"/photos/\d+/display/$"))
        wait_for_image(image)
        selected = image.bounding_box()
        assert abs(overview["width"] - selected["width"]) < 2
        assert abs(overview["height"] - selected["height"]) < 2
        assert (
            image.evaluate("""image => {
            const canvas = document.createElement('canvas');
            canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
            const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
            return context.getImageData(0, 0, 1, 1).data[3];
        }""")
            == 0
        )
        assert image.evaluate("image => getComputedStyle(image).backgroundImage") == "none"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize(
    "javascript,language,width",
    [(True, "sv", 390), (True, "en", 1280), (False, "sv", 390), (False, "en", 1280)],
)
def test_category_workflow_with_image_retry_and_last_category_deletion(
    live_server, user, browser_endpoint, javascript, language, width
):
    crop = create_crop(actor=user, name="Tomato").record
    variety = create_variety(actor=user, crop=crop, name="Golden").record
    create_crop(actor=user, name="Bean")
    buffer = io.BytesIO()
    with Image.new("RGBA", (16, 16), (0, 0, 0, 0)) as source:
        source.putpixel((8, 8), (35, 110, 50, 255))
        source.save(buffer, format="PNG")
    upload = {"name": "synthetic.png", "mimeType": "image/png", "buffer": buffer.getvalue()}
    labels = {
        "sv": {
            "new": "Ny kategori",
            "newcrop": "Ny gröda",
            "save": "Spara",
            "uncategorized": "Utan kategori",
            "edit": "Redigera: Vegetables",
            "delete": "Radera: Summer",
            "confirm": "Radera post",
        },
        "en": {
            "new": "New category",
            "newcrop": "New crop",
            "save": "Save",
            "uncategorized": "Uncategorized",
            "edit": "Edit: Vegetables",
            "delete": "Delete: Summer",
            "confirm": "Delete record",
        },
    }[language]
    errors = []
    with browser_page(
        browser_endpoint, java_script_enabled=javascript, viewport={"width": width, "height": 844}
    ) as page:
        page.on("pageerror", lambda error: errors.append(str(error)))
        login(page, live_server, user, language)
        page.goto(live_server.url + "/garden/varieties/")
        expect(page.locator(".category-card")).to_have_count(1)
        expect(page.locator(".category-card img")).to_have_attribute(
            "src", "/static/images/registers/uncategorized.webp"
        )
        page.get_by_role("link", name=labels["new"], exact=True).click()
        page.locator("#id_name").fill(" ")
        page.get_by_role("checkbox", name="Tomato", exact=True).check()
        page.locator("#id_photos").set_input_files(upload)
        key = page.locator("#id_submission_key").input_value()
        page.get_by_role("button", name=labels["save"], exact=True).click()
        expect(page.locator("#id_name_errors")).not_to_be_empty()
        assert page.locator("#id_submission_key").input_value() == key
        expect(page.get_by_role("checkbox", name="Tomato", exact=True)).to_be_checked()
        if javascript:
            expect(page.locator("[data-photo-previews] figure")).to_have_count(1)
        else:
            page.locator("#id_photos").set_input_files(upload)
        page.locator("#id_name").fill("Vegetables")
        page.get_by_role("button", name=labels["save"], exact=True).click()
        expect(page).to_have_url(re.compile(r"/garden/categories/\d+/$"))
        expect(page.locator("[data-crop-id]")).to_have_count(1)
        expect(page.get_by_role("link", name=labels["new"], exact=True)).to_have_count(0)
        heading_image = page.locator(".register-illustration")
        expect(heading_image).to_have_attribute("src", re.compile(r"/photos/\d+/display/$"))
        category_image_url = heading_image.get_attribute("src")
        if javascript:
            wait_for_image(heading_image)
            assert (
                heading_image.evaluate("""image => {
                const canvas = document.createElement('canvas');
                canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
                const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
                return context.getImageData(0, 0, 1, 1).data[3];
            }""")
                == 0
            )
        page.locator("[data-crop-id] > summary").press("Enter")
        expect(
            page.get_by_role("button" if javascript else "link", name="Golden", exact=True)
        ).to_be_visible()
        page.get_by_role("link", name=labels["newcrop"], exact=True).click()
        page.locator("#id_name").fill("Pepper")
        page.get_by_role("button", name=labels["save"], exact=True).click()
        expect(page).to_have_url(re.compile(r"/garden/categories/\d+/$"))
        expect(page.locator("[data-crop-id]")).to_have_count(2)
        page.goto(live_server.url + "/garden/varieties/")
        expect(
            page.locator(".category-card").filter(has_text="Vegetables").locator("img")
        ).to_have_attribute("src", category_image_url)
        page.get_by_role("link", name=labels["uncategorized"], exact=True).click()
        expect(page.locator("[data-crop-id]")).to_have_count(1)
        expect(page.locator("[data-crop-id] > summary")).to_contain_text("Bean")
        expect(page.locator(".register-illustration")).to_have_attribute(
            "src", "/static/images/registers/uncategorized.webp"
        )
        expect(page.get_by_role("link", name=labels["new"], exact=True)).to_have_count(0)
        page.goto(live_server.url + "/garden/varieties/")
        page.get_by_role(
            "link",
            name="Hantera kategorier" if language == "sv" else "Manage categories",
            exact=True,
        ).click()
        page.get_by_role("link", name=labels["edit"], exact=True).click()
        page.locator("#id_name").fill("Summer")
        page.locator("#id_remove_image").check()
        page.get_by_role("button", name=labels["save"], exact=True).click()
        expect(page).to_have_url(live_server.url + "/garden/categories/manage/")
        page.get_by_role("link", name="Summer", exact=True).click()
        expect(page.locator("[data-crop-id]")).to_have_count(2)
        expect(page.locator(".register-illustration")).to_have_attribute(
            "src", "/static/images/registers/crops-and-varieties.webp"
        )
        if javascript:
            page.locator("#id_q").fill("Bean")
            page.locator("#id_q").press("Enter")
            expect(page.locator("[data-crop-id]")).to_have_count(0)
            page.locator("#id_q").fill("")
            page.locator("#id_q").press("Enter")
            expect(page.locator("[data-crop-id]")).to_have_count(2)
            page.goto(live_server.url + "/garden/varieties/?q=Bean")
            expect(page.locator("[data-crop-id]")).to_have_count(1)
            page.locator("#id_q").fill("")
            page.locator("#id_q").press("Enter")
            expect(page.locator("[data-crop-id]")).to_have_count(3)
        page.goto(live_server.url + "/garden/varieties/")
        expect(
            page.locator(".category-card").filter(has_text="Summer").locator("img")
        ).to_have_count(0)
        if javascript:
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.get_by_role(
            "link",
            name="Hantera kategorier" if language == "sv" else "Manage categories",
            exact=True,
        ).click()
        page.get_by_role("link", name=labels["delete"], exact=True).click()
        page.locator("#id_confirm").check()
        page.get_by_role("button", name=labels["confirm"], exact=True).click()
        expect(page).to_have_url(live_server.url + "/garden/categories/manage/")
        expect(page.locator('li[id^="managed-"]')).to_have_count(0)
        page.get_by_role(
            "link", name="Tillbaka till listan" if language == "sv" else "Back to list", exact=True
        ).click()
        expect(page.locator(".category-card")).to_have_count(1)
        page.get_by_role("link", name=labels["uncategorized"], exact=True).click()
        expect(page.locator("[data-crop-id]")).to_have_count(3)
    assert not errors
    assert not CropCategory.objects.exists()
    assert Variety.objects.get(pk=variety.pk).crop_id == crop.pk
    assert Crop.objects.count() == 3


@pytest.mark.parametrize("javascript", [False, True])
def test_category_variety_save_and_cancel_return_to_filtered_category(
    live_server, user, browser_endpoint, javascript
):
    crop = create_crop(actor=user, name="Tomato").record
    category = create_category(actor=user, name="Vegetables", crops=[crop]).record
    destination = category.get_absolute_url() + "?q=tom&archived=1"
    with browser_page(browser_endpoint, timeout=30000, java_script_enabled=javascript) as page:
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
