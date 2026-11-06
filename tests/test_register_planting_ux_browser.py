"""Real hierarchy/search interaction in Chromium, with ordinary HTML fallbacks."""

import os

import pytest
from playwright.sync_api import expect, sync_playwright

from garden.services import (
    archive_area,
    archive_crop,
    archive_variety,
    create_area,
    create_crop,
    create_planting,
    create_variety,
)

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def ux(user):
    pea = create_crop(actor=user, name="Ärtor", notes="Synthetic crop notes").record
    bean = create_crop(actor=user, name="Bean").record
    green = create_variety(actor=user, crop=pea, name="Åsa Grön").record
    duplicate = create_variety(actor=user, crop=bean, name="Åsa Grön").record
    old = create_variety(actor=user, crop=pea, name="Old trial").record
    root = create_area(actor=user, name="North", notes="Root notes").record
    child = create_area(actor=user, name="Bed", parent=root, notes="Child notes").record
    grandchild = create_area(actor=user, name="Section", parent=child).record
    sibling = create_area(actor=user, name="South", notes="Sibling notes").record
    create_area(actor=user, name="Border", parent=sibling)
    batches = []
    for label, area, variety, quantity in (
        ("Parent unknown", root, None, None),
        ("First batch", child, green, 0),
        ("Second batch", child, green, 7),
        ("Other batch", sibling, green, None),
        ("Unassigned batch", None, None, None),
    ):
        batches.append(
            create_planting(
                actor=user,
                crop=pea,
                variety=variety,
                area=area,
                start_year=2026,
                label=label,
                quantity=quantity,
                notes="Synthetic batch note",
            ).record
        )
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
    }


@pytest.fixture
def page(live_server, user, ux):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=os.environ.get("GROWBOOK_BROWSER_CHANNEL", "chromium")
        )
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.set_default_timeout(8000)
        yield page
        context.close()
        browser.close()


def login(page, live_server, user, language):
    page.goto(live_server.url + "/accounts/login/")
    page.locator("#id_username").fill(user.username)
    page.locator("#id_password").fill("Synthetic-password-42")
    page.get_by_role("button", name="Logga in", exact=True).click()
    expect(page).to_have_url(live_server.url + "/")
    page.context.add_cookies(
        [{"name": "django_language", "value": language, "url": live_server.url}]
    )


def branch(page, area):
    return page.locator(f'[data-area-id="{area.pk}"] > details')


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
    page.goto(live_server.url + "/garden/varieties/")
    search = page.get_by_role(
        "searchbox", name="Sök sorter" if language == "sv" else "Search varieties"
    )
    results = page.locator("#register-results")
    expect(results.locator(".variety-group")).to_have_count(2)
    search.fill("A\u030asa GRO\u0308N")
    search.press("Enter")
    expect(results.locator("tbody")).to_have_count(2)
    expect(search).to_be_focused()
    search.fill("ÄRTOR")
    search.press("Enter")
    expect(results.locator(".variety-group")).to_have_count(1)
    expect(results.locator("tbody")).to_have_count(1)
    page.locator(".archive-options > summary").click()
    page.locator("#id_archived").check()
    expect(results.locator("tbody")).to_have_count(2)
    expect(page).to_have_url(live_server.url + "/garden/varieties/?q=%C3%84RTOR&archived=1")
    hide = "Dölj arkiverade poster" if language == "sv" else "Hide archived records"
    results.get_by_role("link", name=hide, exact=True).click()
    expect(results.locator("tbody")).to_have_count(1)
    expect(search).to_have_value("ÄRTOR")
    page.go_back()
    expect(page.locator("#id_archived")).to_be_checked()
    expect(results.locator("tbody")).to_have_count(2)
    results.get_by_role(
        "link", name="Rensa sökning" if language == "sv" else "Clear search", exact=True
    ).click()
    expect(search).to_have_value("")
    expect(page.locator("#id_archived")).to_be_checked()
    expect(results.locator("tbody")).to_have_count(3)
    page.route(
        "**/garden/varieties/?*", lambda route: route.fulfill(status=503, body="Synthetic failure")
    )
    search.fill("Missing")
    search.press("Enter")
    expect(page.locator("#filter-status")).not_to_be_empty()
    expect(results.locator("tbody")).to_have_count(3)
    page.unroute("**/garden/varieties/?*")
    page.locator("[data-filter-retry]").click()
    expect(results.locator(".empty-state")).to_be_visible()
    expect(search).to_be_focused()
    expect(page.locator(".page-heading > a")).to_be_visible()
    search.fill("Åsa")
    search.press("Enter")
    expect(results.locator("tbody")).to_have_count(2)
    toggle = page.locator(f'[aria-controls="details-variety-{ux["green"].pk}"]')
    toggle.focus()
    toggle.press("Space")
    expect(page.locator(f"#details-variety-{ux['green'].pk}")).to_be_visible()
    expect(toggle).to_be_focused()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    results.locator(f'th > a:first-child[href="{ux["green"].get_absolute_url()}"]').click()
    expect(page).to_have_url(live_server.url + ux["green"].get_absolute_url())
    page.goto(live_server.url + "/garden/crops/?archived=1")
    page.locator(f'[aria-controls="details-crop-{ux["pea"].pk}"]').click()
    page.locator(f"#details-crop-{ux['pea'].pk}").get_by_role(
        "link", name="Åsa Grön", exact=True
    ).click()
    expect(page).to_have_url(live_server.url + ux["green"].get_absolute_url())
    assert errors == []


@pytest.mark.parametrize("language", ["sv", "en"])
@pytest.mark.parametrize("width", [390, 1280])
def test_area_branches_notes_and_grouped_batches_with_keyboard(
    page, live_server, user, ux, language, width
):
    page.set_viewport_size({"width": width, "height": 844})
    login(page, live_server, user, language)
    page.goto(live_server.url + "/garden/areas/")
    north, child, south = (
        branch(page, ux["root"]),
        branch(page, ux["child"]),
        branch(page, ux["sibling"]),
    )
    expect(north).not_to_have_attribute("open", "")
    expect(child.locator(":scope > summary")).to_be_hidden()
    north.locator(":scope > summary").focus()
    north.locator(":scope > summary").press("Enter")
    child.locator(":scope > summary").press("Space")
    south.locator(":scope > summary").click()
    toggle = page.locator(f'[aria-controls="details-area-{ux["child"].pk}"]')
    toggle.focus()
    toggle.press("Space")
    notes = page.locator(f"#details-area-{ux['child'].pk}")
    expect(notes).to_contain_text("Child notes")
    expect(toggle).to_be_focused()
    north.locator(":scope > summary").click()
    expect(notes).to_be_hidden()
    expect(south).to_have_attribute("open", "")
    north.locator(":scope > summary").click()
    expect(child).to_have_attribute("open", "")
    expect(notes).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.goto(live_server.url + "/garden/plantings/?year=2026")
    north, child, south = (
        branch(page, ux["root"]),
        branch(page, ux["child"]),
        branch(page, ux["sibling"]),
    )
    expect(north).not_to_have_attribute("open", "")
    expect(child).to_have_attribute("open", "")
    expect(south).to_have_attribute("open", "")
    expect(page.get_by_role("link", name="First batch", exact=True)).to_be_hidden()
    north.locator(":scope > summary").press("Space")
    expect(page.get_by_role("link", name="First batch", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="Second batch", exact=True)).to_be_visible()
    expect(child.locator(".planting-table")).to_contain_text("0")
    expect(page.locator(".unassigned-group")).to_contain_text("Unassigned batch")
    page.get_by_role(
        "button", name=("Detaljer" if language == "sv" else "Details") + ": First batch", exact=True
    ).click()
    expect(page.locator(f"#details-planting-{ux['batches'][1].pk}")).to_contain_text(
        "Synthetic batch note"
    )
    if width < 900:
        page.locator("#open-filters").click()
    page.locator("#id_variety").select_option(str(ux["green"].pk))
    expect(north).to_have_attribute("open", "")
    if width < 900:
        page.keyboard.press("Escape")
    expect(page.get_by_role("link", name="First batch", exact=True)).to_be_visible()
    page.go_back()
    expect(north).not_to_have_attribute("open", "")
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
    plain.goto(live_server.url + "/garden/varieties/")
    plain.locator("#id_q").fill("ÄRTOR")
    plain.locator(".archive-options > summary").click()
    plain.locator("#id_archived").check()
    plain.get_by_role("button", name="Visa" if language == "sv" else "Apply", exact=True).click()
    expect(plain.locator(".variety-group")).to_have_count(1)
    expect(plain.locator("tbody")).to_have_count(2)
    plain.get_by_role(
        "link", name="Rensa sökning" if language == "sv" else "Clear search", exact=True
    ).click()
    expect(plain.locator("#id_archived")).to_be_checked()
    plain.goto(live_server.url + "/garden/areas/")
    branch(plain, ux["root"]).locator(":scope > summary").press("Enter")
    branch(plain, ux["child"]).locator(":scope > summary").press("Space")
    expect(plain.locator(f'[data-area-id="{ux["grandchild"].pk}"]')).to_be_visible()
    plain.locator(f'[aria-controls="details-area-{ux["child"].pk}"]').click()
    expect(plain).to_have_url(live_server.url + ux["child"].get_absolute_url())
    plain.goto(live_server.url + "/garden/plantings/?year=2026")
    branch(plain, ux["root"]).locator(":scope > summary").press("Space")
    plain.get_by_role("link", name="Second batch", exact=True).click()
    expect(plain).to_have_url(live_server.url + ux["batches"][2].get_absolute_url())
    context.close()


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
    page.goto(live_server.url + "/garden/varieties/")
    search = page.locator("#id_q")
    search.fill("Bean")
    page.wait_for_function("() => window.heldSearch.ready")
    search.fill("ÄRTOR")
    assert page.evaluate("window.heldSearch.aborted")
    expect(page.locator(".variety-group")).to_have_count(1)
    expect(page.locator(".variety-group > h2")).to_contain_text("Ärtor")
    page.evaluate("""async () => {
        window.heldSearch.release();
        await new Promise(resolve => requestAnimationFrame(resolve));
    }""")
    expect(page.locator(".variety-group")).to_have_count(1)
    expect(page.locator(".variety-group > h2")).to_contain_text("Ärtor")
    expect(page.locator("#id_q")).to_be_focused()
    expect(page.locator("#filter-status")).to_be_empty()
    assert "Bean" not in page.locator("#register-results").inner_text()
