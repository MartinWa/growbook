"""Tab-session writing recovery and authoritative save confirmation in Chromium."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import uuid4

import pytest
from browser_helpers import browser_page, login
from django.db import close_old_connections
from playwright.sync_api import expect

from diary.models import DiaryEntry
from diary.services import create_entry, update_entry
from garden.models import Planting
from garden.services import (
    archive_crop,
    archive_variety,
    create_area,
    create_crop,
    create_planting,
    create_variety,
    delete_planting,
    edit_planting,
)

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]

PREFIX = "growbook:draft:v1:"
WRITING = "Ångström 🌱 日本語 <img src=x onerror=alert(1)>\nFirst line\nSecond line"


def database(call):
    def run():
        close_old_connections()
        try:
            return call()
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(run).result()


def drafts(page):
    return page.evaluate(
        "prefix => Object.fromEntries(Object.keys(sessionStorage)"
        ".filter(key => key.startsWith(prefix))"
        ".map(key => [key, JSON.parse(sessionStorage.getItem(key))]))",
        PREFIX,
    )


def token(page):
    return page.locator('[name="submission_key"]').input_value()


def ready(page):
    expect(page.locator("[data-draft-kind]")).to_have_attribute("data-draft-ready", "true")


def discard(page):
    page.locator("[data-draft-discard]").click()
    expect(page.locator("[data-draft-notice]")).to_be_hidden()


@pytest.fixture
def page(browser_endpoint, live_server, user, other_user):
    with browser_page(browser_endpoint, timeout=10000) as page:
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        login(page, live_server, user, "en")
        ready(page)
        yield page
        assert errors == []


def test_immediate_paste_reload_unicode_key_recovery_and_keyboard_discard(page):
    key = token(page)
    warnings = []
    page.on("dialog", lambda dialog: (warnings.append(dialog.type), dialog.accept()))
    # fill emits input without blur; reload follows immediately.
    page.locator("#id_body").fill(WRITING)
    page.reload()
    expect(page.locator("#id_body")).to_have_value(WRITING)
    expect(page.locator("[data-draft-status]")).to_contain_text("was restored")
    assert token(page) == key
    snapshot = next(iter(drafts(page).values()))
    assert snapshot["fields"]["body"] == [WRITING]
    assert not {"csrfmiddlewaretoken", "password", "filter_query"} & snapshot["fields"].keys()
    assert all(isinstance(count, int) for count in snapshot["uploads"].values())
    assert "blob:" not in json.dumps(snapshot)
    assert warnings == ["beforeunload"]
    expect(page.locator("[data-draft-status]")).to_have_attribute("role", "status")
    button = page.get_by_role("button", name="Discard unsaved writing")
    button.focus()
    button.press("Enter")
    expect(page.locator("#id_body")).to_have_value("")
    assert drafts(page) == {}
    page.reload()
    expect(page.locator("[data-draft-notice]")).to_be_hidden()
    assert warnings == ["beforeunload"]


def test_native_warning_only_for_changes_uploads_and_failed_saves(page, image_upload):
    warnings = []
    page.on("dialog", lambda dialog: (warnings.append(dialog.type), dialog.accept()))
    page.locator("#id_body").focus()
    page.reload()
    assert warnings == []
    page.locator("#id_body").fill("Transient writing")
    page.locator("#id_body").fill("")
    page.reload()
    assert warnings == []
    expect(page.locator("#id_body")).to_have_value("")
    page.locator("#id_library").set_input_files(
        {"name": "synthetic.jpg", "mimeType": "image/jpeg", "buffer": image_upload().read()}
    )
    # Removing an upload through the existing preview helper updates dirty state.
    page.locator("[data-photo-previews] button").click()
    page.reload()
    assert warnings == []
    page.locator("#id_body").fill("Reset me")
    page.evaluate("document.getElementById('capture-form').reset()")
    expect(page.locator("#id_body")).to_have_value("")
    page.reload()
    assert warnings == []


def test_same_tab_navigation_filters_language_season_and_independent_tabs(page, live_server):
    page.locator("#id_body").fill(WRITING)
    key = token(page)
    page.locator("[data-filter-disclosure] > summary").click()
    page.locator("#id_q").fill("synthetic")
    expect(page).to_have_url(live_server.url + "/?q=synthetic")
    page.locator("#account-menu > summary").click()
    page.locator("#language").select_option("sv")
    expect(page.locator("html")).to_have_attribute("lang", "sv")
    page.locator("#account-menu > summary").click()
    page.locator("#season").select_option("summer")
    expect(page.locator("html")).to_have_attribute("data-season", "summer")
    expect(page.locator("#id_body")).to_have_value(WRITING)
    assert len(drafts(page)) == 1 and token(page) == key
    page.goto(live_server.url + "/garden/crops/new/")
    page.locator("#id_name").fill("Synthetic crop")
    page.locator("#id_notes").fill("Separate crop writing")
    page.goto(live_server.url + "/?year=2026")
    expect(page.locator("#id_body")).to_have_value(WRITING)
    assert token(page) == key
    expect(page.locator("[data-draft-status]")).to_contain_text("återställdes")
    second = page.context.new_page()
    try:
        second.goto(live_server.url + "/")
        ready(second)
        expect(second.locator("#id_body")).to_have_value("")
        assert drafts(second) == {}
    finally:
        second.close()
    page.goto(live_server.url + "/garden/crops/new/?category=1")
    expect(page.locator("#id_notes")).to_have_value("Separate crop writing")


@pytest.mark.parametrize("failure", [401, 403, 409, 422, 503, 200, "network"])
def test_unconfirmed_responses_keep_draft_and_original_key(page, failure):
    key = token(page)
    page.locator("#id_body").fill(WRITING)

    def fail(route):
        if failure == "network":
            route.abort()
        else:
            route.fulfill(status=failure, content_type="text/html", body="Unconfirmed save")

    page.route("**/capture/", fail)
    page.locator("#save-entry").click()
    expect(page.locator("#save-entry")).to_be_enabled()
    assert next(iter(drafts(page).values()))["tokens"]["submission_key"] == [key]
    page.reload()
    expect(page.locator("#id_body")).to_have_value(WRITING)
    assert token(page) == key


def test_committed_response_lost_reload_identical_text_retry_has_no_duplicate(page):
    page.locator("#id_title").fill("Synthetic recovered observation")
    page.locator("#id_body").fill(WRITING)
    key = token(page)

    def lose_response(route):
        response = route.fetch()
        assert response.status == 200
        route.abort()

    page.route("**/capture/", lose_response)
    page.locator("#save-entry").click()
    expect(page.locator("#capture-errors")).to_contain_text("connection failed")
    assert database(lambda: DiaryEntry.objects.count()) == 1
    page.reload()
    expect(page.locator("#id_body")).to_have_value(WRITING)
    assert token(page) == key
    page.unroute("**/capture/", lose_response)
    page.locator("#save-entry").click()
    expect(page.locator("#quick-receipt")).to_contain_text("Observation already saved")
    expect(page.locator("#id_body")).to_have_value("")
    assert drafts(page) == {}
    assert token(page) != key
    assert database(lambda: DiaryEntry.objects.count()) == 1
    # Start and confirm another independent draft after the receipt reset.
    page.locator("#id_body").fill("Next observation")
    assert len(drafts(page)) == 1
    page.locator("#save-entry").click()
    expect(page.locator("#diary-results .diary-card")).to_have_count(2)
    assert drafts(page) == {}


def test_selected_photos_restore_incomplete_then_reselect_or_explicitly_omit(page, image_upload):
    upload = {"name": "synthetic.jpg", "mimeType": "image/jpeg", "buffer": image_upload().read()}
    page.locator("#id_body").fill(WRITING)
    page.locator("#id_library").set_input_files(upload)
    page.reload()
    expect(page.locator("[data-draft-photos]")).to_be_visible()
    assert next(iter(drafts(page).values()))["uploads"]["library"] == 1
    page.locator("#save-entry").click()
    expect(page.locator("[data-draft-omit]")).to_be_focused()
    assert database(lambda: DiaryEntry.objects.count()) == 0
    page.locator("#id_library").set_input_files(upload)
    expect(page.locator("[data-draft-photos]")).to_be_hidden()
    page.locator("#save-entry").click()
    expect(page.locator("#quick-receipt")).to_contain_text("Observation saved")
    assert database(lambda: DiaryEntry.objects.get().photos.count()) == 1
    page.locator("#id_body").fill("Omit this selection explicitly")
    page.locator("#id_library").set_input_files(upload)
    page.reload()
    page.locator("[data-draft-omit]").click()
    expect(page.locator("[data-draft-photos]")).to_be_hidden()
    assert database(lambda: DiaryEntry.objects.count()) == 1
    page.locator("#save-entry").click()
    expect(page.locator("#diary-results .diary-card")).to_have_count(2)
    assert drafts(page) == {}


def test_entry_redirect_removals_tags_and_stale_edit_copy(page, live_server, user, image_upload):
    entry, crop = database(
        lambda: (
            create_entry(
                author=user,
                date=date(2026, 1, 1),
                body="Saved note",
                uploads=[image_upload()],
                submission_key=uuid4(),
            ).entry,
            create_crop(actor=user, name="Synthetic tag").record,
        )
    )
    edit = live_server.url + entry.get_absolute_url() + "edit/"
    page.goto(edit)
    page.locator("#id_body").fill(WRITING)
    page.locator('[name="remove_photos"]').check()
    page.locator("[data-tag-trigger]").click()
    page.locator("[data-tag-type]").select_option("crop")
    page.locator(f'[name="link_crops"][value="{crop.pk}"]').check()
    page.locator("[data-tag-done]").click()
    key = token(page)
    page.reload()
    expect(page.locator('[name="remove_photos"]')).to_be_checked()
    expect(page.locator("[data-tag-chips]")).to_contain_text("Synthetic tag")
    assert token(page) == key
    warnings = []
    page.on("dialog", lambda dialog: (warnings.append(dialog.type), dialog.accept()))
    page.locator('#entry-form button[type="submit"]').click()
    expect(page).to_have_url(live_server.url + entry.get_absolute_url())
    assert drafts(page) == {} and warnings == []
    assert database(lambda: DiaryEntry.objects.get(pk=entry.pk).photos.count()) == 0
    page.goto(edit)
    original_version = page.locator('[name="expected_updated_at"]').input_value()
    page.locator("#id_body").fill("Earlier unsaved writing")
    original_draft = next(iter(drafts(page).values()))

    def change_saved():
        current = DiaryEntry.objects.get(pk=entry.pk)
        update_entry(
            actor=user,
            pk=current.pk,
            date=current.date,
            body="New saved writing",
            title=current.title,
            expected_updated_at=current.updated_at,
        )

    database(change_saved)
    page.reload()
    expect(page.locator("#id_body")).to_have_value("New saved writing")
    expect(page.locator("[data-draft-copy]")).to_be_visible()
    expect(page.locator("#draft-copy-body")).to_have_value("Earlier unsaved writing")
    assert page.locator('[name="expected_updated_at"]').input_value() != original_version
    assert next(iter(drafts(page).values())) == original_draft
    page.locator("#id_body").fill("Current form correction")
    assert next(iter(drafts(page).values()))["previous"] == [original_draft]
    page.reload()
    expect(page.locator("#id_body")).to_have_value("Current form correction")
    expect(page.locator("#draft-copy-body")).to_have_value("Earlier unsaved writing")
    discard(page)
    expect(page.locator("#id_body")).to_have_value("New saved writing")


def test_unavailable_tag_restores_visibly_and_can_be_removed(page, live_server, user):
    crop = database(lambda: create_crop(actor=user, name="Synthetic unavailable tag").record)
    page.reload()
    page.locator("#id_body").fill(WRITING)
    page.locator("[data-tag-trigger]").click()
    page.locator("[data-tag-type]").select_option("crop")
    page.locator(f'[name="link_crops"][value="{crop.pk}"]').check()
    page.locator("[data-tag-done]").click()
    database(lambda: archive_crop(actor=user, pk=crop.pk))
    page.reload()
    expect(page.locator("[data-tag-chips]")).to_contain_text(f"Unavailable selection ({crop.pk})")
    assert next(iter(drafts(page).values()))["fields"]["link_crops"] == [str(crop.pk)]
    page.locator("[data-tag-chips] button").click()
    expect(page.locator("[data-tag-chips]")).to_be_hidden()


def test_dependent_planting_choices_zero_quantities_and_custom_label_restore(
    page, live_server, user
):
    crop, variety = database(lambda: planting_choices(user))
    page.goto(live_server.url + "/garden/plantings/new/")
    page.locator("#id_crop").select_option(str(crop.pk))
    expect(page.locator(f'#id_variety option[value="{variety.pk}"]')).to_have_count(1)
    page.locator("#planting-more-details > summary").click()
    page.locator("#id_variety").select_option(str(variety.pk))
    page.locator("#id_quantity").fill("0")
    page.locator("#id_label").fill("Synthetic custom label")
    page.locator("#planting-notes-photos > summary").click()
    page.locator("#id_notes").fill(WRITING)
    key = token(page)
    assert next(iter(drafts(page).values()))["fields"].get("notes") == [WRITING]
    page.reload()
    expect(page.locator("#id_notes")).to_have_value(WRITING)
    expect(page.locator("#id_variety")).to_have_value(str(variety.pk))
    expect(page.locator("#id_quantity")).to_have_value("0")
    expect(page.locator("#id_label")).to_have_value("Synthetic custom label")
    assert token(page) == key
    page.locator('#register-form button[type="submit"]:not([name])').click()
    expect(page.locator("[data-draft-kind]")).to_have_count(0)
    assert drafts(page) == {}
    assert database(lambda: Planting.objects.get().quantity) == 0


def planting_choices(user):
    crop = create_crop(actor=user, name="Synthetic crop").record
    variety = create_variety(actor=user, crop=crop, name="Synthetic variety").record
    return crop, variety


@pytest.mark.parametrize("capture", [False, True])
@pytest.mark.parametrize("changed", [False, True])
def test_cycle_evaluations_member_versions_and_capture_action_restore(
    page, live_server, user, capture, changed
):
    def seed():
        crop, variety = planting_choices(user)
        return create_planting(
            actor=user,
            crop=crop,
            variety=variety,
            start_year=2026,
            status="ongoing",
            started_on=date(2026, 1, 1),
        ).record

    planting = database(seed)
    if capture:
        page.reload()
        page.locator("#id_body").fill("Synthetic completion")
        page.locator("[data-planting-action] > summary").click()
        page.locator('[name="planting_action"]').select_option("cycle")
        page.locator('[name="action_planting"]').select_option(str(planting.pk))
        expect(page.locator(f'[name="cycle-evaluation_{planting.pk}"]')).to_have_count(1)
        page.locator('[data-cycle-preset="complete"]').click()
        evaluation = f"cycle-evaluation_{planting.pk}"
    else:
        page.goto(live_server.url + f"/garden/plantings/cycles/{planting.cycle_id}/complete/")
        evaluation = f"evaluation_{planting.pk}"
    page.locator(f'[name="{evaluation}"]').fill(WRITING)
    old = next(iter(drafts(page).values()))
    if changed:
        database(
            lambda: edit_planting(
                actor=user,
                pk=planting.pk,
                crop=planting.crop_id,
                variety=planting.variety_id,
                start_year=2026,
                notes="Changed member",
                expected_updated_at=planting.updated_at,
            )
        )
    page.reload()
    if changed:
        expect(page.locator("[data-draft-copy]")).to_be_visible()
        expect(page.locator(f"#draft-copy-{evaluation}")).to_have_value(WRITING)
        assert next(iter(drafts(page).values())) == old
    else:
        expect(page.locator(f'[name="{evaluation}"]')).to_have_value(WRITING)
        assert token(page) == old["tokens"]["submission_key"][0]
        if capture:
            expect(page.locator('[name="planting_action"]')).to_have_value("cycle")
            expect(page.locator('[name="cycle-status"]')).to_have_value("completed")
            page.locator("#save-entry").click()
            expect(page.locator("#quick-receipt")).to_contain_text("Observation saved")
        else:
            page.locator('#register-form button[type="submit"]').click()
            expect(page.locator("[data-draft-kind]")).to_have_count(0)
        assert drafts(page) == {}
        assert (
            database(lambda: Planting.objects.get(pk=planting.pk).evaluation).replace("\r\n", "\n")
            == WRITING
        )


@pytest.mark.parametrize("blocked", ["get", "quota", "size"])
def test_storage_failures_keep_editing_and_submission_usable(page, blocked):
    if blocked == "get":
        page.add_init_script(
            "Object.defineProperty(window, 'sessionStorage', "
            "{get() {throw new DOMException('Blocked', 'SecurityError')}})"
        )
        page.reload()
    elif blocked == "quota":
        page.evaluate(
            "() => {Storage.prototype.setItem = () => "
            "{throw new DOMException('Full', 'QuotaExceededError')};}"
        )
    writing = "x" * (1024 * 1024 + 1) if blocked == "size" else WRITING
    page.locator("#id_body").fill(writing)
    expect(page.locator("[data-draft-storage]")).to_be_visible()
    expect(page.locator("#id_body")).to_have_value(writing)
    if blocked == "size":
        page.locator("#id_body").fill(WRITING)
    page.locator("#save-entry").click()
    expect(page.locator("#quick-receipt")).to_contain_text("Observation saved")


@pytest.mark.parametrize("corrupt", ["json", "version", "credentials", "scalar", "count"])
def test_untrusted_malformed_snapshots_are_rejected_whole(page, corrupt):
    page.locator("#id_body").fill(WRITING)
    key, snapshot = next(iter(drafts(page).items()))
    if corrupt == "version":
        snapshot["version"] = 99
    elif corrupt == "credentials":
        snapshot["fields"]["csrfmiddlewaretoken"] = ["Never restore credentials"]
    elif corrupt == "scalar":
        snapshot["fields"]["body"] = [WRITING, "Do not silently drop this"]
    elif corrupt == "count":
        snapshot["uploads"]["library"] = -1
    # Avoid pagehide's legitimate final flush replacing the intentionally corrupt fixture.
    page.locator("[data-draft-discard]").click()
    raw = "{" if corrupt == "json" else json.dumps(snapshot)
    page.add_init_script(f"sessionStorage.setItem({json.dumps(key)}, {json.dumps(raw)})")
    page.reload()
    expect(page.locator("#id_body")).to_have_value("")
    expect(page.locator("[data-draft-status]")).to_contain_text("could not be restored")
    assert drafts(page) == {}


def test_account_switch_and_logout_do_not_expose_or_retain_other_drafts(
    page, live_server, other_user
):
    page.locator("#id_body").fill("First account's private draft")
    other = page.context.new_page()
    try:
        login(other, live_server, other_user, "en")
        page.reload()
        expect(page.locator("#id_body")).to_have_value("")
        expect(page.locator(".account-name")).to_have_text(other_user.username)
        page.locator("#id_body").fill("Second account's private draft")
        other.locator("#id_body").fill("Other tab writing")
        page.locator("#account-menu > summary").click()
        page.locator("[data-draft-logout] button").click()
        expect(page).to_have_url(live_server.url + "/accounts/login/")
        assert drafts(page) == {}
        other.wait_for_function(
            "prefix => !Object.keys(sessionStorage).some(key => key.startsWith(prefix))", arg=PREFIX
        )
        assert drafts(other) == {}
    finally:
        other.close()


def test_no_javascript_forms_keep_native_save_fallback(browser_endpoint, live_server, user):
    with browser_page(browser_endpoint, java_script_enabled=False) as page:
        login(page, live_server, user, "en")
        expect(page.locator("[data-draft-notice]")).to_be_hidden()
        page.locator("#id_body").fill(WRITING)
        page.locator("#save-entry").click()
        expect(page.locator(".diary-card")).to_contain_text("Ångström")
        assert database(lambda: DiaryEntry.objects.get().body).replace("\r\n", "\n") == WRITING


@pytest.mark.parametrize(
    "kind,field",
    [
        ("crop", "notes"),
        ("variety", "experiences"),
        ("area", "notes"),
        ("event", "note"),
        ("cycle", "evaluation"),
    ],
)
def test_existing_note_forms_restore_per_record_and_discard_saved_defaults(
    page, live_server, user, kind, field
):
    def seed():
        crop, variety = planting_choices(user)
        if kind == "crop":
            return f"/garden/crops/{crop.pk}/edit/", create_crop(
                actor=user, name="Other crop"
            ).record.get_absolute_url() + "edit/"
        if kind == "variety":
            second = create_variety(actor=user, crop=crop, name="Other variety").record
            return f"/garden/varieties/{variety.pk}/edit/", f"/garden/varieties/{second.pk}/edit/"
        if kind == "area":
            first = create_area(actor=user, name="Synthetic area", notes="Saved area note").record
            second = create_area(actor=user, name="Other area").record
            return f"/garden/areas/{first.pk}/edit/", f"/garden/areas/{second.pk}/edit/"
        planting = create_planting(actor=user, crop=crop, variety=variety, start_year=2026).record
        if kind == "event":
            return f"/garden/plantings/{planting.pk}/events/new/", "/garden/areas/new/"
        return (
            f"/garden/plantings/cycles/{planting.cycle_id}/edit/",
            "/garden/areas/new/",
            planting.pk,
        )

    paths = database(seed)
    path, other_path = paths[:2]
    if kind == "cycle":
        field = f"evaluation_{paths[2]}"
    page.goto(live_server.url + path)
    if kind == "cycle":
        page.locator("#id_status").select_option("completed")
    saved = page.locator(f'[name="{field}"]').input_value()
    page.locator(f'[name="{field}"]').fill(WRITING)
    key = token(page)
    page.goto(live_server.url + other_path)
    ready(page)
    expect(page.locator("[data-draft-notice]")).to_be_hidden()
    page.goto(live_server.url + path)
    expect(page.locator(f'[name="{field}"]')).to_have_value(WRITING)
    assert token(page) == key
    discard(page)
    expect(page.locator(f'[name="{field}"]')).to_have_value(saved)
    assert drafts(page) == {}


def test_failed_native_post_retains_fields_and_discards_to_saved_values(page, live_server, user):
    crop = database(
        lambda: create_crop(actor=user, name="Saved crop", notes="Saved writing").record
    )
    page.goto(live_server.url + f"/garden/crops/{crop.pk}/edit/")
    page.locator("#id_name").fill("")
    page.locator("#id_notes").fill(WRITING)
    key = token(page)
    # Native submit bypasses HTMX and browser constraints, exercising a bound error page.
    with page.expect_navigation():
        page.evaluate("document.getElementById('register-form').submit()")
    expect(page.locator("#register-form")).to_have_attribute("data-draft-bound", "true")
    expect(page.locator("#id_notes")).to_have_value(WRITING)
    assert token(page) == key
    expect(page.locator("[data-draft-status]")).to_contain_text("submission was kept")
    discard(page)
    expect(page.locator("#id_name")).to_have_value("Saved crop")
    expect(page.locator("#id_notes")).to_have_value("Saved writing")
    assert token(page) != key
    page.goto(page.url)
    expect(page.locator("[data-draft-notice]")).to_be_hidden()


def test_missing_variety_is_visible_and_original_key_is_kept(page, live_server, user):
    crop, variety = database(lambda: planting_choices(user))
    page.goto(live_server.url + "/garden/plantings/new/")
    page.locator("#id_crop").select_option(str(crop.pk))
    expect(page.locator(f'#id_variety option[value="{variety.pk}"]')).to_have_count(1)
    page.locator("#planting-more-details > summary").click()
    page.locator("#id_variety").select_option(str(variety.pk))
    page.locator("#planting-notes-photos > summary").click()
    page.locator("#id_notes").fill(WRITING)
    key = token(page)
    database(lambda: archive_variety(actor=user, pk=variety.pk))
    page.reload()
    expect(page.locator("#id_variety")).to_have_value(str(variety.pk))
    expect(page.locator("#id_variety option:checked")).to_contain_text("Unavailable selection")
    assert token(page) == key
    page.locator('#register-form button[type="submit"]:not([name])').click()
    expect(page.locator("#register-errors")).not_to_be_empty()
    assert token(page) == key and len(drafts(page)) == 1


@pytest.mark.parametrize("action", ["new", "event"])
def test_optional_capture_fields_and_upload_counts_restore(
    page, live_server, user, image_upload, action
):
    def seed():
        crop, variety = planting_choices(user)
        return (
            crop,
            variety,
            create_planting(actor=user, crop=crop, variety=variety, start_year=2026).record,
        )

    crop, variety, planting = database(seed)
    page.reload()
    page.locator("[data-planting-action] > summary").click()
    page.locator('[name="planting_action"]').select_option(action)
    if action == "new":
        page.locator('[name="planting-crop"]').select_option(str(crop.pk))
        page.locator('[name="planting-variety"]').select_option(str(variety.pk))
        page.locator('[name="planting-quantity"]').fill("0")
        page.locator('[name="planting-notes"]').fill(WRITING)
        page.locator('[name="planting-photos"]:not([capture])').set_input_files(
            {"name": "synthetic.jpg", "mimeType": "image/jpeg", "buffer": image_upload().read()}
        )
        writing_field = "planting-notes"
    else:
        page.locator('[name="action_planting"]').select_option(str(planting.pk))
        page.locator('[name="event-kind"]').select_option("sowing")
        page.locator('[name="event-actual_date"]').fill("2026-02-03")
        page.locator('[name="event-note"]').fill(WRITING)
        writing_field = "event-note"
    page.locator("#id_body").fill("Synthetic linked writing")
    key = token(page)
    page.reload()
    expect(page.locator(f'[name="{writing_field}"]')).to_have_value(WRITING)
    expect(page.locator('[name="planting_action"]')).to_have_value(action)
    assert token(page) == key
    if action == "new":
        expect(page.locator('[name="planting-variety"]')).to_have_value(str(variety.pk))
        expect(page.locator('[name="planting-quantity"]')).to_have_value("0")
        expect(page.locator("[data-draft-photos]")).to_be_visible()
    else:
        expect(page.locator('[name="action_planting"]')).to_have_value(str(planting.pk))
        expect(page.locator('[name="event-actual_date"]')).to_have_value("2026-02-03")


def test_expired_session_recovery_requires_same_account_login(page, live_server, user):
    page.locator("#id_body").fill(WRITING)
    key = token(page)
    page.context.clear_cookies()
    page.reload()
    expect(page.locator("#id_username")).to_be_visible()
    assert page.locator("#id_body").count() == 0
    login(page, live_server, user, "en")
    expect(page.locator("#id_body")).to_have_value(WRITING)
    assert token(page) == key


def test_unavailable_capture_action_keeps_selection_and_writing(page, live_server, user):
    def seed():
        crop, variety = planting_choices(user)
        return create_planting(actor=user, crop=crop, variety=variety, start_year=2026).record

    planting = database(seed)
    page.reload()
    page.locator("[data-planting-action] > summary").click()
    page.locator('[name="planting_action"]').select_option("event")
    page.locator('[name="action_planting"]').select_option(str(planting.pk))
    page.locator('[name="event-note"]').fill(WRITING)
    page.locator("#id_body").fill("Synthetic event observation")
    key = token(page)
    database(
        lambda: delete_planting(actor=user, pk=planting.pk, expected_updated_at=planting.updated_at)
    )
    page.reload()
    expect(page.locator('[name="event-note"]')).to_have_value(WRITING)
    expect(page.locator('[name="action_planting"]')).to_have_value(str(planting.pk))
    expect(page.locator('[name="action_planting"] option:checked')).to_contain_text(
        "Unavailable selection"
    )
    assert token(page) == key
    page.locator("#save-entry").click()
    expect(page.locator("#capture-errors")).not_to_be_empty()
    assert token(page) == key


def test_changed_cycle_membership_offers_old_evaluation_without_restoring_versions(
    page, live_server, user
):
    def seed():
        crop, variety = planting_choices(user)
        second = create_variety(actor=user, crop=crop, name="Other member").record
        return (
            crop,
            second,
            create_planting(actor=user, crop=crop, variety=variety, start_year=2026).record,
        )

    crop, second, planting = database(seed)
    page.goto(live_server.url + f"/garden/plantings/cycles/{planting.cycle_id}/edit/")
    page.locator("#id_status").select_option("completed")
    page.locator(f'[name="evaluation_{planting.pk}"]').fill(WRITING)
    old = next(iter(drafts(page).values()))
    database(
        lambda: create_planting(
            actor=user, crop=crop, variety=second, start_year=2026, batch_planting=planting
        )
    )
    page.reload()
    expect(page.locator("[data-draft-copy]")).to_be_visible()
    expect(page.locator(f"#draft-copy-evaluation_{planting.pk}")).to_have_value(WRITING)
    assert (
        page.locator('[name="expected_updated_at"]').input_value()
        != old["tokens"]["expected_updated_at"][0]
    )
    assert next(iter(drafts(page).values())) == old


def test_batch_member_drafts_stay_with_their_target_batch(page, live_server, user):
    def seed():
        crop, first = planting_choices(user)
        second = create_variety(actor=user, crop=crop, name="Second variety").record
        third = create_variety(actor=user, crop=crop, name="Third variety").record
        batch_a = create_planting(actor=user, crop=crop, variety=first, start_year=2026).record
        batch_b = create_planting(actor=user, crop=crop, variety=second, start_year=2026).record
        return batch_a, batch_b, third

    batch_a, batch_b, third = database(seed)
    path_a = live_server.url + f"/garden/plantings/new/?batch_planting={batch_a.pk}"
    path_b = live_server.url + f"/garden/plantings/new/?batch_planting={batch_b.pk}"
    page.goto(path_a)
    page.locator("#id_variety").select_option(str(third.pk))
    page.locator("#planting-notes-photos > summary").click()
    page.locator("#id_notes").fill(WRITING)
    key_a = token(page)
    page.goto(path_b)
    expect(page.locator("#id_batch_planting")).to_have_value(str(batch_b.pk))
    expect(page.locator("#id_notes")).to_have_value("")
    expect(page.locator("[data-draft-notice]")).to_be_hidden()
    page.locator("#id_variety").select_option(str(third.pk))
    page.locator("#planting-notes-photos > summary").click()
    page.locator("#id_notes").fill("Other batch writing")
    assert len(drafts(page)) == 2
    page.locator('#register-form button[type="submit"]:not([name])').click()
    expect(page.locator("[data-draft-kind]")).to_have_count(0)
    assert len(drafts(page)) == 1
    assert database(lambda: Planting.objects.filter(cycle_id=batch_b.cycle_id).count()) == 2
    page.goto(path_a)
    expect(page.locator("#id_notes")).to_have_value(WRITING)
    expect(page.locator("#id_batch_planting")).to_have_value(str(batch_a.pk))
    assert token(page) == key_a
    page.locator('#register-form button[type="submit"]:not([name])').click()
    expect(page.locator("[data-draft-kind]")).to_have_count(0)
    assert drafts(page) == {}
    assert database(lambda: Planting.objects.filter(cycle_id=batch_a.cycle_id).count()) == 2


def test_partial_photo_reselection_across_sources_keeps_the_original_count(page, image_upload):
    first = {"name": "first.jpg", "mimeType": "image/jpeg", "buffer": image_upload().read()}
    second = {"name": "second.jpg", "mimeType": "image/jpeg", "buffer": image_upload().read()}
    page.locator("#id_body").fill(WRITING)
    page.locator("#id_library").set_input_files([first, second])
    page.reload()
    page.locator("#id_camera").set_input_files(first)
    snapshot = next(iter(drafts(page).values()))
    assert snapshot["uploads"]["camera"] + snapshot["uploads"]["library"] == 2
    page.reload()
    page.locator("#save-entry").click()
    expect(page.locator("[data-draft-omit]")).to_be_focused()
    assert database(lambda: DiaryEntry.objects.count()) == 0
    page.locator("#id_library").set_input_files([first, second])
    expect(page.locator("[data-draft-photos]")).to_be_hidden()
    page.locator("#save-entry").click()
    expect(page.locator("#quick-receipt")).to_contain_text("Observation saved")
    assert database(lambda: DiaryEntry.objects.get().photos.count()) == 2
    assert drafts(page) == {}


@pytest.mark.parametrize("action", ["new", "event", "cycle"])
def test_failed_native_capture_discard_resets_action_writing(page, user, action):
    def seed():
        crop, variety = planting_choices(user)
        return create_planting(
            actor=user,
            crop=crop,
            variety=variety,
            start_year=2026,
            status="ongoing",
            started_on=date(2026, 1, 1),
            evaluation="Saved evaluation",
        ).record

    planting = database(seed)
    page.reload()
    page.locator("#id_body").fill(WRITING)
    page.locator("[data-planting-action] > summary").click()
    page.locator('[name="planting_action"]').select_option(action)
    if action == "new":
        field, saved = "planting-notes", ""
    else:
        page.locator('[name="action_planting"]').select_option(str(planting.pk))
        if action == "cycle":
            field, saved = f"cycle-evaluation_{planting.pk}", "Saved evaluation"
            expect(page.locator(f'[name="{field}"]')).to_have_count(1)
            page.locator('[data-cycle-preset="complete"]').click()
        else:
            field, saved = "event-note", ""
    page.locator(f'[name="{field}"]').fill(WRITING)
    page.locator("#id_date").fill("")
    key = token(page)
    with page.expect_navigation():
        page.evaluate("document.getElementById('capture-form').submit()")
    expect(page.locator("#capture-form")).to_have_attribute("data-draft-bound", "true")
    expect(page.locator(f'[name="{field}"]')).to_have_value(WRITING)
    discard(page)
    expect(page.locator(f'[name="{field}"]')).to_have_value(saved)
    expect(page.locator("#id_body")).to_have_value("")
    expect(page.locator('[name="planting_action"]')).to_have_value("")
    assert token(page) != key and drafts(page) == {}
    assert database(lambda: DiaryEntry.objects.count()) == 0


def test_discard_reloads_varieties_for_the_original_crop(page, live_server, user):
    def seed():
        first, first_variety = planting_choices(user)
        other_first = create_variety(actor=user, crop=first, name="Other original variety").record
        second = create_crop(actor=user, name="Second crop").record
        second_variety = create_variety(actor=user, crop=second, name="Second variety").record
        return first, first_variety, other_first, second, second_variety

    first, first_variety, other_first, second, second_variety = database(seed)
    page.goto(
        live_server.url + f"/garden/plantings/new/?crop={first.pk}&variety={first_variety.pk}"
    )
    page.locator("#id_crop").select_option(str(second.pk))
    expect(page.locator(f'#id_variety option[value="{second_variety.pk}"]')).to_have_count(1)
    page.locator("#id_variety").select_option(str(second_variety.pk))
    page.reload()
    expect(page.locator("#id_variety")).to_have_value(str(second_variety.pk))
    discard(page)
    expect(page.locator("#id_crop")).to_have_value(str(first.pk))
    expect(page.locator(f'#id_variety option[value="{first_variety.pk}"]')).to_contain_text(
        first_variety.name
    )
    expect(page.locator(f'#id_variety option[value="{second_variety.pk}"]')).to_have_count(0)
    page.locator("#id_variety").select_option(str(other_first.pk))
    page.locator('#register-form button[type="submit"]:not([name])').click()
    expect(page.locator("[data-draft-kind]")).to_have_count(0)
    assert database(
        lambda: (Planting.objects.get().crop_id, Planting.objects.get().variety_id)
    ) == (
        first.pk,
        other_first.pk,
    )


@pytest.mark.parametrize("members_changed", [False, True])
@pytest.mark.parametrize("choices_fail", [False, True])
def test_lost_cycle_capture_response_recovers_an_identical_retry(
    page, user, members_changed, choices_fail
):
    def seed():
        crop, variety = planting_choices(user)
        second = create_variety(actor=user, crop=crop, name="Other member").record
        replacement = create_variety(actor=user, crop=crop, name="Replacement member").record
        planting = create_planting(
            actor=user,
            crop=crop,
            variety=variety,
            start_year=2026,
            status="ongoing",
            started_on=date(2026, 1, 1),
        ).record
        other = create_planting(
            actor=user,
            crop=crop,
            variety=second,
            start_year=2026,
            batch_planting=planting,
        ).record
        return planting, other, replacement

    planting, other, replacement = database(seed)
    page.reload()
    page.locator("#id_body").fill(WRITING)
    page.locator("[data-planting-action] > summary").click()
    page.locator('[name="planting_action"]').select_option("cycle")
    page.locator('[name="action_planting"]').select_option(str(planting.pk))
    expect(page.locator(f'[name="cycle-evaluation_{other.pk}"]')).to_have_count(1)
    page.locator('[data-cycle-preset="complete"]').click()
    page.locator(f'[name="cycle-evaluation_{planting.pk}"]').fill(WRITING)
    page.locator(f'[name="cycle-evaluation_{other.pk}"]').fill(
        WRITING + "\nOther original evaluation"
    )
    key = token(page)

    def lose_response(route):
        response = route.fetch()
        assert response.status == 200
        route.abort()

    page.route("**/capture/", lose_response)
    page.locator("#save-entry").click()
    expect(page.locator("#capture-errors")).to_contain_text("connection failed")
    assert database(lambda: DiaryEntry.objects.count()) == 1
    page.unroute("**/capture/", lose_response)
    if members_changed:

        def change_members():
            current = Planting.objects.get(pk=other.pk)
            delete_planting(
                actor=user,
                pk=current.pk,
                expected_updated_at=current.updated_at,
            )
            create_planting(
                actor=user,
                crop=planting.crop_id,
                variety=replacement.pk,
                start_year=2026,
                batch_planting=planting.pk,
            )

        database(change_members)
    if choices_fail:
        page.route(
            "**/capture/",
            lambda route: route.fulfill(
                status=503, content_type="text/html", body="Choices unavailable"
            ),
        )
    page.reload()
    expect(page.locator("#id_body")).to_have_value(WRITING)
    expect(page.locator("[data-draft-status]")).to_contain_text("submission was kept")
    assert token(page) == key
    if choices_fail:
        page.unroute("**/capture/")
    page.locator("#save-entry").click()
    expect(page.locator("#quick-receipt")).to_contain_text("Observation already saved")
    assert database(lambda: DiaryEntry.objects.count()) == 1
    assert drafts(page) == {}
    if members_changed:
        assert database(
            lambda: Planting.objects.filter(
                cycle_id=planting.cycle_id, variety=replacement
            ).exists()
        )
        assert not database(lambda: Planting.objects.filter(pk=other.pk).exists())
    page.locator("#id_body").fill("Next independent observation")
    page.reload()
    expect(page.locator("#id_body")).to_have_value("Next independent observation")
    page.locator("#save-entry").click()
    expect(page.locator("#diary-results .diary-card")).to_have_count(2)
    assert drafts(page) == {}


def test_uncommitted_cycle_retry_still_rejects_a_concurrent_change(page, user):
    def seed():
        crop, variety = planting_choices(user)
        return create_planting(
            actor=user,
            crop=crop,
            variety=variety,
            start_year=2026,
            status="ongoing",
            started_on=date(2026, 1, 1),
        ).record

    planting = database(seed)
    page.reload()
    page.locator("#id_body").fill(WRITING)
    page.locator("[data-planting-action] > summary").click()
    page.locator('[name="planting_action"]').select_option("cycle")
    page.locator('[name="action_planting"]').select_option(str(planting.pk))
    expect(page.locator(f'[name="cycle-evaluation_{planting.pk}"]')).to_have_count(1)
    page.locator('[data-cycle-preset="complete"]').click()
    page.locator(f'[name="cycle-evaluation_{planting.pk}"]').fill(WRITING)
    key = token(page)
    page.route(
        "**/capture/",
        lambda route: route.fulfill(status=503, content_type="text/html", body="Unconfirmed save"),
    )
    page.locator("#save-entry").click()
    expect(page.locator("#capture-errors")).to_contain_text("Unconfirmed save")
    page.unroute("**/capture/")
    database(
        lambda: edit_planting(
            actor=user,
            pk=planting.pk,
            crop=planting.crop_id,
            variety=planting.variety_id,
            start_year=2026,
            notes="Concurrent correction",
            expected_updated_at=planting.updated_at,
        )
    )
    page.reload()
    expect(page.locator("#id_body")).to_have_value(WRITING)
    assert token(page) == key
    with page.expect_response(lambda response: response.url.endswith("/capture/")) as response:
        page.locator("#save-entry").click()
    assert response.value.status == 409
    assert database(lambda: DiaryEntry.objects.count()) == 0
    assert database(lambda: Planting.objects.get(pk=planting.pk).notes) == "Concurrent correction"
    assert database(lambda: Planting.objects.get(pk=planting.pk).cycle.status) == "ongoing"
    discard(page)
    expect(page.locator("#id_body")).to_have_value("")


def test_lost_entry_edit_response_recovers_the_original_version_and_key(page, live_server, user):
    entry = database(
        lambda: (
            create_entry(
                author=user,
                date=date(2026, 1, 1),
                body="Saved writing",
                uploads=[],
                submission_key=uuid4(),
            ).entry
        )
    )
    edit = live_server.url + entry.get_absolute_url() + "edit/"
    page.goto(edit)
    version = page.locator('[name="expected_updated_at"]').input_value()
    page.locator("#id_body").fill(WRITING)
    key = token(page)

    def lose_response(route):
        response = route.fetch()
        assert response.status == 204
        route.abort()

    page.route(edit, lose_response)
    page.locator('#entry-form button[type="submit"]').click()
    expect(page.locator("#entry-errors")).to_contain_text("connection failed")
    page.unroute(edit, lose_response)
    page.reload()
    expect(page.locator("#id_body")).to_have_value(WRITING)
    expect(page.locator('[name="expected_updated_at"]')).to_have_value(version)
    assert token(page) == key
    page.locator('#entry-form button[type="submit"]').click()
    expect(page).to_have_url(live_server.url + entry.get_absolute_url())
    assert drafts(page) == {} and database(lambda: DiaryEntry.objects.count()) == 1
