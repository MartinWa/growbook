"""Season selection, real layout and colour contrast in Chromium."""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from browser_helpers import browser_page, login, wait_for_image
from playwright.sync_api import expect

from core.appearance import SEASON_COOKIE
from diary.services import create_entry, update_entry_photo
from garden.services import create_area, create_crop, create_planting, create_variety

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def sample_entries(user, image_upload):
    note = "Synthetic diary observation. " * 12 + "Full text remains visible on mobile."
    records = []
    for body, colours in (
        (note, [(55, 112, 75)]),
        ("", [(128, 95, 48), (83, 111, 123)]),
        ("Synthetic text-only observation.", []),
    ):
        records.append(
            create_entry(
                author=user,
                date=date(2026, 5, 9),
                body=body,
                uploads=[image_upload(size=(640, 480), color=colour) for colour in colours],
                submission_key=uuid4(),
            ).entry
        )
    return records


@pytest.fixture
def page(live_server, sample_entries, browser_endpoint):
    with browser_page(browser_endpoint, viewport={"width": 390, "height": 844}) as page:
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        csp_monitor = """window.cspViolations = [];
            document.addEventListener('securitypolicyviolation', event =>
                window.cspViolations.push(event.violatedDirective));"""
        page.add_init_script(csp_monitor)
        page.evaluate(csp_monitor)
        yield page
        assert errors == []
        assert page.evaluate("window.cspViolations") == []


def choose(page, season):
    page.locator("#account-menu > summary").click()
    page.locator("#season").select_option(season)
    page.keyboard.press("Escape")
    expect(page.locator("html")).to_have_attribute("data-season-mode", season)


def luminance(value):
    if len(value) == 4:
        value = "#" + "".join(channel * 2 for channel in value[1:])
    channels = [int(value[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return sum(
        channel * weight for channel, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True)
    )


@pytest.mark.parametrize("language", ["sv", "en"])
def test_seasonal_colours_and_diary_layout(page, live_server, user, sample_entries, language):
    login(page, live_server, user, language)
    palettes = []
    for season in ("spring", "summer", "autumn", "winter"):
        choose(page, season)
        expect(page.locator("html")).to_have_attribute("data-season", season)
        palette = page.locator("html").evaluate("""element => {
            const style = getComputedStyle(element);
            return Object.fromEntries(['base-100', 'base-200', 'base-content', 'primary',
                'primary-content', 'secondary', 'accent', 'muted', 'focus', 'error',
                'error-content', 'success', 'success-content', 'warning', 'warning-content']
                .map(role => [role, style.getPropertyValue('--color-' + role).trim()]));
        }""")
        palettes.append(palette)
        for foreground, background in (
            ("base-content", "base-100"),
            ("base-content", "base-200"),
            ("primary-content", "primary"),
            ("secondary", "base-100"),
            ("muted", "base-200"),
            ("error-content", "error"),
            ("success-content", "success"),
            ("warning-content", "warning"),
        ):
            values = sorted((luminance(palette[foreground]), luminance(palette[background])))
            assert (values[1] + 0.05) / (values[0] + 0.05) >= 4.5, (season, foreground, background)
        focus = sorted((luminance(palette["focus"]), luminance(palette["base-100"])))
        assert (focus[1] + 0.05) / (focus[0] + 0.05) >= 3
        assert (
            page.locator(".diary-heading")
            .evaluate("element => getComputedStyle(element, '::before').backgroundImage")
            .endswith(f'/images/seasons/{season}.webp")')
        )
        page.wait_for_function("() => document.fonts.status === 'loaded'", timeout=8000)
        assert page.evaluate("document.fonts.check('32px \"Barlow Condensed\"')")
        note_card = page.locator(f"#entry-{sample_entries[0].pk}")
        expect(note_card.locator(".entry-body")).to_have_text(sample_entries[0].body)
        for width in (320, 390, 1280):
            page.set_viewport_size({"width": width, "height": 1000})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            photos = note_card.locator(".diary-photos").bounding_box()
            text = note_card.locator(".diary-card-note").bounding_box()
            if width < 800:
                assert photos["y"] + photos["height"] <= text["y"]
            else:
                assert photos["x"] + photos["width"] <= text["x"]
            expect(page.locator(".diary-card .photo-grid img")).to_have_count(3)
            page.wait_for_function("""() => [...document.querySelectorAll('.diary-photos img')]
                .every(image => image.complete && image.naturalWidth > 0)""")
            assert note_card.locator(".diary-photos img").bounding_box()["width"] >= 250
    assert len({palette["primary"] for palette in palettes}) == 4
    assert len({palette["base-200"] for palette in palettes}) == 4
    assert len({palette["base-content"] for palette in palettes}) == 4


@pytest.fixture(params=["", "Synthetic photo observation."])
def captioned_entries(request, user, image_upload):
    sizes = [(1600, 1200), (1200, 1600), (1600, 400)]
    observations = []
    caption = 'Synthetic <caption> & "photo" ' + "longcaption" * 20
    for photo_sizes in [[size] for size in sizes] + [sizes]:
        observation = create_entry(
            author=user,
            date=date(2026, 5, 9),
            body=request.param,
            uploads=[image_upload(size=size) for size in photo_sizes],
            submission_key=uuid4(),
        ).entry
        photo = observation.photos.first()
        update_entry_photo(
            actor=user,
            pk=photo.pk,
            caption=caption,
            position=photo.position,
            expected_updated_at=observation.updated_at,
        )
        observations.append((observation.pk, photo.get_absolute_url()))
    return observations, caption


@pytest.mark.parametrize("javascript", [False, True])
def test_diary_photo_captions_and_aspect_ratios(
    captioned_entries, live_server, user, browser_endpoint, javascript
):
    observations, caption = captioned_entries
    with browser_page(browser_endpoint, timeout=30000, java_script_enabled=javascript) as view:
        login(view, live_server, user)
        for width in (320, 390, 1280):
            view.set_viewport_size({"width": width, "height": 1000})
            for entry_id, photo_url in observations:
                gallery = view.locator(f"#entry-{entry_id} .diary-photos")
                expect(gallery.locator("figcaption")).to_have_text(caption)
                for image in gallery.locator("img").all():
                    wait_for_image(image)
                    dimensions = image.evaluate("""image => {
                        const box = image.getBoundingClientRect();
                        return {width: box.width, height: box.height,
                            ratio: image.naturalWidth / image.naturalHeight,
                            radius: parseFloat(getComputedStyle(image).borderTopLeftRadius)};
                    }""")
                    assert dimensions["width"] / dimensions["height"] == pytest.approx(
                        dimensions["ratio"], rel=0.01
                    )
                    assert dimensions["radius"] > 0
                    limit = view.locator("html").evaluate(
                        "element => parseFloat(getComputedStyle(element).fontSize) * 24"
                    )
                    assert max(dimensions["width"], dimensions["height"]) <= limit + 1
                figure = gallery.locator("figure").first
                expect(figure.locator("a")).to_have_attribute("href", photo_url)
            assert view.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("language", ["sv", "en"])
@pytest.mark.parametrize("width", [320, 1280])
def test_register_headings_and_fixed_illustrations_follow_the_season(
    page, live_server, user, language, width
):
    page.set_viewport_size({"width": width, "height": 1000})
    login(page, live_server, user, language)
    for path, heading, filename in (
        (
            "/garden/areas/",
            "Odlingsytor" if language == "sv" else "Growing areas",
            "growing-areas",
        ),
        (
            "/garden/varieties/",
            "Grödor och sorter" if language == "sv" else "Crops and varieties",
            "crops-and-varieties",
        ),
        (
            "/garden/plantings/?year=2026",
            ("Odlingar" if language == "sv" else "Plantings") + " · 2026",
            "plantings",
        ),
    ):
        page.goto(live_server.url + path)
        expect(page.locator("h1")).to_have_text(heading)
        navigation = page.get_by_role(
            "navigation", name="Huvudmeny" if language == "sv" else "Main navigation"
        )
        expect(navigation.locator("a")).to_have_count(4)
        expect(navigation.locator('[aria-current="page"]')).to_have_attribute(
            "href", path.split("?")[0]
        )
        illustration = page.locator(".register-illustration")
        source = f"/static/images/registers/{filename}.webp"
        expect(illustration).to_have_attribute("alt", "")
        wait_for_image(illustration)
        colours = []
        for season in ("spring", "summer", "autumn", "winter"):
            choose(page, season)
            expect(page.locator("html")).to_have_attribute("data-season", season)
            expect(illustration).to_have_attribute("src", source)
            expect(page.locator("h1")).to_have_text(heading)
            colours.append(
                page.evaluate("""() => [getComputedStyle(document.body).backgroundColor,
                    getComputedStyle(document.querySelector('h1')).color]""")
            )
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            title = page.locator("h1").bounding_box()
            art = illustration.bounding_box()
            assert title["x"] + title["width"] <= art["x"]
        assert len({background for background, _ in colours}) == 4
        assert len({text for _, text in colours}) == 4


@pytest.fixture
def sample_registers(user):
    crop = create_crop(actor=user, name="Synthetic crop").record
    area = create_area(actor=user, name="Synthetic field").record
    variety = create_variety(actor=user, crop=crop, name="Synthetic variety").record
    create_planting(
        actor=user,
        crop=crop,
        variety=variety,
        area=area,
        start_year=2026,
        label="Synthetic batch",
    )


@pytest.mark.usefixtures("sample_registers")
@pytest.mark.parametrize("language", ["sv", "en"])
@pytest.mark.parametrize("width", [320, 768, 1280])
def test_main_pages_align_and_keep_compact_controls_accessible(
    page, live_server, user, language, width
):
    page.set_viewport_size({"width": width, "height": 1000})
    login(page, live_server, user, language)
    edges = []
    for path in (
        "/",
        "/garden/areas/",
        "/garden/varieties/",
        "/garden/categories/all/",
        "/garden/plantings/?year=2026",
    ):
        page.goto(live_server.url + path)
        page.wait_for_function("() => document.fonts.status === 'loaded'", timeout=8000)
        main = page.locator("#main").bounding_box()
        title = page.locator("h1").bounding_box()
        edges.append((title["x"], title["y"] - main["y"], main["width"]))
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        if path == "/garden/varieties/":
            card = page.locator(".category-card")
            expect(card).to_have_count(1)
            expect(card).to_be_in_viewport(ratio=1)
            if width >= 768:
                assert card.bounding_box()["width"] < main["width"] / 2
            show_all = page.get_by_role(
                "link",
                name="Visa alla grödor och sorter"
                if language == "sv"
                else "Show all crops and varieties",
                exact=True,
            )
            new_category = page.get_by_role(
                "link", name="Ny kategori" if language == "sv" else "New category", exact=True
            )
            expect(show_all).to_be_visible()
            expect(new_category).to_be_visible()
            manage_categories = page.get_by_role(
                "link",
                name="Hantera kategorier" if language == "sv" else "Manage categories",
                exact=True,
            )
            expect(manage_categories).to_be_visible()
            show_all.focus()
            show_all.press("Tab")
            expect(manage_categories).to_be_focused()
            manage_categories.press("Tab")
            expect(new_category).to_be_focused()
            if width >= 1024:
                show_box = show_all.bounding_box()
                new_box = new_category.bounding_box()
                art = page.locator(".register-illustration").bounding_box()
                assert (
                    abs(
                        show_box["y"]
                        + show_box["height"] / 2
                        - new_box["y"]
                        - new_box["height"] / 2
                    )
                    < 2
                )
                assert show_box["x"] + show_box["width"] <= new_box["x"]
                assert new_box["x"] + new_box["width"] <= art["x"]
                assert abs(new_box["y"] + new_box["height"] - art["y"] - art["height"]) < 2
            expect(page.locator(".category-card img")).to_be_visible()
        elif path == "/garden/categories/all/":
            search = page.get_by_role(
                "searchbox",
                name="Sök grödor och sorter" if language == "sv" else "Search crops and varieties",
            )
            expect(search).to_be_visible()
            expect(page.locator(".variety-filter .related-actions")).to_be_hidden()
            if width >= 768:
                action = page.locator(".register-toolbar").get_by_role(
                    "link", name="Ny sort" if language == "sv" else "New variety", exact=True
                )
                assert abs(search.bounding_box()["y"] - action.bounding_box()["y"]) < 2
        elif path.startswith("/garden/plantings/"):
            action = page.locator("[data-filter-create]")
            summary = page.locator("[data-filter-disclosure] > summary")
            expect(action).to_be_visible()
            if width >= 768:
                assert abs(action.bounding_box()["y"] - summary.bounding_box()["y"]) < 2
            action.focus()
            action.press("Tab")
            category_management = page.locator("[data-filter-category-manage]")
            expect(category_management).to_be_focused()
            category_management.press("Tab")
            category_action = page.locator("[data-filter-category-create]")
            expect(category_action).to_be_focused()
            category_action.press("Tab")
            expect(summary).to_be_focused()
            summary.press("Enter")
            summary.press("Tab")
            expect(page.locator("#id_year")).to_be_focused()
            panel = page.locator("#filter-panel").bounding_box()
            results = page.locator("#planting-results").bounding_box()
            assert abs(panel["x"] - results["x"]) < 2
            assert abs(panel["width"] - results["width"]) < 2
            assert panel["y"] + panel["height"] <= results["y"]
    for edge in edges[1:]:
        assert all(
            abs(value - baseline) < 2 for value, baseline in zip(edge, edges[0], strict=True)
        ), edges


def test_season_choice_keeps_the_draft_and_files_and_is_browser_local(page, live_server, user):
    login(page, live_server, user)
    page.goto(live_server.url + "/?q=synthetic")
    page.locator("#id_body").fill("Unsubmitted synthetic draft")
    page.locator("#id_library").set_input_files(
        {"name": "synthetic.jpg", "mimeType": "image/jpeg", "buffer": b"selected"}
    )
    key = page.locator("#id_submission_key").input_value()
    choose(page, "winter")
    expect(page).to_have_url(live_server.url + "/?q=synthetic")
    expect(page.locator("#id_body")).to_have_value("Unsubmitted synthetic draft")
    expect(page.locator("#id_submission_key")).to_have_value(key)
    assert page.locator("#id_library").evaluate("input => input.files.length") == 1
    page.goto(live_server.url + "/garden/")
    expect(page.locator("html")).to_have_attribute("data-season", "winter")
    expect(page.locator(".diary-heading")).to_have_count(0)
    page.reload()
    expect(page.locator("html")).to_have_attribute("data-season", "winter")
    other = page.context.browser.new_context()
    try:
        other.add_cookies(
            [cookie for cookie in page.context.cookies() if cookie["name"] != SEASON_COOKIE]
        )
        view = other.new_page()
        view.goto(live_server.url + "/garden/")
        expect(view.locator("html")).to_have_attribute("data-season-mode", "auto")
    finally:
        other.close()


def test_automatic_season_changes_at_garden_midnight_and_manual_choice_stays_fixed(
    page, live_server, user
):
    page.clock.install(time=datetime(2026, 2, 28, 22, 59, 30, tzinfo=UTC))
    login(page, live_server, user)
    expect(page.locator("html")).to_have_attribute("data-season", "winter")
    page.clock.fast_forward(60000)
    expect(page.locator("html")).to_have_attribute("data-season", "spring")
    choose(page, "autumn")
    page.clock.set_system_time(datetime(2026, 6, 1, tzinfo=UTC))
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    expect(page.locator("html")).to_have_attribute("data-season", "autumn")
    choose(page, "auto")
    expect(page.locator("html")).to_have_attribute("data-season", "summer")


def test_season_form_and_colours_work_without_javascript(page, live_server, user):
    context = page.context.browser.new_context(java_script_enabled=False)
    try:
        plain = context.new_page()
        plain.goto(live_server.url + "/accounts/login/")
        plain.locator("#season").select_option("winter")
        plain.locator('.season-form button[type="submit"]').click()
        expect(plain.locator("html")).to_have_attribute("data-season", "winter")
        login(plain, live_server, user)
        plain.goto(live_server.url + "/?q=synthetic")
        plain.locator("#account-menu > summary").click()
        plain.locator("#season").select_option("spring")
        plain.locator('.season-form button[type="submit"]').click()
        expect(plain).to_have_url(live_server.url + "/?q=synthetic")
        expect(plain.locator("html")).to_have_attribute("data-season", "spring")
        plain.goto(live_server.url + "/garden/")
        expect(plain.locator("html")).to_have_attribute("data-season", "spring")
    finally:
        context.close()
