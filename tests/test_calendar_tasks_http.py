"""Calendar task and month-note writes over HTTP: forms, receipts and permissions."""

import re
from datetime import date
from html import unescape
from types import SimpleNamespace
from urllib.parse import urljoin
from uuid import uuid4

import pytest
from django.db import OperationalError
from django.urls import reverse
from django.utils import timezone

from core import receipts
from diary.services import create_entry
from garden.services import (
    create_area,
    create_crop,
    create_planting,
    create_planting_event,
    create_variety,
)
from planner import services
from planner.models import MonthNote, PlannedTask
from planner.queries import month_items

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def garden(user):
    crop = create_crop(actor=user, name="Synthetic tomato").record
    area = create_area(actor=user, name="Synthetic bed").record
    planting = create_planting(
        actor=user, crop=crop, start_year=2026, area=area, label="Synthetic batch"
    ).record
    return SimpleNamespace(crop=crop, area=area, planting=planting)


def task_payload(**changes):
    values = {
        "submission_key": str(uuid4()),
        "planned_date": "2026-05-12",
        "title": "Synthetic task",
        "note": "",
    }
    return {**values, **changes}


def test_new_and_edit_forms_need_a_signed_in_member(client, db):
    new_url = reverse("planner:task-new", args=[2026, 5])
    response = client.get(new_url)
    assert response.status_code == 302 and "/login/" in response["Location"]
    assert client.post(new_url, task_payload()).status_code == 302


def test_a_new_task_is_created_and_opened_in_its_month(client, user):
    client.force_login(user)
    url = reverse("planner:task-new", args=[2026, 5])
    response = client.post(url, task_payload())
    task = PlannedTask.objects.get()
    assert response.status_code == 302
    assert response["Location"] == f"/calendar/2026/5/?item=task-{task.pk}"
    page = client.get(response["Location"])
    assert page.status_code == 200
    assert b"Synthetic task" in page.content


def test_a_retried_new_task_returns_the_same_task(client, user):
    client.force_login(user)
    url = reverse("planner:task-new", args=[2026, 5])
    payload = task_payload()
    first = client.post(url, payload)
    second = client.post(url, payload)
    assert first.status_code == second.status_code == 302
    assert first["Location"] == second["Location"]
    assert PlannedTask.objects.count() == 1


@pytest.mark.parametrize("editing", [False, True])
def test_htmx_task_saves_and_retries_confirm_the_key_before_redirecting(client, user, editing):
    client.force_login(user)
    payload = task_payload()
    if editing:
        task = services.create_task(
            actor=user, planned_date=date(2026, 5, 10), title="Original task"
        ).task
        url = reverse("planner:task-edit", args=[task.pk])
        payload["expected_updated_at"] = task.updated_at.isoformat()
    else:
        url = reverse("planner:task-new", args=[2026, 5])
    for _attempt in range(2):
        response = client.post(url, payload, HTTP_HX_REQUEST="true")
        task = PlannedTask.objects.get()
        assert response.status_code == 204
        assert response["HX-Redirect"] == f"/calendar/2026/5/?item=task-{task.pk}"
        assert response["X-Growbook-Saved-Key"] == payload["submission_key"]
    assert task.title == payload["title"]


def test_a_failed_htmx_task_save_returns_field_errors_without_replacing_the_form(client, user):
    client.force_login(user)
    response = client.post(
        reverse("planner:task-new", args=[2026, 5]),
        task_payload(planned_date="1999-05-12"),
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 422
    assert b'data-field-errors="planned_date"' in response.content
    assert b"<form" not in response.content
    assert not PlannedTask.objects.exists()


def test_a_task_form_needs_a_title_or_a_note(client, user):
    client.force_login(user)
    response = client.post(
        reverse("planner:task-new", args=[2026, 5]), task_payload(title="", note="")
    )
    assert response.status_code == 422
    assert PlannedTask.objects.count() == 0


def test_the_month_page_offers_the_new_task_link_and_the_note_form(client, user):
    client.force_login(user)
    response = client.get(reverse("planner:month", args=[2026, 5]) + "?area=")
    assert response.status_code == 200
    assert reverse("planner:task-new", args=[2026, 5]).encode() in response.content
    assert b'name="note-body"' in response.content
    assert reverse("planner:note", args=[2026, 5]).encode() in response.content


def test_a_previous_year_item_prefills_the_new_task_form(client, user, garden):
    event = create_planting_event(
        actor=user, planting=garden.planting, kind="sowing", actual_date=date(2025, 5, 4)
    ).record
    client.force_login(user)
    page = client.get(reverse("planner:month", args=[2026, 5]))
    assert f"?from=event-{event.pk}".encode() in page.content
    response = client.get(reverse("planner:task-new", args=[2026, 5]) + f"?from=event-{event.pk}")
    assert response.status_code == 200
    assert b'value="2026-05-04"' in response.content
    assert b"Synthetic batch" in response.content


@pytest.mark.parametrize("kind", ["area", "crop", "variety", "planting"])
def test_copying_an_observations_single_link_keeps_the_task_in_its_filter(
    client, user, garden, kind
):
    target = (
        create_variety(actor=user, crop=garden.crop, name="Synthetic variety").record
        if kind == "variety"
        else getattr(garden, kind)
    )
    entry = create_entry(
        author=user,
        date=date(2025, 5, 4),
        title="Synthetic linked observation",
        body="Synthetic work to repeat",
        uploads=(),
        submission_key=uuid4(),
        show_in_calendar=True,
        links=(target,),
    ).entry
    client.force_login(user)
    url = reverse("planner:task-new", args=[2026, 5])
    page = client.get(url, {"from": f"entry-{entry.pk}"})
    assert page.status_code == 200
    form = page.context["form"]
    assert form["link"].value() == f"{kind}-{target.pk}"
    assert form.initial["planned_date"] == date(2026, 5, 4)
    response = client.post(
        url,
        task_payload(
            planned_date="2026-05-04",
            title=form["title"].value(),
            note=form["note"].value(),
            link=form["link"].value(),
        ),
    )
    assert response.status_code == 302
    task = PlannedTask.objects.get()
    assert getattr(task.links.get(), f"{kind}_id") == target.pk
    assert [item.task.pk for item in month_items(2026, 5, **{kind: target})] == [task.pk]


def test_copying_an_observation_with_several_non_planting_tags_does_not_guess_a_link(
    client, user, garden
):
    entry = create_entry(
        author=user,
        date=date(2025, 5, 4),
        body="Synthetic shared work",
        uploads=(),
        submission_key=uuid4(),
        show_in_calendar=True,
        links=(garden.area, garden.crop),
    ).entry
    client.force_login(user)
    page = client.get(reverse("planner:task-new", args=[2026, 5]), {"from": f"entry-{entry.pk}"})
    assert page.status_code == 200
    assert not page.context["form"]["link"].value()


def test_an_unknown_previous_year_item_is_not_found(client, user):
    client.force_login(user)
    response = client.get(reverse("planner:task-new", args=[2026, 5]) + "?from=event-999")
    assert response.status_code == 404


def test_editing_a_task_rejects_a_stale_version(client, user):
    task = services.create_task(
        actor=user, planned_date=date(2026, 5, 10), title="Synthetic task"
    ).task
    version = task.updated_at
    services.edit_task(
        actor=user,
        pk=task.pk,
        planned_date=date(2026, 5, 10),
        title="Later title",
        expected_updated_at=version,
        submission_key=receipts.submission_key(None),
    )
    client.force_login(user)
    url = reverse("planner:task-edit", args=[task.pk])
    payload = {
        "submission_key": str(uuid4()),
        "planned_date": "2026-05-10",
        "title": "Stale title",
        "expected_updated_at": version.isoformat(),
    }
    response = client.post(url, payload)
    assert response.status_code == 409
    task.refresh_from_db()
    assert task.title == "Later title"
    fresh = {
        **payload,
        "expected_updated_at": task.updated_at.isoformat(),
        "submission_key": str(uuid4()),
    }
    assert client.post(url, fresh).status_code == 302
    task.refresh_from_db()
    assert task.title == "Stale title"


def test_the_panel_completes_and_reopens_a_task(client, user):
    client.force_login(user)
    task = services.create_task(
        actor=user, planned_date=date(2026, 5, 10), title="Synthetic task"
    ).task
    panel = client.get(reverse("planner:month", args=[2026, 5]) + f"?item=task-{task.pk}")
    assert reverse("planner:task-complete", args=[task.pk]).encode() in panel.content
    assert b'name="completed_on"' in panel.content
    assert f'value="{timezone.localdate().isoformat()}"'.encode() in panel.content
    response = client.post(
        reverse("planner:task-complete", args=[task.pk]),
        {
            "submission_key": str(uuid4()),
            "expected_updated_at": task.updated_at.isoformat(),
            "completed_on": "2026-05-12",
        },
    )
    assert response.status_code == 302
    task.refresh_from_db()
    assert task.completed_on == date(2026, 5, 12)
    day = client.get(reverse("planner:month", args=[2026, 5]))
    cell = re.search(r'data-date="2026-05-12">(.*?)</td>', day.text, re.S).group(1)
    assert f"item=task-{task.pk}" in cell
    panel = client.get(reverse("planner:month", args=[2026, 5]) + f"?item=task-{task.pk}")
    assert reverse("planner:task-reopen", args=[task.pk]).encode() in panel.content
    response = client.post(
        reverse("planner:task-reopen", args=[task.pk]),
        {
            "submission_key": str(uuid4()),
            "expected_updated_at": task.updated_at.isoformat(),
        },
    )
    assert response.status_code == 302
    task.refresh_from_db()
    assert task.completed_on is None


def test_a_stale_panel_action_changes_nothing(client, user):
    client.force_login(user)
    task = services.create_task(
        actor=user, planned_date=date(2026, 5, 10), title="Synthetic task"
    ).task
    version = task.updated_at
    services.edit_task(
        actor=user,
        pk=task.pk,
        planned_date=date(2026, 5, 10),
        title="Later title",
        expected_updated_at=version,
        submission_key=receipts.submission_key(None),
    )
    response = client.post(
        reverse("planner:task-complete", args=[task.pk]),
        {
            "submission_key": str(uuid4()),
            "expected_updated_at": version.isoformat(),
            "completed_on": "2026-05-12",
        },
    )
    assert response.status_code == 302
    task.refresh_from_db()
    assert task.completed_on is None


def test_deleting_a_task_needs_confirmation(client, user):
    client.force_login(user)
    task = services.create_task(
        actor=user, planned_date=date(2026, 5, 10), title="Synthetic task"
    ).task
    url = reverse("planner:task-delete", args=[task.pk])
    assert client.get(url).status_code == 200
    refused = client.post(url, {"expected_updated_at": task.updated_at.isoformat()})
    assert refused.status_code == 422
    assert PlannedTask.objects.filter(pk=task.pk).exists()
    accepted = client.post(
        url,
        {"expected_updated_at": task.updated_at.isoformat(), "confirm": "on"},
    )
    assert accepted.status_code == 302
    assert not PlannedTask.objects.filter(pk=task.pk).exists()


def test_the_month_note_is_saved_and_a_stale_save_keeps_the_text(client, user):
    client.force_login(user)
    url = reverse("planner:note", args=[2026, 5])
    response = client.post(
        url,
        {"note-submission_key": str(uuid4()), "note-body": "Synthetic note"},
    )
    assert response.status_code == 302
    note = MonthNote.objects.get(year=2026, month=5)
    assert note.body == "Synthetic note"
    stale = client.post(
        url,
        {
            "note-submission_key": str(uuid4()),
            "note-body": "Stale text",
            "note-expected_updated_at": "2026-05-01T10:00:00+00:00",
        },
    )
    assert stale.status_code == 409
    assert b"Stale text" in stale.content
    note.refresh_from_db()
    assert note.body == "Synthetic note"


def test_a_note_retry_is_idempotent(client, user):
    client.force_login(user)
    url = reverse("planner:note", args=[2026, 5])
    payload = {"note-submission_key": str(uuid4()), "note-body": "Synthetic note"}
    assert client.post(url, payload).status_code == 302
    assert client.post(url, payload).status_code == 302
    assert MonthNote.objects.count() == 1


@pytest.mark.parametrize("failure", ["conflict", "database"])
def test_failed_note_pages_keep_item_and_language_return_links_readable(
    client, user, monkeypatch, failure
):
    task = services.create_task(
        actor=user, planned_date=date(2026, 5, 10), title="Synthetic task"
    ).task

    def failed_save(**kwargs):
        if failure == "conflict":
            raise services.PlannerConflict("Synthetic note conflict")
        raise OperationalError("Synthetic database contention")

    monkeypatch.setattr(services, "save_month_note", failed_save)
    client.force_login(user)
    url = reverse("planner:note", args=[2026, 5], query=[("type", "task"), ("type", "event")])
    failed = client.post(
        url, {"note-submission_key": str(uuid4()), "note-body": "Preserved synthetic writing"}
    )
    assert failed.status_code == (409 if failure == "conflict" else 503)
    assert b"Preserved synthetic writing" in failed.content
    item_link = unescape(re.search(r'href="([^"]*item=task-\d+[^"]*)"', failed.text).group(1))
    opened = client.get(urljoin(url, item_link), follow=True)
    assert opened.status_code == 200
    assert opened.context["panel"]["item"].task.pk == task.pk
    assert opened.wsgi_request.GET.getlist("type") == ["task", "event"]
    language_next = unescape(re.search(r'name="next" value="([^"]*)"', failed.text).group(1))
    returned = client.get(language_next, follow=True)
    assert returned.status_code == 200
    assert returned.wsgi_request.path == reverse("planner:month", args=[2026, 5])
    assert returned.wsgi_request.GET.getlist("type") == ["task", "event"]
    assert not MonthNote.objects.exists()


def test_an_out_of_range_month_has_no_task_form(client, user):
    client.force_login(user)
    assert client.get(reverse("planner:task-new", args=[1999, 5])).status_code == 404
    assert (
        client.post(
            reverse("planner:note", args=[1999, 5]),
            {"note-submission_key": str(uuid4()), "note-body": "Synthetic note"},
        ).status_code
        == 404
    )


def test_an_inactive_account_cannot_write(client, user):
    task = services.create_task(
        actor=user, planned_date=date(2026, 5, 10), title="Synthetic task"
    ).task
    user.is_active = False
    user.save(update_fields=["is_active"])
    client.force_login(user)
    response = client.post(
        reverse("planner:task-complete", args=[task.pk]),
        {
            "submission_key": str(uuid4()),
            "expected_updated_at": task.updated_at.isoformat(),
            "completed_on": "2026-05-12",
        },
    )
    # A deactivated account is signed out rather than served the write.
    assert response.status_code == 302 and "/login/" in response["Location"]
    task.refresh_from_db()
    assert task.completed_on is None


def test_the_edit_form_preselects_the_current_link(client, user, garden):
    task = services.create_task(
        actor=user,
        planned_date=date(2026, 5, 10),
        title="Synthetic task",
        links=(garden.planting,),
    ).task
    client.force_login(user)
    page = client.get(reverse("planner:task-edit", args=[task.pk]))
    assert page.status_code == 200
    assert f'value="planting-{garden.planting.pk}" selected'.encode() in page.content


def test_a_retry_survives_archiving_the_linked_record(client, user, garden):
    from garden.services import archive_area

    client.force_login(user)
    payload = {
        "submission_key": str(uuid4()),
        "planned_date": "2026-05-12",
        "title": "Synthetic task",
        "note": "",
        "link": f"area-{garden.area.pk}",
    }
    url = reverse("planner:task-new", args=[2026, 5])
    first = client.post(url, payload)
    assert first.status_code == 302
    archive_area(actor=user, pk=garden.area.pk)
    retry = client.post(url, payload)
    assert retry.status_code == 302
    assert retry["Location"] == first["Location"]
    assert PlannedTask.objects.count() == 1
    assert PlannedTask.objects.get().links.get().area_id == garden.area.pk


def test_the_note_form_addresses_the_month_not_an_opened_item(client, user):
    task = services.create_task(
        actor=user, planned_date=date(2026, 5, 10), title="Synthetic task"
    ).task
    client.force_login(user)
    page_url = reverse("planner:month", args=[2026, 5]) + f"?area=&item=task-{task.pk}"
    page = client.get(page_url)
    assert page.status_code == 200
    action = re.search(r'action="(/calendar/2026/5/note/[^"]*)"', page.text).group(1)
    assert "item=" not in action and "day=" not in action
    saved = client.post(
        action,
        {"note-submission_key": str(uuid4()), "note-body": "Synthetic note"},
    )
    assert saved.status_code == 302
    assert saved["Location"] == f"{reverse('planner:month', args=[2026, 5])}?area="
    reopened = client.get(saved["Location"])
    assert b"<dialog" not in reopened.content
    assert MonthNote.objects.get().body == "Synthetic note"


@pytest.mark.parametrize(
    "value",
    [
        "area-²",
        "area-" + "9" * 4301,
        "area-0",
        "area-99999999999999999999",
        "area-1x",
        "elsewhere-1",
    ],
)
def test_a_malformed_link_address_is_a_form_error(value):
    from django import forms as django_forms

    from planner.forms import parse_link

    with pytest.raises(django_forms.ValidationError):
        parse_link(value)


def test_a_malformed_link_address_is_unprocessable_not_a_server_error(client, user):
    client.force_login(user)
    response = client.post(
        reverse("planner:task-new", args=[2026, 5]),
        task_payload(link="area-²"),
    )
    assert response.status_code == 422
    assert PlannedTask.objects.count() == 0
