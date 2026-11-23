"""Calendar month reads, placement rules and the diary entry-to-event link."""

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.utils import translation

from diary.actions import PlantingEventAction
from diary.models import DiaryEntry
from diary.queries import filter_entries
from diary.services import create_entry, delete_entry, payload_fingerprint, update_entry
from garden.models import PlantingEvent
from garden.services import (
    create_area,
    create_crop,
    create_planting,
    create_planting_event,
    delete_planting_event,
)
from planner.queries import (
    entry_label,
    entry_queryset,
    month_bounds,
    month_context,
    month_items,
    parse_day,
    parse_item,
    previous_year_items,
    weekday_names,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def garden(user):
    crop = create_crop(actor=user, name="Synthetic tomato").record
    parent = create_area(actor=user, name="Synthetic greenhouse").record
    child = create_area(actor=user, name="Synthetic bed", parent=parent).record
    planting = create_planting(
        actor=user,
        crop=crop,
        start_year=2026,
        area=child,
        label="Synthetic batch",
    ).record
    return SimpleNamespace(crop=crop, parent=parent, child=child, planting=planting)


def make_event(user, garden, *, kind="sowing", planned=None, actual=None):
    return create_planting_event(
        actor=user, planting=garden.planting, kind=kind, planned_date=planned, actual_date=actual
    ).record


def make_entry(
    user,
    *,
    day=date(2026, 5, 9),
    title="",
    body="Synthetic observation",
    show_in_calendar=False,
    action=None,
    uploads=(),
):
    return create_entry(
        author=user,
        date=day,
        title=title,
        body=body,
        uploads=uploads,
        submission_key=uuid4(),
        show_in_calendar=show_in_calendar,
        action=action,
    ).entry


def test_month_bounds_include_the_leap_day():
    assert month_bounds(2028, 2) == (date(2028, 2, 1), date(2028, 2, 29))
    assert month_bounds(2026, 4) == (date(2026, 4, 1), date(2026, 4, 30))


def test_month_items_stay_inside_the_month(user, garden):
    make_event(user, garden, planned=date(2028, 1, 31))
    inside = make_event(user, garden, planned=date(2028, 2, 29))
    make_event(user, garden, planned=date(2028, 3, 1))
    items = month_items(2028, 2)
    assert [item.event.pk for item in items] == [inside.pk]
    assert items[0].day == date(2028, 2, 29)


def test_planned_and_done_events_move_to_their_actual_date(user, garden):
    planned = make_event(user, garden, planned=date(2026, 5, 10))
    done = make_event(user, garden, planned=date(2026, 5, 11), actual=date(2026, 5, 20))
    items = month_items(2026, 5)
    assert [(item.event.pk, item.day, item.planned) for item in items] == [
        (planned.pk, date(2026, 5, 10), True),
        (done.pk, date(2026, 5, 20), False),
    ]
    # A localized request elsewhere leaves the active language set; state it here.
    with translation.override("sv"):
        assert items[0].state_label == "Planerad"
        assert items[1].state_label == "Klart"


def test_event_with_only_a_planned_date_is_planned(user, garden):
    record = make_event(user, garden, planned=date(2026, 5, 10))
    item = month_items(2026, 5)[0]
    assert item.event == record
    with translation.override("sv"):
        assert "Sådd" in item.title
        assert "Planerad" in item.meta


def test_entry_and_event_render_once_on_the_event_date(user, garden):
    entry = make_entry(
        user,
        day=date(2026, 5, 9),
        body="Sowed in the synthetic bed",
        show_in_calendar=True,
        action=PlantingEventAction(
            planting=garden.planting.pk, kind="sowing", actual_date=date(2026, 5, 12)
        ),
    )
    items = month_items(2026, 5)
    assert len(items) == 1
    item = items[0]
    assert item.kind == "combined"
    assert item.day == date(2026, 5, 12)
    assert item.entry == entry
    assert item.entry_url == entry.get_absolute_url()
    assert item.event_url == entry.event.get_absolute_url()


def test_clearing_the_checkbox_leaves_only_the_event(user, garden):
    entry = make_entry(
        user,
        show_in_calendar=True,
        action=PlantingEventAction(planting=garden.planting.pk, kind="sowing"),
    )
    assert month_items(2026, 5)[0].kind == "combined"
    update_entry(
        actor=user,
        pk=entry.pk,
        date=entry.date,
        body=entry.body,
        show_in_calendar=False,
        expected_updated_at=entry.updated_at,
    )
    items = month_items(2026, 5)
    assert len(items) == 1
    assert items[0].kind == "event"
    assert items[0].entry is None


def test_entry_alone_when_its_event_is_in_another_month(user, garden):
    entry = make_entry(
        user,
        day=date(2026, 5, 9),
        show_in_calendar=True,
        action=PlantingEventAction(
            planting=garden.planting.pk, kind="sowing", actual_date=date(2026, 6, 1)
        ),
    )
    may = month_items(2026, 5)
    june = month_items(2026, 6)
    assert [(item.kind, item.day) for item in may] == [("entry", date(2026, 5, 9))]
    assert may[0].entry == entry
    assert [(item.kind, item.day) for item in june] == [("event", date(2026, 6, 1))]


def tag_entry(user, entry, *plantings):
    return update_entry(
        actor=user,
        pk=entry.pk,
        date=entry.date,
        body=entry.body,
        show_in_calendar=entry.show_in_calendar,
        links=[("planting", planting.pk) for planting in plantings],
        expected_updated_at=entry.updated_at,
    )


def test_tagged_entry_pairs_with_the_event_of_its_planting_on_the_same_day(user, garden):
    record = make_event(user, garden, actual=date(2026, 5, 9))
    entry = make_entry(user, day=date(2026, 5, 9), show_in_calendar=True)
    entry = tag_entry(user, entry, garden.planting)
    items = month_items(2026, 5)
    assert len(items) == 1
    assert items[0].kind == "combined"
    assert items[0].event == record
    assert items[0].entry == entry
    assert items[0].day == date(2026, 5, 9)


def test_pairing_keeps_an_event_on_another_day_separate(user, garden):
    record = make_event(user, garden, actual=date(2026, 5, 10))
    entry = make_entry(user, day=date(2026, 5, 9), show_in_calendar=True)
    entry = tag_entry(user, entry, garden.planting)
    items = month_items(2026, 5)
    assert [(item.kind, item.day) for item in items] == [
        ("entry", date(2026, 5, 9)),
        ("event", date(2026, 5, 10)),
    ]
    assert items[1].event == record
    assert items[0].event is None
    assert items[0].entry == entry


def test_ambiguous_events_are_not_paired(user, garden):
    make_event(user, garden, kind="sowing", actual=date(2026, 5, 9))
    make_event(user, garden, kind="other", actual=date(2026, 5, 9))
    entry = make_entry(user, day=date(2026, 5, 9), show_in_calendar=True)
    entry = tag_entry(user, entry, garden.planting)
    items = month_items(2026, 5)
    assert sorted(item.kind for item in items) == ["entry", "event", "event"]
    assert [item.entry for item in items if item.kind == "entry"] == [entry]


def test_one_event_pairs_with_only_one_entry(user, garden):
    make_event(user, garden, actual=date(2026, 5, 9))
    first = make_entry(user, day=date(2026, 5, 9), show_in_calendar=True)
    second = make_entry(user, day=date(2026, 5, 9), show_in_calendar=True)
    first = tag_entry(user, first, garden.planting)
    second = tag_entry(user, second, garden.planting)
    items = month_items(2026, 5)
    assert sorted(item.kind for item in items) == ["combined", "entry"]
    combined = next(item for item in items if item.kind == "combined")
    alone = next(item for item in items if item.kind == "entry")
    assert (combined.entry, alone.entry) == (first, second)


def test_a_second_planting_link_does_not_pair_the_entry(user, garden):
    other_crop = create_crop(actor=user, name="Synthetic bean").record
    other = create_planting(actor=user, crop=other_crop, start_year=2026).record
    make_event(user, garden, actual=date(2026, 5, 9))
    create_planting_event(actor=user, planting=other, kind="sowing", actual_date=date(2026, 5, 9))
    tag_entry(
        user,
        make_entry(user, day=date(2026, 5, 9), show_in_calendar=True),
        garden.planting,
        other,
    )
    assert sorted(item.kind for item in month_items(2026, 5)) == ["entry", "event", "event"]


def test_hidden_entry_is_absent_from_the_month(user):
    make_entry(user, show_in_calendar=False)
    assert month_items(2026, 5) == []


def test_untitled_photo_only_entry_is_labelled_with_its_localized_date(user, image_upload):
    entry = make_entry(user, day=date(2026, 5, 9), body="", uploads=[image_upload()])
    with translation.override("sv"):
        assert entry_label(entry) == "9 maj 2026"
    with translation.override("en"):
        assert entry_label(entry) == "May 9, 2026"


def test_entry_label_prefers_title_then_first_note_line(user):
    titled = make_entry(user, title="Synthetic title", body="Synthetic note")
    untitled = make_entry(user, body="First line\nSecond line")
    long = make_entry(user, body="x" * 120)
    assert entry_label(titled) == "Synthetic title"
    assert entry_label(untitled) == "First line"
    assert entry_label(long).endswith("…")
    assert len(entry_label(long)) == 80


def test_combined_item_links_both_records(user, garden):
    make_entry(
        user,
        title="Synthetic combined",
        show_in_calendar=True,
        action=PlantingEventAction(planting=garden.planting.pk, kind="sowing"),
    )
    item = month_items(2026, 5)[0]
    entry = DiaryEntry.objects.get(title="Synthetic combined")
    assert item.primary_url == entry.get_absolute_url()
    assert item.event_url.endswith(f"#event-{entry.event_id}")


def test_entry_and_event_queries_share_diary_filter_semantics(user, garden):
    event_record = make_event(user, garden, planned=date(2026, 5, 10))
    entry = make_entry(user, day=date(2026, 5, 9), show_in_calendar=True)
    entry = update_entry(
        actor=user,
        pk=entry.pk,
        date=entry.date,
        body=entry.body,
        show_in_calendar=True,
        links=[("planting", garden.planting.pk)],
        expected_updated_at=entry.updated_at,
    )
    assert event_record.planting.area_id == garden.child.pk
    for kind, record in (
        ("area", garden.parent),
        ("area", garden.child),
        ("crop", garden.crop),
        ("planting", garden.planting),
    ):
        ours = set(entry_queryset(**{kind: record}).values_list("pk", flat=True))
        theirs = set(filter_entries(**{kind: record}).values_list("pk", flat=True))
        assert ours == theirs == {entry.pk}
    assert set(entry_queryset(planting=garden.planting).values_list("pk", flat=True)) == {entry.pk}
    assert set(entry_queryset().values_list("pk", flat=True)) == {entry.pk}


def test_event_filters_follow_the_planting(user, garden):
    other_crop = create_crop(actor=user, name="Synthetic bean").record
    outside = create_area(actor=user, name="Synthetic far bed").record
    other = create_planting(actor=user, crop=other_crop, start_year=2026, area=outside).record
    inside_event = make_event(user, garden, planned=date(2026, 5, 10))
    outside_event = create_planting_event(
        actor=user, planting=other, kind="sowing", planned_date=date(2026, 5, 11)
    ).record
    assert [item.event for item in month_items(2026, 5, area=garden.parent)] == [inside_event]
    assert [item.event for item in month_items(2026, 5, crop=other_crop)] == [outside_event]
    assert [item.event for item in month_items(2026, 5, planting=garden.planting)] == [inside_event]


def test_previous_year_items_are_bounded(user, garden):
    make_event(user, garden, planned=date(2000, 5, 10))
    assert [item.day for item in previous_year_items(2001, 5)] == [date(2000, 5, 10)]
    assert previous_year_items(2000, 5) == []


def test_navigation_is_bounded_to_the_year_range():
    first = month_context(2000, 1)
    assert first["previous"] is None
    assert first["following"]["year"] == 2000
    assert first["following"]["month"] == 2


def test_weekday_names_start_on_monday():
    with translation.override("en"):
        assert weekday_names() == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    with translation.override("sv"):
        assert weekday_names()[0] == "mån"


def test_month_grid_is_monday_first_and_marks_today():
    context = month_context(2026, 5, today=date(2026, 5, 9))
    assert all(week[0].day.weekday() == 0 for week in context["weeks"])
    today = [day for week in context["weeks"] for day in week if day.is_today]
    assert [day.day for day in today] == [date(2026, 5, 9)]


def test_action_result_links_the_entry_to_its_new_event(user, garden):
    entry = make_entry(
        user,
        action=PlantingEventAction(planting=garden.planting.pk, kind="sowing"),
    )
    assert entry.event_id is not None
    assert entry.event.diary_entry == entry
    assert entry.links.filter(planting=garden.planting).exists()


def test_event_link_is_written_once_on_a_retry(user, garden):
    key = uuid4()
    action = PlantingEventAction(planting=garden.planting.pk, kind="sowing")
    first = create_entry(
        author=user,
        date=date(2026, 5, 9),
        body="Synthetic retried observation",
        uploads=(),
        submission_key=key,
        action=action,
    )
    second = create_entry(
        author=user,
        date=date(2026, 5, 9),
        body="Synthetic retried observation",
        uploads=(),
        submission_key=key,
        action=action,
    )
    assert first.entry.pk == second.entry.pk
    assert not second.created
    assert PlantingEvent.objects.filter(planting=garden.planting).count() == 1
    first.entry.refresh_from_db()
    assert first.entry.event_id is not None


def test_deleting_the_entry_keeps_the_event_and_deleting_the_event_clears_the_link(user, garden):
    entry = make_entry(
        user,
        action=PlantingEventAction(planting=garden.planting.pk, kind="sowing"),
    )
    event_id = entry.event_id
    delete_entry(actor=user, pk=entry.pk, expected_updated_at=entry.updated_at)
    assert PlantingEvent.objects.filter(pk=event_id).exists()

    second = make_entry(
        user,
        action=PlantingEventAction(planting=garden.planting.pk, kind="harvest_start"),
    )
    record = second.event
    delete_planting_event(actor=user, pk=record.pk, expected_updated_at=record.updated_at)
    second.refresh_from_db()
    assert second.event_id is None


def test_hidden_calendar_visibility_does_not_change_existing_fingerprints():
    hidden = payload_fingerprint(date(2026, 5, 9), "Synthetic", ())
    assert payload_fingerprint(date(2026, 5, 9), "Synthetic", (), show_in_calendar=False) == hidden
    assert payload_fingerprint(date(2026, 5, 9), "Synthetic", (), show_in_calendar=True) != hidden


def test_create_receipt_matches_the_hidden_fingerprint(user):
    key = uuid4()
    result = create_entry(
        author=user,
        date=date(2026, 5, 9),
        body="Synthetic observation",
        uploads=(),
        submission_key=key,
    )
    from diary.models import DiarySubmission

    receipt = DiarySubmission.objects.get(key=key)
    expected = payload_fingerprint(date(2026, 5, 9), "Synthetic observation", ())
    assert receipt.payload_sha256 == expected
    assert not result.entry.show_in_calendar


def test_month_query_count_is_fixed_for_a_full_month(user, garden, django_assert_num_queries):
    make_event(user, garden, planned=date(2026, 5, 10))
    make_event(user, garden, actual=date(2026, 5, 12))
    for day in (3, 9, 20):
        tag_entry(
            user,
            make_entry(user, day=date(2026, 5, day), show_in_calendar=True),
            garden.planting,
        )
    with django_assert_num_queries(3):
        items = month_items(2026, 5)
    assert len(items) == 5


def test_a_recorded_link_wins_over_an_inferred_pair(user, garden):
    older = make_entry(user, day=date(2026, 5, 9), show_in_calendar=True, title="Older note")
    older = tag_entry(user, older, garden.planting)
    newer = make_entry(
        user,
        day=date(2026, 5, 9),
        show_in_calendar=True,
        title="Newer note",
        action=PlantingEventAction(planting=garden.planting.pk, kind="sowing"),
    )
    items = month_items(2026, 5)
    assert sorted((item.kind, (item.entry or item.event).pk) for item in items) == [
        ("combined", newer.pk),
        ("entry", older.pk),
    ]


def test_a_claimed_event_does_not_make_a_pair_unambiguous(user, garden):
    linked = make_entry(
        user,
        day=date(2026, 5, 9),
        show_in_calendar=True,
        action=PlantingEventAction(planting=garden.planting.pk, kind="sowing"),
    )
    second = make_event(user, garden, kind="other", actual=date(2026, 5, 9))
    tagged = tag_entry(
        user, make_entry(user, day=date(2026, 5, 9), show_in_calendar=True), garden.planting
    )
    items = month_items(2026, 5)
    assert sorted(item.kind for item in items) == ["combined", "entry", "event"]
    assert sorted(value.pk for value in items if value.kind == "entry") == [tagged.pk]
    assert [value.event for value in items if value.kind == "event"] == [second]
    assert [value.entry for value in items if value.kind == "combined"] == [linked]


def test_two_planting_links_with_one_event_do_not_pair(user, garden):
    other_crop = create_crop(actor=user, name="Synthetic bean").record
    other = create_planting(actor=user, crop=other_crop, start_year=2026).record
    make_event(user, garden, actual=date(2026, 5, 9))
    tag_entry(
        user,
        make_entry(user, day=date(2026, 5, 9), show_in_calendar=True),
        garden.planting,
        other,
    )
    assert sorted(item.kind for item in month_items(2026, 5)) == ["entry", "event"]


def test_an_address_rejects_non_ascii_digits():
    assert parse_item("entry-²") is None
    assert parse_item("event-+1") is None
    assert parse_item("event-1_0") is None
    assert parse_item("entry-0") is None
    assert parse_item("entry-7") == ("entry", 7)
    assert parse_day("²", 2026, 5) is None
    assert parse_day("+9", 2026, 5) is None
    assert parse_day("0", 2026, 5) is None
    assert parse_day("9", 2026, 5) == date(2026, 5, 9)
