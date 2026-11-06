"""Real Chromium checks for the shared tagging flow; no physical-device claims."""

import os
import re
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from django.db import close_old_connections, connection
from playwright.sync_api import expect, sync_playwright

from diary.models import DiaryEntry
from garden.services import archive_crop, create_area, create_crop, create_planting, create_variety

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def browser_tags(user):
    crop = create_crop(actor=user, name="Tomato").record
    variety = create_variety(actor=user, crop=crop, name="Golden Cherry").record
    parent = create_area(actor=user, name="Glasshouse").record
    area = create_area(actor=user, name="West bed", parent=parent).record

    def planting(year, label, **kwargs):
        return create_planting(
            actor=user,
            crop=crop,
            variety=variety,
            area=area,
            start_year=year,
            label=label,
            **kwargs,
        ).record

    first, second = planting(2026, "Batch A"), planting(2026, "Batch B")
    older = planting(2025, "Earlier batch")
    ongoing = planting(2022, "Established", lifespan="persistent")
    ended = planting(2022, "Ended", lifespan="persistent", end_year=2024)
    unknown = create_planting(actor=user, crop=crop, start_year=2026).record
    archived = create_crop(actor=user, name="Archived crop").record
    archive_crop(actor=user, pk=archived.pk)
    return SimpleNamespace(
        crop=crop,
        variety=variety,
        area=area,
        parent=parent,
        first=first,
        second=second,
        older=older,
        ongoing=ongoing,
        ended=ended,
        unknown=unknown,
    )


@pytest.fixture
def page(live_server, browser_tags):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=os.environ.get("GROWBOOK_BROWSER_CHANNEL", "chromium")
        )
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        page.set_default_timeout(8000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        yield page
        context.close()
        browser.close()
        assert errors == []


def login(page, live_server, user, language="sv"):
    page.context.add_cookies(
        [{"name": "django_language", "value": language, "url": live_server.url}]
    )
    page.goto(live_server.url + "/accounts/login/")
    page.locator("#id_username").fill(user.username)
    page.locator("#id_password").fill("Synthetic-password-42")
    page.locator('.login-panel button[type="submit"]').click()
    expect(page).to_have_url(live_server.url + "/")


def row(page, name, pk):
    return page.locator(f'[data-tag-option]:has(input[name="{name}"][value="{pk}"])')


def saved_state():
    def read():
        close_old_connections()
        try:
            entry = DiaryEntry.objects.get()
            return dict(
                pk=entry.pk,
                body=entry.body,
                photos=entry.photos.count(),
                links=list(
                    entry.links.values_list("planting_id", "area_id", "crop_id", "variety_id")
                ),
            )
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(read).result(timeout=10)


@pytest.mark.parametrize("language", ["sv", "en"])
@pytest.mark.parametrize("mode", ["quick", "edit"])
def test_shared_picker_whole_rows_focus_stationary_selection_and_save(
    page, live_server, user, browser_tags, mode, language, tmp_path
):
    login(page, live_server, user, language)
    if mode == "edit":
        page.locator("#id_body").fill("Older unlinked observation")
        page.locator("#save-entry").click()
        page.get_by_role(
            "link", name="Redigera" if language == "sv" else "Edit", exact=True
        ).click()
    page.locator("#id_date").fill("2026-07-14")
    page.locator("#id_body").fill("Compare the batches")
    trigger = page.locator("[data-tag-trigger]")
    popover = page.locator("[data-tag-popover]")
    save = page.locator('form:has([data-tag-picker]) button[type="submit"]')
    expect(trigger).to_have_accessible_name(
        re.compile("Lägg till taggar" if language == "sv" else "Add tags")
    )
    actions = page.locator(".capture-actions")
    expect(actions.locator("[data-tag-trigger]")).to_be_visible()
    expect(actions.locator("[data-photo-chooser]")).to_be_visible()
    page.set_viewport_size({"width": 1280, "height": 844})
    tag_box = trigger.bounding_box()
    photo_box = actions.locator("[data-photo-chooser]").bounding_box()
    save_box = save.bounding_box()
    assert tag_box["x"] < photo_box["x"] < save_box["x"]
    assert tag_box["y"] == photo_box["y"] == save_box["y"]
    action_box = actions.bounding_box()
    assert abs(save_box["x"] + save_box["width"] - action_box["x"] - action_box["width"]) < 1
    page.set_viewport_size({"width": 390, "height": 844})
    expect(popover).to_be_hidden()
    # Keep the trigger clear of the edit page's sticky save actions before
    # measuring; Playwright otherwise scrolls it into view during the click.
    trigger.evaluate("button => button.scrollIntoView({block: 'center'})")
    trigger.focus()
    expect(popover).to_be_hidden()
    before_save = save.bounding_box()
    trigger.click()
    expect(popover).to_be_visible()
    expect(trigger).to_be_focused()
    assert save.bounding_box() == before_save
    bounds = popover.bounding_box()
    assert bounds["x"] >= 0 and bounds["y"] >= 0
    assert bounds["x"] + bounds["width"] <= 390
    assert bounds["y"] + bounds["height"] <= 844
    first = row(page, "link_plantings", browser_tags.first.pk)
    second = row(page, "link_plantings", browser_tags.second.pk)
    expect(first).to_contain_text("Glasshouse / West bed · 2026 · Batch A")
    expect(second).to_contain_text("Batch B")
    # Click result text and padding, exercising the complete native label.
    first.locator(".tag-title").click()
    expect(first.locator("input")).to_be_checked()
    expect(popover).to_be_visible()
    assert popover.bounding_box() == bounds
    second.click(position={"x": 4, "y": 8})
    expect(second.locator("input")).to_be_checked()
    assert popover.bounding_box() == bounds
    first.locator(".tag-meta").click()
    expect(first.locator("input")).not_to_be_checked()
    assert popover.bounding_box() == bounds
    first.click()
    expect(page.locator("[data-tag-chips] li")).to_have_count(2)
    expect(page.locator("[data-tag-context]")).to_contain_text("Golden Cherry")
    page.keyboard.press("Escape")
    expect(popover).to_be_hidden()
    expect(trigger).to_be_focused()
    # Keyboard activation opens search, and Enter moves to a checkbox without saving.
    trigger.press("Enter")
    search = page.locator("[data-tag-search]")
    expect(search).to_be_focused()
    search.fill("West 2026")
    expect(first).to_be_visible()
    expect(second).to_be_visible()
    search.press("Enter")
    expect(first.locator("input")).to_be_focused()
    first.locator("input").press("Space")
    expect(first.locator("input")).not_to_be_checked()
    first.locator("input").press("Space")
    page.get_by_role("button", name="Klart" if language == "sv" else "Done", exact=True).click()
    expect(trigger).to_be_focused()
    page.locator("[data-tag-picker]").screenshot(path=str(tmp_path / f"tags-{mode}-{language}.png"))
    save.click()
    if mode == "quick":
        expect(page.locator("#quick-receipt")).to_contain_text(
            "Observationen har sparats" if language == "sv" else "Observation saved"
        )
        expect(page.locator("[data-tag-chips] li")).to_have_count(0)
        page.locator("#quick-receipt a").click()
    expect(page).to_have_url(re.compile(r"/entries/\d+/$"))
    expect(page.locator(".link-tags")).to_contain_text("Batch A")
    expect(page.locator(".link-tags")).to_contain_text("Batch B")
    state = saved_state()
    assert state["body"] == "Compare the batches"
    assert {link[0] for link in state["links"]} == {browser_tags.first.pk, browser_tags.second.pk}
    assert all(link[1:] == (None, None, None) for link in state["links"])
    page.get_by_role("link", name="Redigera" if language == "sv" else "Edit", exact=True).click()
    expect(page.locator("[data-tag-chips] li")).to_have_count(2)
    expect(first.locator("input")).to_be_checked()
    expect(second.locator("input")).to_be_checked()
    page.locator("[data-tag-chips] button").first.click()
    expect(page.locator("[data-tag-chips] li")).to_have_count(1)
    expect(page.locator("[data-tag-trigger]")).to_be_focused()


@pytest.mark.parametrize("mode", ["quick", "edit"])
@pytest.mark.parametrize("year", [2021, 2024])
def test_tag_search_enter_focuses_first_visible_choice_after_year_change(
    page, live_server, user, browser_tags, mode, year
):
    login(page, live_server, user, "en")
    page.locator("#id_date").fill("2026-07-14")
    if mode == "edit":
        page.locator("#id_body").fill("Observation with changing tag suggestions")
        page.locator("#save-entry").click()
        page.locator("#quick-receipt a").click()
        page.get_by_role("link", name="Edit", exact=True).click()
    page.locator("#id_date").fill(f"{year}-07-14")
    trigger = page.locator("[data-tag-trigger]")
    trigger.press("Enter")
    search = page.locator("[data-tag-search]")
    expect(search).to_be_focused()
    expect(row(page, "link_plantings", browser_tags.first.pk)).to_be_hidden()
    expected = (
        row(page, "link_plantings", browser_tags.ongoing.pk)
        if year == 2024
        else row(page, "link_areas", browser_tags.parent.pk)
    ).locator("input")
    search.press("Enter")
    expect(expected).to_be_focused()
    expected.press("Space")
    expect(expected).to_be_checked()
    expect(page.locator("[data-tag-chips] li")).to_have_count(1)
    # Search exposes other years, but the current year's choices still come first.
    search.fill("Tomato")
    search.press("Enter")
    first_planting = browser_tags.ongoing if year == 2024 else browser_tags.unknown
    expect(row(page, "link_plantings", first_planting.pk).locator("input")).to_be_focused()
    search.fill("No matching synthetic tag")
    search.press("Enter")
    expect(search).to_be_focused()
    expect(page.locator("[data-tag-popover]")).to_be_visible()
    page.keyboard.press("Escape")
    expect(trigger).to_be_focused()


def test_year_ranking_search_and_honest_unknowns(page, live_server, user, browser_tags):
    login(page, live_server, user, "en")
    page.goto(live_server.url + "/")
    page.locator("#id_date").fill("2026-07-14")
    page.locator("[data-tag-trigger]").click()
    current = page.locator('[data-tag-group="planting"]')
    expect(current).to_contain_text("Established")
    expect(current).not_to_contain_text("Earlier batch")
    expect(current).not_to_contain_text("Ended")
    expect(current).to_contain_text("2022–Ongoing")
    expect(row(page, "link_plantings", browser_tags.unknown.pk)).to_contain_text("Unknown variety")
    expect(row(page, "link_plantings", browser_tags.unknown.pk)).to_contain_text("Unassigned area")
    expect(row(page, "link_plantings", browser_tags.unknown.pk)).to_contain_text(
        f"Batch {browser_tags.unknown.pk}"
    )
    search = page.locator("[data-tag-search]")
    search.fill("2025")
    expect(row(page, "link_plantings", browser_tags.older.pk)).to_be_visible()
    row(page, "link_plantings", browser_tags.older.pk).click()
    search.fill("Golden Cherry")
    expect(row(page, "link_varieties", browser_tags.variety.pk)).to_be_visible()
    search.fill("Archived crop")
    expect(page.locator("[data-tag-no-results]")).to_be_visible()
    page.get_by_role("button", name="Done", exact=True).click()
    page.locator("#id_date").fill("2024-07-14")
    page.locator("[data-tag-trigger]").click()
    search.fill("")
    expect(current).to_contain_text("Ended")
    expect(current).not_to_contain_text("Batch A")


@pytest.mark.parametrize("failure", [422, 503, "network"])
def test_failed_tagged_capture_preserves_files_tags_key_and_unlocks(
    page, live_server, user, browser_tags, image_upload, failure
):
    login(page, live_server, user, "en")
    page.goto(live_server.url + "/")
    page.locator("#id_date").fill("2026-07-14")
    page.locator("#id_body").fill("Retry tagged draft")
    page.locator("#id_library").set_input_files(
        {"name": "synthetic.jpg", "mimeType": "image/jpeg", "buffer": image_upload().read()}
    )
    key = page.locator("#id_submission_key").input_value()
    page.locator("[data-tag-trigger]").click()
    selected = row(page, "link_plantings", browser_tags.first.pk).locator("input")
    selected.check()
    page.get_by_role("button", name="Done", exact=True).click()
    pending = []
    page.route(
        "**/capture/",
        lambda route: (
            pending.append(route) if route.request.method == "POST" else route.continue_()
        ),
    )
    page.locator("#save-entry").click()
    expect(page.locator("[data-tag-trigger]")).to_be_disabled()
    expect(selected).to_be_disabled()
    expect(page.locator("[data-tag-chips] button")).to_be_disabled()
    assert len(pending) == 1
    if failure == "network":
        pending[0].abort()
    else:
        pending[0].fulfill(
            status=failure,
            content_type="text/html",
            body='<div class="error-summary">Synthetic retry</div>',
        )
    expect(page.locator("#capture-errors")).not_to_be_empty()
    expect(page.locator("[data-tag-trigger]")).to_be_enabled()
    expect(selected).to_be_enabled()
    expect(selected).to_be_checked()
    expect(page.locator("[data-tag-chips] li")).to_have_count(1)
    expect(page.locator("#id_body")).to_have_value("Retry tagged draft")
    expect(page.locator("#id_submission_key")).to_have_value(key)
    assert page.locator("#id_library").evaluate("input => input.files.length") == 1
    page.unroute("**/capture/")
    page.locator("#save-entry").click()
    page.locator("#quick-receipt a").click()
    expect(page).to_have_url(re.compile(r"/entries/\d+/$"))
    state = saved_state()
    assert state["photos"] == 1 and state["links"] == [(browser_tags.first.pk, None, None, None)]


def test_no_javascript_grouped_checkboxes_capture_and_edit(page, live_server, user, browser_tags):
    login(page, live_server, user, "en")
    plain_context = page.context.browser.new_context(
        java_script_enabled=False, viewport={"width": 390, "height": 844}
    )
    try:
        plain_context.add_cookies(page.context.cookies())
        plain = plain_context.new_page()
        plain.goto(live_server.url + "/capture/")
        expect(plain.locator("[data-tag-trigger]")).to_be_hidden()
        expect(plain.locator('[data-tag-group="planting"]')).to_be_visible()
        expect(plain.locator('[data-tag-group="area"]')).to_be_visible()
        plain.locator("#id_body").fill("Native tagged note")
        row(plain, "link_plantings", browser_tags.first.pk).locator("input").check()
        row(plain, "link_areas", browser_tags.parent.pk).locator("input").check()
        plain.locator("#save-entry").click()
        expect(plain).to_have_url(re.compile(r"/#entry-\d+$"))
        plain.get_by_role("link", name="Edit", exact=True).click()
        expect(row(plain, "link_plantings", browser_tags.first.pk).locator("input")).to_be_checked()
        expect(row(plain, "link_areas", browser_tags.parent.pk).locator("input")).to_be_checked()
        row(plain, "link_plantings", browser_tags.first.pk).locator("input").uncheck()
        plain.locator('#entry-form button[type="submit"]').click()
        expect(plain).to_have_url(re.compile(r"/entries/\d+/$"))
        assert saved_state()["links"] == [(None, browser_tags.parent.pk, None, None)]
    finally:
        plain_context.close()


def test_target_archived_while_drafting_shows_field_error_and_can_be_removed(
    page, live_server, user, browser_tags, image_upload
):
    login(page, live_server, user, "en")
    page.goto(live_server.url + "/")
    page.locator("#id_body").fill("Keep the draft after a tag error")
    page.locator("#id_library").set_input_files(
        {"name": "synthetic.jpg", "mimeType": "image/jpeg", "buffer": image_upload().read()}
    )
    page.locator("[data-tag-trigger]").click()
    page.locator("[data-tag-search]").fill("Tomato")
    crop = row(page, "link_crops", browser_tags.crop.pk).locator("input")
    crop.check()
    page.get_by_role("button", name="Done", exact=True).click()

    def archive():
        close_old_connections()
        try:
            archive_crop(actor=type(user).objects.get(pk=user.pk), pk=browser_tags.crop.pk)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(archive).result(timeout=10)
    with page.expect_response(
        lambda response: response.url.endswith("/capture/") and response.request.method == "POST"
    ) as rejected:
        page.locator("#save-entry").click()
    assert rejected.value.status == 422
    expect(page.locator("#id_link_crops_errors")).to_contain_text("Choose an active record")
    expect(crop).to_be_checked()
    assert page.locator("#id_library").evaluate("input => input.files.length") == 1
    page.locator('.error-summary a[href="#id_link_crops"]').click()
    expect(page.locator("[data-tag-popover]")).to_be_visible()
    expect(crop).to_be_focused()
    crop.uncheck()
    page.get_by_role("button", name="Done", exact=True).click()
    page.locator("#save-entry").click()
    page.locator("#quick-receipt a").click()
    expect(page).to_have_url(re.compile(r"/entries/\d+/$"))
    state = saved_state()
    assert state["body"] == "Keep the draft after a tag error"
    assert state["photos"] == 1 and state["links"] == []


@pytest.mark.parametrize("width,height", [(320, 568), (390, 400), (1280, 900)])
def test_picker_fits_viewport_opens_above_and_keeps_choices_on_resize(
    page, live_server, user, browser_tags, width, height, tmp_path
):
    login(page, live_server, user, "en")
    page.goto(live_server.url + "/")
    page.set_viewport_size({"width": width, "height": height})
    page.locator("#id_date").fill("2026-07-14")
    trigger = page.locator("[data-tag-trigger]")
    trigger.scroll_into_view_if_needed()
    trigger.click()
    popover = page.locator("[data-tag-popover]")
    expect(popover).to_be_visible()
    bounds = popover.bounding_box()
    assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
    assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= height
    assert page.locator(".tag-options").bounding_box()["height"] >= 100
    anchor = trigger.bounding_box()
    if anchor["y"] > height / 2:
        assert bounds["y"] < anchor["y"]
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    popover.screenshot(path=str(tmp_path / f"tag-popover-{width}.png"))
    row(page, "link_plantings", browser_tags.first.pk).locator("input").check()
    page.set_viewport_size({"width": width, "height": height + 80})
    expect(row(page, "link_plantings", browser_tags.first.pk).locator("input")).to_be_checked()
    expect(popover).to_be_visible()
    page.get_by_role("button", name="Done", exact=True).click()
    expect(trigger).to_be_focused()
