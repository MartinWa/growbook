"""Real hierarchy/search interaction in Chromium, with ordinary HTML fallbacks."""

from datetime import date
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import pytest
from browser_helpers import browser_page, login, wait_for_image
from django.utils import timezone
from playwright.sync_api import expect

from diary.services import create_entry
from garden.services import (
    archive_area,
    archive_crop,
    archive_variety,
    create_area,
    create_category,
    create_crop,
    create_planting,
    create_variety,
)

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def ux(user, image_upload):
    pea = create_crop(actor=user, name="Ärtor", notes="Synthetic crop notes").record
    bean = create_crop(actor=user, name="Bean").record
    green = create_variety(
        actor=user,
        crop=pea,
        name="Åsa Grön",
        description="Synthetic variety details",
        uploads=[image_upload()],
    ).record
    create_entry(
        author=user,
        date=date(2026, 5, 9),
        body="Synthetic linked variety observation",
        uploads=[],
        submission_key=uuid4(),
        links=[green],
    )
    duplicate = create_variety(actor=user, crop=bean, name="Åsa Grön").record
    old = create_variety(actor=user, crop=pea, name="Old trial").record
    root = create_area(actor=user, name="North", notes="Root notes").record
    sibling = create_area(actor=user, name="South", notes="Sibling notes").record
    areas = {"root": root, "sibling": sibling}
    batches = []
    for label, area, variety, quantity in (
        ("Parent unknown", "root", None, None),
        ("First batch", "child", green, 0),
        ("Second batch", "child", green, 7),
        ("Other batch", "sibling", green, None),
        ("Unassigned batch", None, None, None),
    ):
        if area == "child" and area not in areas:
            areas[area] = create_area(
                actor=user, name="Bed", parent=root, notes="Child notes"
            ).record
        batches.append(
            create_planting(
                actor=user,
                crop=pea,
                variety=variety,
                area=areas.get(area),
                start_year=2026,
                label=label,
                quantity=quantity,
                notes="Synthetic batch note",
            ).record
        )
    child = areas["child"]
    grandchild = create_area(actor=user, name="Section", parent=child).record
    create_area(actor=user, name="Border", parent=sibling)
    archive_variety(actor=user, pk=old.pk)
    archive_crop(actor=user, pk=pea.pk)
    archive_area(actor=user, pk=root.pk)
    return {
        "pea": pea,
        "green": green,
        "duplicate": duplicate,
        "old": old,
        "root": root,
        "child": child,
        "grandchild": grandchild,
        "sibling": sibling,
        "batches": batches,
        "current_year": timezone.localdate().year,
    }


@pytest.fixture
def register_scope(request, user, ux):
    scope = getattr(request, "param", "all")
    if scope == "category":
        archive_crop(actor=user, pk=ux["pea"].pk, archived=False)
        category = create_category(actor=user, name="Vegetables", crops=[ux["pea"]]).record
        archive_crop(actor=user, pk=ux["pea"].pk)
        return str(category.pk)
    return scope


@pytest.fixture
def page(live_server, user, ux, register_scope, browser_endpoint):
    with browser_page(browser_endpoint, viewport={"width": 1280, "height": 900}) as page:
        yield page


def branch(page, area):
    return page.locator(f'[data-area-id="{area.pk}"] > details')


@pytest.mark.parametrize("javascript", [False, True])
@pytest.mark.parametrize("archived", [False, True])
def test_search_enter_preserves_archive_visibility(
    page, live_server, user, ux, javascript, archived
):
    login(page, live_server, user, "en")
    context = page.context.browser.new_context(
        java_script_enabled=javascript,
        storage_state=page.context.storage_state(),
        viewport={"width": 390, "height": 844},
    )
    try:
        search_page = context.new_page()
        path = "/garden/categories/all/" + ("?archived=1" if archived else "")
        search_page.goto(live_server.url + path)
        search_page.locator("#id_q").fill("ÄRTOR")
        with search_page.expect_response(
            lambda response: (
                response.status == 200 and urlsplit(response.url).path == "/garden/categories/all/"
            )
        ) as loaded:
            search_page.locator("#id_q").press("Enter")
        expected = {"q": ["ÄRTOR"], **({"archived": ["1"]} if archived else {})}
        assert parse_qs(urlsplit(loaded.value.url).query) == expected
        expect(search_page.locator(".variety-list > li")).to_have_count(2 if archived else 1)
        expect(search_page.locator("[data-archive-toggle]")).to_have_attribute(
            "aria-pressed", str(archived).lower()
        )
        assert parse_qs(urlsplit(search_page.url).query) == expected
    finally:
        context.close()


@pytest.mark.parametrize("failure", ["network", "server"])
@pytest.mark.parametrize("archived", [False, True])
def test_archive_toggle_retry_repeats_failed_filters(
    page, live_server, user, ux, failure, archived
):
    login(page, live_server, user, "en")
    filters = {"q": "ÄRTOR", **({"archived": "1"} if archived else {})}
    path = "/garden/categories/all/?" + urlencode(filters)
    page.goto(live_server.url + path)
    records = page.locator("#register-results .variety-list > li")
    expect(records).to_have_count(2 if archived else 1)

    def fail_request(route):
        if failure == "network":
            route.abort()
        else:
            route.fulfill(status=503, body="Synthetic failure")

    page.route("**/garden/categories/all/?*", fail_request)
    page.locator("[data-archive-toggle]").click()
    expect(page.locator("#filter-status")).not_to_be_empty()
    expect(records).to_have_count(2 if archived else 1)
    expect(page).to_have_url(live_server.url + path)
    expect(page.locator("[data-archive-toggle]")).to_have_attribute(
        "aria-pressed", str(archived).lower()
    )
    page.unroute("**/garden/categories/all/?*")
    with page.expect_response(
        lambda response: urlsplit(response.url).path == "/garden/categories/all/"
    ) as loaded:
        page.get_by_role("button", name="Retry", exact=True).click()
    expected = {"q": ["ÄRTOR"], **({"archived": ["1"]} if not archived else {})}
    assert parse_qs(urlsplit(loaded.value.url).query) == expected
    expect(records).to_have_count(1 if archived else 2)
    expect(page.locator("[data-archive-toggle]")).to_have_attribute(
        "aria-pressed", str(not archived).lower()
    )
    expect(page.locator("#id_q")).to_be_focused()
    expect(page.locator("#filter-status")).to_be_empty()
    expect(page.get_by_role("button", name="Retry", exact=True)).to_be_hidden()
    assert parse_qs(urlsplit(page.url).query) == expected


def test_new_search_supersedes_failed_archive_toggle(page, live_server, user, ux):
    login(page, live_server, user, "en")
    page.goto(live_server.url + "/garden/categories/all/?" + urlencode({"q": "ÄRTOR"}))
    page.route(
        "**/garden/categories/all/?*",
        lambda route: route.fulfill(status=503, body="Synthetic failure"),
    )
    page.locator("[data-archive-toggle]").click()
    retry = page.get_by_role("button", name="Retry", exact=True)
    expect(retry).to_be_visible()
    page.unroute("**/garden/categories/all/?*")
    page.locator("#id_q").fill("Bean")
    assert not retry.is_visible()
    with page.expect_response(
        lambda response: urlsplit(response.url).path == "/garden/categories/all/"
    ) as loaded:
        page.locator("#id_q").press("Enter")
    assert parse_qs(urlsplit(loaded.value.url).query) == {"q": ["Bean"]}
    expect(page.locator(".variety-group > summary")).to_have_text("Bean")
    expect(page.locator("[data-archive-toggle]")).to_have_attribute("aria-pressed", "false")


@pytest.mark.parametrize("kind", ["area", "variety"])
def test_cancel_from_new_edit_tab_opens_record(page, live_server, user, ux, kind):
    login(page, live_server, user, "en")
    if kind == "area":
        record = ux["child"]
        page.goto(live_server.url + record.get_absolute_url())
        details = page.locator(".area-details")
    else:
        record = ux["green"]
        page.goto(live_server.url + "/garden/categories/all/")
        page.locator(f'[data-crop-id="{record.crop_id}"] > summary').click()
        page.locator(f'[aria-controls="details-{kind}-{record.pk}"]').click()
        details = page.locator(f"#details-{kind}-{record.pk}")
    list_url = page.url
    with page.context.expect_page() as opened:
        details.get_by_role("link", name="Edit", exact=True).click(button="middle")
    edit_tab = opened.value
    try:
        expect(edit_tab.locator("#register-form")).to_be_visible()
        assert edit_tab.evaluate("document.referrer") == list_url
        edit_tab.get_by_role("link", name="Cancel", exact=True).click()
        expect(edit_tab).to_have_url(live_server.url + record.get_absolute_url())
        expect(page).to_have_url(list_url)
    finally:
        edit_tab.close()


@pytest.mark.parametrize("language", ["sv", "en"])
@pytest.mark.parametrize("width", [390, 1280])
def test_variety_search_clear_back_retry_and_direct_links(
    page, live_server, user, ux, language, width
):
    page.set_viewport_size({"width": width, "height": 844})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script(
        "document.addEventListener('securitypolicyviolation', e => {"
        " throw new Error(e.effectiveDirective); });"
    )
    login(page, live_server, user, language)
    page.goto(live_server.url + "/garden/categories/all/")
    search = page.get_by_role(
        "searchbox",
        name="Sök grödor och sorter" if language == "sv" else "Search crops and varieties",
    )
    results = page.locator("#register-results")
    expect(results.locator(".variety-group")).to_have_count(2)
    group = results.locator(f'[data-crop-id="{ux["pea"].pk}"]')
    expect(group).not_to_have_attribute("open", "")
    expect(group.locator(":scope > summary a")).to_have_count(0)
    group.locator(":scope > summary").press("Enter")
    expect(group.get_by_role("button", name="Åsa Grön", exact=True)).to_be_visible()
    thumbnail = group.locator(".variety-thumbnail")
    expect(thumbnail).to_be_visible()
    wait_for_image(thumbnail)
    group.locator(":scope > summary").press("Space")
    expect(group.get_by_role("button", name="Åsa Grön", exact=True)).to_be_hidden()
    search.fill("A\u030asa GRO\u0308N")
    search.press("Enter")
    expect(page).to_have_url(
        live_server.url + "/garden/categories/all/?" + urlencode({"q": "A\u030asa GRO\u0308N"})
    )
    expect(results.locator(".variety-list > li")).to_have_count(2)
    expect(page.locator("[data-archive-toggle]")).to_have_attribute("aria-pressed", "false")
    expect(search).to_be_focused()
    search.fill("ÄRTOR")
    search.press("Enter")
    expect(results.locator(".variety-group")).to_have_count(1)
    expect(results.locator(".variety-list > li")).to_have_count(1)
    page.locator("[data-archive-toggle]").click()
    expect(results.locator(".variety-list > li")).to_have_count(2)
    expect(page).to_have_url(live_server.url + "/garden/categories/all/?q=%C3%84RTOR&archived=1")
    hide = "Dölj arkiverade poster" if language == "sv" else "Hide archived records"
    results.get_by_role("link", name=hide, exact=True).click()
    expect(results.locator(".variety-list > li")).to_have_count(1)
    expect(search).to_have_value("ÄRTOR")
    page.go_back()
    expect(page.locator("[data-archive-toggle]")).to_have_attribute("aria-pressed", "true")
    expect(results.locator(".variety-list > li")).to_have_count(2)
    results.get_by_role(
        "link", name="Rensa sökning" if language == "sv" else "Clear search", exact=True
    ).click()
    expect(search).to_have_value("")
    expect(page.locator("[data-archive-toggle]")).to_have_attribute("aria-pressed", "true")
    expect(results.locator(".variety-list > li")).to_have_count(3)
    page.route(
        "**/garden/categories/all/?*",
        lambda route: route.fulfill(status=503, body="Synthetic failure"),
    )
    search.fill("Missing")
    search.press("Enter")
    expect(page.locator("#filter-status")).not_to_be_empty()
    expect(results.locator(".variety-list > li")).to_have_count(3)
    page.unroute("**/garden/categories/all/?*")
    page.locator("[data-filter-retry]").click()
    expect(results.locator(".empty-state")).to_be_visible()
    expect(search).to_be_focused()
    expect(page.locator('.register-toolbar a[href^="/garden/varieties/new/"]')).to_be_visible()
    search.fill("Åsa")
    search.press("Enter")
    expect(results.locator(".variety-list > li")).to_have_count(2)
    toggle = page.locator(f'[aria-controls="details-variety-{ux["green"].pk}"]')
    toggle.focus()
    toggle.press("Space")
    expect(page.locator(f"#details-variety-{ux['green'].pk}")).to_be_visible()
    expect(
        page.locator(f"#details-variety-{ux['green'].pk} .register-photos figure")
    ).to_have_count(1)
    observations = page.locator(f"#diary-observations-variety-{ux['green'].pk}")
    expect(page.locator(f"#details-variety-{ux['green'].pk} h2").first).to_be_in_viewport()
    return_button = page.get_by_role(
        "button", name="Tillbaka till listan" if language == "sv" else "Back to list", exact=True
    )
    return_button.focus()
    return_button.press("Enter")
    expect(toggle).to_be_focused()
    expect(observations).not_to_have_attribute("open", "")
    observations.locator(":scope > summary").press("Enter")
    expect(page.locator(f"#details-variety-{ux['green'].pk}")).to_contain_text(
        "Synthetic linked variety observation"
    )
    expect(observations.locator(":scope > summary")).to_be_focused()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.locator(
        f'#details-variety-{ux["green"].pk} a[href="{ux["green"].get_absolute_url()}"]'
    ).click()
    expect(page).to_have_url(live_server.url + ux["green"].get_absolute_url())
    page.goto(live_server.url + "/garden/crops/?archived=1")
    expect(page).to_have_url(live_server.url + "/garden/categories/all/?archived=1")
    page.locator(".crop-maintenance > summary").click()
    page.locator(f'.crop-maintenance a[href="{ux["pea"].get_absolute_url()}"]').click()
    page.locator(".crop-varieties").get_by_role("link", name="Åsa Grön", exact=True).click()
    expect(page).to_have_url(live_server.url + ux["green"].get_absolute_url())
    assert errors == []


@pytest.mark.parametrize("language", ["sv", "en"])
@pytest.mark.parametrize("width", [390, 1280])
def test_area_pages_and_grouped_batches_with_keyboard(page, live_server, user, ux, language, width):
    page.set_viewport_size({"width": width, "height": 844})
    login(page, live_server, user, language)
    page.goto(live_server.url + "/garden/areas/")
    expect(page.locator(".area-card")).to_have_count(2)
    page.get_by_role("link", name=ux["root"].name, exact=True).press("Enter")
    expect(page).to_have_url(live_server.url + ux["root"].get_absolute_url())
    page.get_by_role("link", name=ux["child"].name, exact=True).press("Enter")
    expect(page).to_have_url(live_server.url + ux["child"].get_absolute_url())
    notes = page.locator(".area-details")
    expect(notes).to_contain_text("Child notes")
    expect(notes.locator(".record-fields")).not_to_contain_text(ux["root"].name)
    expect(page.locator(".area-card")).to_have_count(1)
    expect(page.locator(".area-card")).to_contain_text(ux["grandchild"].name)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.goto(live_server.url + "/garden/plantings/?year=2026")
    expect(page.locator(".category-card")).to_have_count(3)
    page.locator(".category-card-link").filter(
        has_text="Planerad" if language == "sv" else "Planned"
    ).press("Enter")
    expect(page.get_by_role("button", name="First batch", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Second batch", exact=True)).to_be_visible()
    expect(page.locator(".planting-list")).not_to_contain_text("Synthetic batch note")
    expect(page.locator(".planting-list")).to_contain_text("Unassigned batch")
    page.get_by_role("button", name="First batch", exact=True).click()
    expect(page.locator(f"#details-cycle-{ux['batches'][1].cycle_id}")).to_contain_text(
        "Synthetic batch note"
    )
    expect(
        page.locator(f"#details-cycle-{ux['batches'][1].cycle_id} .record-fields").last
    ).to_contain_text("0")
    second = page.get_by_role("button", name="Second batch", exact=True)
    second.focus()
    second.press("Space")
    expect(page.locator(f"#details-cycle-{ux['batches'][2].cycle_id}")).to_contain_text(
        "Synthetic batch note"
    )
    expect(page.locator(f"#details-cycle-{ux['batches'][1].cycle_id}")).to_be_hidden()
    expect(second).to_be_focused()
    page.locator("[data-filter-disclosure] > summary").click()
    page.locator("#id_variety").select_option(str(ux["green"].pk))
    expect(page).to_have_url(
        live_server.url + f"/garden/plantings/status/planned/?year=2026&variety={ux['green'].pk}"
    )
    expect(page.locator("[data-record-placeholder]")).to_be_visible()
    if width < 900:
        page.keyboard.press("Escape")
    expect(page.get_by_role("button", name="First batch", exact=True)).to_be_visible()
    page.go_back()
    expect(page.get_by_role("button", name="Unassigned batch", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("language", ["sv", "en"])
def test_no_javascript_search_and_native_hierarchies(page, live_server, user, ux, language):
    login(page, live_server, user, language)
    context = page.context.browser.new_context(
        java_script_enabled=False,
        storage_state=page.context.storage_state(),
        viewport={"width": 390, "height": 844},
    )
    plain = context.new_page()
    plain.goto(live_server.url + "/garden/categories/all/")
    expect(plain.locator("[data-record-detail-pane]")).to_be_hidden()
    plain.locator("#id_q").fill("ÄRTOR")
    plain.locator("[data-archive-toggle]").click()
    expect(plain.locator(".variety-group")).to_have_count(1)
    expect(plain.locator(".variety-list > li")).to_have_count(2)
    plain.get_by_role(
        "link", name="Rensa sökning" if language == "sv" else "Clear search", exact=True
    ).click()
    expect(plain.locator("[data-archive-toggle]")).to_have_attribute("data-pressed", "true")
    plain.goto(live_server.url + "/garden/areas/")
    plain.get_by_role("link", name=ux["root"].name, exact=True).press("Enter")
    plain.get_by_role("link", name=ux["child"].name, exact=True).press("Enter")
    expect(plain.locator(f'[data-area-id="{ux["grandchild"].pk}"]')).to_be_visible()
    expect(plain).to_have_url(live_server.url + ux["child"].get_absolute_url())
    plain.goto(live_server.url + "/garden/plantings/?year=2026")
    plain.locator(".category-card-link").filter(
        has_text="Planerad" if language == "sv" else "Planned"
    ).click()
    plain.get_by_role("link", name="Second batch", exact=True).click()
    assert plain.url.startswith(live_server.url + ux["batches"][2].cycle.get_absolute_url())
    context.close()


@pytest.mark.parametrize("language", ["sv", "en"])
@pytest.mark.parametrize("width", [390, 1280])
def test_area_information_opens_plantings_with_year_and_area_selected(
    page, live_server, user, ux, language, width
):
    page.set_viewport_size({"width": width, "height": 844})
    login(page, live_server, user, language)
    page.goto(live_server.url + "/garden/areas/")
    page.get_by_role("link", name=ux["root"].name, exact=True).press("Enter")
    page.get_by_role("link", name=ux["child"].name, exact=True).press("Enter")
    details = page.locator(".area-details")
    expect(details).to_contain_text("Child notes")
    expect(details.locator(".record-fields")).not_to_contain_text("North")
    year = ux["current_year"]
    target = f"/garden/plantings/?year={year}&area={ux['child'].pk}&area_only=1"
    link = details.locator(f'a[href="{target}"]')
    expect(link).to_have_attribute("href", target)
    link.focus()
    link.press("Enter")
    expect(page).to_have_url(live_server.url + target)
    expect(page.locator("#id_year")).to_have_value(str(year))
    expect(page.locator("#id_area")).to_have_value(str(ux["child"].pk))
    if year == 2026:
        page.locator(".category-card-link").filter(
            has_text="Planerad" if language == "sv" else "Planned"
        ).click()
        expect(page.locator("#planting-results")).to_contain_text("First batch")
        expect(page.locator("#planting-results")).not_to_contain_text("Other batch")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_debounced_search_discards_a_response_after_the_draft_changes(page, live_server, user):
    page.add_init_script("""(() => {
        const original = window.fetch;
        window.heldSearch = { ready: false, aborted: false };
        window.fetch = async (url, options) => {
            if (new URL(url).searchParams.get('q') !== 'Bean') return original(url, options);
            const response = await original(url, options);
            const body = await response.text();
            options.signal.addEventListener('abort', () => { window.heldSearch.aborted = true; });
            return new Promise(resolve => {
                window.heldSearch.ready = true;
                window.heldSearch.release = () => resolve(new Response(body, { status: 200 }));
            });
        };
    })();""")
    login(page, live_server, user, "en")
    page.goto(live_server.url + "/garden/categories/all/")
    search = page.locator("#id_q")
    search.fill("Bean")
    page.wait_for_function("() => window.heldSearch.ready")
    search.fill("ÄRTOR")
    assert page.evaluate("window.heldSearch.aborted")
    expect(page.locator(".variety-group")).to_have_count(1)
    expect(page.locator(".variety-group > summary")).to_contain_text("Ärtor")
    page.evaluate("""async () => {
        window.heldSearch.release();
        await new Promise(resolve => requestAnimationFrame(resolve));
    }""")
    expect(page.locator(".variety-group")).to_have_count(1)
    expect(page.locator(".variety-group > summary")).to_contain_text("Ärtor")
    expect(page.locator("#id_q")).to_be_focused()
    expect(page.locator("#filter-status")).to_be_empty()
    assert "Bean" not in page.locator("#register-results").inner_text()


@pytest.mark.parametrize("language", ["sv", "en"])
@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("action", ["back", "cancel"])
def test_edit_return_to_area_page_preserves_archive_choice(
    page, live_server, user, ux, language, width, action
):
    page.set_viewport_size({"width": width, "height": 400})
    login(page, live_server, user, language)
    page.goto(live_server.url + "/garden/areas/?archived=1")
    page.get_by_role("link", name=ux["root"].name, exact=True).press("Enter")
    page.get_by_role("link", name=ux["child"].name, exact=True).press("Enter")
    area_url = page.url
    details = page.locator(".area-details")
    edit = details.get_by_role("link", name="Redigera" if language == "sv" else "Edit", exact=True)
    edit.scroll_into_view_if_needed()
    edit.click()
    expect(page.locator("#register-form")).to_be_visible()
    if action == "back":
        page.go_back()
    else:
        page.get_by_role(
            "link", name="Avbryt" if language == "sv" else "Cancel", exact=True
        ).click()
    expect(page).to_have_url(area_url)
    expect(details).to_contain_text("Child notes")
    expect(page.locator("[data-archive-toggle]")).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".area-card")).to_contain_text(ux["grandchild"].name)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("language", ["sv", "en"])
def test_main_navigation_and_archive_toggle_are_keyboard_accessible(
    page, live_server, user, ux, language
):
    login(page, live_server, user, language)
    page.goto(live_server.url + "/garden/")
    expect(page).to_have_url(live_server.url + "/garden/areas/")
    navigation = page.get_by_role(
        "navigation", name="Huvudmeny" if language == "sv" else "Main navigation"
    )
    expect(navigation.locator("a")).to_have_count(4)
    expect(navigation.locator('[aria-current="page"]')).to_have_attribute("href", "/garden/areas/")
    for path, name in (
        ("/garden/varieties/", "Grödor och sorter" if language == "sv" else "Crops and varieties"),
        ("/garden/plantings/", "Odlingar" if language == "sv" else "Plantings"),
        ("/", "Dagbok" if language == "sv" else "Diary"),
    ):
        link = navigation.get_by_role("link", name=name, exact=True)
        link.focus()
        link.press("Enter")
        expect(page).to_have_url(live_server.url + path)
        expect(navigation.locator('[aria-current="page"]')).to_have_attribute("href", path)
    page.goto(live_server.url + "/garden/areas/")
    toggle = page.get_by_role(
        "button", name="Visa arkiverade" if language == "sv" else "Include archived", exact=True
    )
    expect(toggle).to_have_attribute("aria-pressed", "false")
    toggle.focus()
    toggle.press("Space")
    expect(page).to_have_url(live_server.url + "/garden/areas/?archived=1")
    expect(toggle).to_have_attribute("aria-pressed", "true")
    toggle.press("Enter")
    expect(page).to_have_url(live_server.url + "/garden/areas/")
    expect(toggle).to_have_attribute("aria-pressed", "false")


@pytest.fixture
def observation_history(user, ux):
    for day in range(1, 7):
        create_entry(
            author=user,
            date=date(2026, 6, day),
            body=f"Older variety observation {day}",
            uploads=[],
            submission_key=uuid4(),
            links=[ux["green"]],
        )


@pytest.mark.parametrize("register_scope", ["all", "uncategorized", "category"], indirect=True)
def test_variety_observations_and_edit_return_keep_search_and_history_access(
    observation_history, page, live_server, user, ux, tmp_path, register_scope
):
    login(page, live_server, user, "en")
    path = f"/garden/categories/{register_scope}/?q=Gr%C3%B6n&archived=1"
    page.goto(live_server.url + path)
    toggle = page.locator(f'[aria-controls="details-variety-{ux["green"].pk}"]')
    toggle.click()
    details = page.locator(f"#details-variety-{ux['green'].pk}")
    observations = details.locator(".observation-disclosure")
    expect(observations.locator(":scope > summary")).to_have_text("Diary observations (7)")
    expect(observations.locator(".observation-list")).to_be_hidden()
    observations.locator(":scope > summary").press("Enter")
    expect(observations.locator(".observation-list > li")).to_have_count(5)
    page.screenshot(path=str(tmp_path / "garden-variety-observations.png"), full_page=True)
    details.get_by_role("link", name="Edit", exact=True).click()
    page.get_by_role("link", name="Cancel", exact=True).click()
    expect(page).to_have_url(live_server.url + path)
    expect(page.locator("#id_q")).to_have_value("Grön")
    expect(page.locator("[data-archive-toggle]")).to_have_attribute("aria-pressed", "true")
    expect(observations).to_have_attribute("open", "")
    expect(toggle).to_be_focused()
    observations.get_by_role("link", name="View all observations", exact=True).click()
    expect(page).to_have_url(live_server.url + f"/?variety={ux['green'].pk}")
    expect(page.locator("#diary-results")).to_contain_text("Older variety observation 1")
