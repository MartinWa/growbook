import io
import re

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from playwright.sync_api import expect, sync_playwright

from garden.models import Crop, CropCategory, Variety
from garden.services import create_category, create_crop, create_variety

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


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
    with (
        sync_playwright() as playwright,
        playwright.chromium.connect(browser_endpoint, timeout=20000) as browser,
        browser.new_context(viewport={"width": width, "height": 1000}) as context,
    ):
        context.add_cookies([{"name": "django_language", "value": "en", "url": live_server.url}])
        page = context.new_page()
        page.set_default_timeout(8000)
        page.goto(live_server.url + "/accounts/login/")
        page.locator("#id_username").fill(user.username)
        page.locator("#id_password").fill("Synthetic-password-42")
        page.get_by_role("button", name="Log in", exact=True).click()
        page.goto(live_server.url + "/garden/varieties/")
        image = page.locator(".register-illustration")
        image.evaluate("image => image.decode()")
        overview = image.bounding_box()
        page.goto(live_server.url + category.get_absolute_url())
        expect(image).to_have_attribute("src", re.compile(r"/photos/\d+/display/$"))
        image.evaluate("image => image.decode()")
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
            "login": "Logga in",
            "new": "Ny kategori",
            "newcrop": "Ny gröda",
            "save": "Spara",
            "uncategorized": "Utan kategori",
            "edit": "Redigera kategori: Vegetables",
            "delete": "Ta bort kategori: Summer",
            "confirm": "Radera post",
        },
        "en": {
            "login": "Log in",
            "new": "New category",
            "newcrop": "New crop",
            "save": "Save",
            "uncategorized": "Uncategorized",
            "edit": "Edit category: Vegetables",
            "delete": "Delete category: Summer",
            "confirm": "Delete record",
        },
    }[language]
    errors = []
    with (
        sync_playwright() as playwright,
        playwright.chromium.connect(browser_endpoint, timeout=20000) as browser,
        browser.new_context(
            java_script_enabled=javascript, viewport={"width": width, "height": 844}
        ) as context,
    ):
        context.add_cookies(
            [{"name": "django_language", "value": language, "url": live_server.url}]
        )
        page = context.new_page()
        page.set_default_timeout(8000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(live_server.url + "/accounts/login/")
        page.locator("#id_username").fill(user.username)
        page.locator("#id_password").fill("Synthetic-password-42")
        page.get_by_role("button", name=labels["login"], exact=True).click()
        page.goto(live_server.url + "/garden/varieties/")
        expect(page.locator(".category-card")).to_have_count(1)
        expect(page.locator(".category-card img")).to_have_attribute(
            "src", "/static/images/registers/crops-and-varieties.webp"
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
            assert (
                heading_image.evaluate("""async image => {
                await image.decode();
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
        expect(page).to_have_url(re.compile(r"/garden/crops/\d+/$"))
        page.goto(live_server.url + "/garden/varieties/")
        expect(
            page.locator(".category-card").filter(has_text="Vegetables").locator("img")
        ).to_have_attribute("src", category_image_url)
        page.get_by_role("link", name=labels["uncategorized"], exact=True).click()
        expect(page.locator("[data-crop-id]")).to_have_count(1)
        expect(page.locator("[data-crop-id] > summary")).to_contain_text("Bean")
        expect(page.locator(".register-illustration")).to_have_attribute(
            "src", "/static/images/registers/crops-and-varieties.webp"
        )
        expect(page.get_by_role("link", name=labels["new"], exact=True)).to_have_count(0)
        page.goto(live_server.url + "/garden/varieties/")
        page.get_by_role("link", name=labels["edit"], exact=True).click()
        page.locator("#id_name").fill("Summer")
        page.locator("#id_remove_image").check()
        page.get_by_role("button", name=labels["save"], exact=True).click()
        expect(page).to_have_url(re.compile(r"/garden/categories/\d+/$"))
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
        page.get_by_role("link", name=labels["delete"], exact=True).click()
        page.locator("#id_confirm").check()
        page.get_by_role("button", name=labels["confirm"], exact=True).click()
        expect(page).to_have_url(live_server.url + "/garden/varieties/")
        expect(page.locator(".category-card")).to_have_count(1)
        page.get_by_role("link", name=labels["uncategorized"], exact=True).click()
        expect(page.locator("[data-crop-id]")).to_have_count(3)
    assert not errors
    assert not CropCategory.objects.exists()
    assert Variety.objects.get(pk=variety.pk).crop_id == crop.pk
    assert Crop.objects.count() == 3
