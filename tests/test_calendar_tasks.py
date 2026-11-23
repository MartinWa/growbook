"""Planned tasks, their links and the shared month note."""

from datetime import date

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import translation

from core import receipts
from garden.services import create_area, create_crop, create_planting, create_planting_event
from planner import services
from planner.models import MonthNote, PlannedTask, PlannerSubmission, TaskLink
from planner.queries import month_context, month_items, previous_year_items, task_label

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def english_messages():
    """Assert the source messages; the Swedish catalog is checked separately."""
    with translation.override("en"):
        yield


@pytest.fixture
def garden(user):
    crop = create_crop(actor=user, name="Synthetic tomato").record
    parent = create_area(actor=user, name="Synthetic greenhouse").record
    child = create_area(actor=user, name="Synthetic bed", parent=parent).record
    planting = create_planting(
        actor=user, crop=crop, start_year=2026, area=child, label="Synthetic batch"
    ).record
    return {"crop": crop, "parent": parent, "child": child, "planting": planting}


def make_task(user, *, day=date(2026, 5, 10), title="Synthetic task", note="", links=()):
    return services.create_task(
        actor=user, planned_date=day, title=title, note=note, links=links
    ).task


def test_a_planned_task_sits_on_its_planned_day(user):
    task = make_task(user)
    items = month_items(2026, 5)
    assert [(item.kind, item.day, item.pk) for item in items] == [
        ("task", date(2026, 5, 10), task.pk)
    ]
    assert items[0].planned
    assert items[0].item_param == f"task-{task.pk}"


def test_a_completed_task_moves_to_its_completion_day_and_reopening_restores_it(user):
    task = make_task(user)
    services.complete_task(
        actor=user,
        pk=task.pk,
        completed_on=date(2026, 5, 20),
        expected_updated_at=task.updated_at,
    )
    task.refresh_from_db()
    assert task.completed_on == date(2026, 5, 20)
    items = month_items(2026, 5)
    assert [(item.day, item.planned) for item in items] == [(date(2026, 5, 20), False)]

    services.reopen_task(actor=user, pk=task.pk, expected_updated_at=task.updated_at)
    task.refresh_from_db()
    assert task.completed_on is None
    items = month_items(2026, 5)
    assert [(item.day, item.planned) for item in items] == [(date(2026, 5, 10), True)]


def test_a_task_completed_in_another_month_leaves_the_planned_month(user):
    task = make_task(user)
    services.complete_task(
        actor=user,
        pk=task.pk,
        completed_on=date(2026, 6, 3),
        expected_updated_at=task.updated_at,
    )
    assert month_items(2026, 5) == []
    items = month_items(2026, 6)
    assert [(item.day, item.planned) for item in items] == [(date(2026, 6, 3), False)]


def test_a_task_without_a_title_is_labelled_by_its_note_then_its_date(user):
    noted = make_task(user, title="", note="Turn the compost heap")
    assert task_label(noted) == "Turn the compost heap"
    # The constraint needs a title or a note, so only a stray row falls back to the date.
    stray = PlannedTask(planned_date=date(2026, 5, 11))
    with translation.override("sv"):
        assert task_label(stray) == "11 maj 2026"
    with translation.override("en"):
        assert task_label(stray) == "May 11, 2026"


def test_a_task_needs_a_title_or_a_note(user):
    with pytest.raises(ValidationError, match="title or a note"):
        services.create_task(actor=user, planned_date=date(2026, 5, 10), title=" ", note=" ")


def test_task_links_carry_one_target_and_drive_the_shared_filters(user, garden):
    area_task = make_task(user, title="Area task", links=(garden["child"],))
    make_task(user, title="Crop task", links=(("crop", garden["crop"].pk),))
    make_task(user, title="Planting task", links=(("planting", garden["planting"].pk),))
    assert TaskLink.objects.filter(task=area_task).count() == 1

    def titles(**filters):
        return sorted(item.title for item in month_items(2026, 5, **filters))

    assert titles(area=garden["parent"]) == ["Area task", "Planting task"]
    assert titles(crop=garden["crop"]) == ["Crop task", "Planting task"]
    assert titles(planting=garden["planting"]) == ["Planting task"]
    assert titles(kinds=frozenset({"task"})) == ["Area task", "Crop task", "Planting task"]


def test_a_task_link_rejects_a_duplicate_or_a_missing_record(user, garden):
    with pytest.raises(ValidationError, match="only be linked once"):
        services.create_task(
            actor=user,
            planned_date=date(2026, 5, 10),
            title="Synthetic task",
            links=(garden["child"], ("area", garden["child"].pk)),
        )
    with pytest.raises(ValidationError, match="valid record"):
        services.create_task(
            actor=user,
            planned_date=date(2026, 5, 10),
            title="Synthetic task",
            links=(("planting", 999),),
        )


def test_editing_a_task_replaces_its_links_and_keeps_the_completion(user, garden):
    task = make_task(user, links=(garden["child"],))
    services.complete_task(
        actor=user, pk=task.pk, completed_on=date(2026, 5, 12), expected_updated_at=task.updated_at
    )
    task.refresh_from_db()
    edited = services.edit_task(
        actor=user,
        pk=task.pk,
        planned_date=date(2026, 5, 10),
        title="Synthetic task",
        note="Reworded",
        links=(("planting", garden["planting"].pk),),
        expected_updated_at=task.updated_at,
    ).task
    assert edited.completed_on == date(2026, 5, 12)
    assert list(edited.links.values_list("planting_id", flat=True)) == [garden["planting"].pk]


def test_creating_a_task_replays_its_receipt_and_rejects_other_content(user):
    key = receipts.submission_key(None)
    values = {
        "actor": user,
        "planned_date": date(2026, 5, 10),
        "title": "Synthetic task",
        "submission_key": key,
    }
    services.create_task(**values)
    saved = PlannedTask.objects.get()
    replay = services.create_task(**values)
    assert replay.replayed and replay.task.pk == saved.pk
    assert PlannedTask.objects.count() == 1
    with pytest.raises(services.PlannerConflict, match="different content"):
        services.create_task(**{**values, "title": "Changed retry"})


def test_a_stale_task_version_is_rejected_but_a_retry_precedes_it(user):
    task = make_task(user)
    first = services.edit_task(
        actor=user,
        pk=task.pk,
        planned_date=date(2026, 5, 10),
        title="First edit",
        expected_updated_at=task.updated_at,
        submission_key=receipts.submission_key(None),
    ).task
    with pytest.raises(services.PlannerConflict, match="changed while the form was open"):
        services.edit_task(
            actor=user,
            pk=task.pk,
            planned_date=date(2026, 5, 10),
            title="Second edit",
            expected_updated_at=task.updated_at,
            submission_key=receipts.submission_key(None),
        )
    assert first.title == "First edit"


def test_completing_a_task_twice_with_one_submission_replays_it(user):
    task = make_task(user)
    key = receipts.submission_key(None)
    today = date(2026, 5, 15)
    first = services.complete_task(
        actor=user,
        pk=task.pk,
        completed_on=today,
        submission_key=key,
        expected_updated_at=task.updated_at,
    )
    replay = services.complete_task(
        actor=user,
        pk=task.pk,
        completed_on=today,
        submission_key=key,
        expected_updated_at=task.updated_at,
    )
    assert first.replayed is False and replay.replayed is True
    assert PlannerSubmission.objects.filter(key=key).count() == 1


def test_a_deleted_task_keeps_its_receipt_as_a_tombstone(user):
    task = make_task(user)
    version = task.updated_at
    key = receipts.submission_key(None)
    services.complete_task(
        actor=user,
        pk=task.pk,
        completed_on=date(2026, 5, 15),
        submission_key=key,
        expected_updated_at=version,
    )
    task.refresh_from_db()
    services.delete_task(actor=user, pk=task.pk, expected_updated_at=task.updated_at)
    receipt = PlannerSubmission.objects.get(key=key)
    assert receipt.task_id is None
    with pytest.raises(services.PlannerConflict, match="deleted"):
        services.complete_task(
            actor=user,
            pk=task.pk,
            completed_on=date(2026, 5, 15),
            submission_key=key,
            expected_updated_at=version,
        )


def test_deleting_a_task_needs_the_current_version(user):
    task = make_task(user)
    services.edit_task(
        actor=user,
        pk=task.pk,
        planned_date=date(2026, 5, 10),
        title="Later",
        expected_updated_at=task.updated_at,
        submission_key=receipts.submission_key(None),
    )
    with pytest.raises(services.PlannerConflict, match="changed while the form was open"):
        services.delete_task(actor=user, pk=task.pk, expected_updated_at=task.updated_at)


def test_inactive_accounts_cannot_write(user):
    user.is_active = False
    user.save(update_fields=["is_active"])
    with pytest.raises(PermissionDenied):
        services.create_task(actor=user, planned_date=date(2026, 5, 10), title="Synthetic task")
    with pytest.raises(PermissionDenied):
        services.save_month_note(actor=user, year=2026, month=5, body="Synthetic note")


def test_the_previous_year_lists_completed_tasks_and_never_copies_them(user):
    done = services.create_task(actor=user, planned_date=date(2025, 5, 2), title="Old task").task
    services.complete_task(
        actor=user,
        pk=done.pk,
        completed_on=date(2025, 5, 4),
        expected_updated_at=done.updated_at,
    )
    items = previous_year_items(2026, 5)
    assert [(item.kind, item.day) for item in items] == [("task", date(2025, 5, 4))]
    assert month_items(2026, 5) == []


def test_a_month_note_is_saved_once_and_guarded_by_its_version(user):
    first = services.save_month_note(
        actor=user,
        year=2026,
        month=5,
        body="First note",
        submission_key=receipts.submission_key(None),
    )
    assert first.note.body == "First note"
    note = MonthNote.objects.get(year=2026, month=5)
    second = services.save_month_note(
        actor=user,
        year=2026,
        month=5,
        body="Second note",
        expected_updated_at=note.updated_at,
        submission_key=receipts.submission_key(None),
    )
    assert second.note.body == "Second note"
    with pytest.raises(services.PlannerConflict, match="changed while the form was open"):
        services.save_month_note(
            actor=user,
            year=2026,
            month=5,
            body="Stale note",
            expected_updated_at=note.updated_at,
            submission_key=receipts.submission_key(None),
        )


def test_a_note_saved_twice_with_one_key_replays_the_first_save(user):
    key = receipts.submission_key(None)
    values = {
        "actor": user,
        "year": 2026,
        "month": 5,
        "body": "Synthetic note",
        "submission_key": key,
    }
    first = services.save_month_note(**values)
    replay = services.save_month_note(**values)
    assert first.replayed is False and replay.replayed is True
    assert MonthNote.objects.count() == 1


def test_a_first_note_save_loses_to_a_note_created_meanwhile(user):
    services.save_month_note(actor=user, year=2026, month=5, body="Synthetic note")
    with pytest.raises(services.PlannerConflict, match="changed while the form was open"):
        services.save_month_note(actor=user, year=2026, month=5, body="Late first save")


def test_the_month_context_exposes_the_note(user):
    services.save_month_note(actor=user, year=2026, month=5, body="Synthetic note")
    context = month_context(2026, 5, today=date(2026, 5, 9))
    assert context["month_note"].body == "Synthetic note"
    assert context["new_task_date"] == date(2026, 5, 9)
    other = month_context(2026, 6, today=date(2026, 5, 9))
    assert other["month_note"] is None
    assert other["new_task_date"] == date(2026, 6, 1)


def test_a_task_and_an_event_on_one_day_are_separate_items(user, garden):
    make_task(user, day=date(2026, 5, 9))
    create_planting_event(
        actor=user, planting=garden["planting"], kind="sowing", actual_date=date(2026, 5, 9)
    )
    kinds = sorted(item.kind for item in month_items(2026, 5))
    assert kinds == ["event", "task"]
