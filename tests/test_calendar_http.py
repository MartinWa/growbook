"""Calendar HTTP behavior: permissions, localization, the year range and the layout."""

import re
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.urls import reverse
from django.utils import timezone

from diary.actions import PlantingEventAction
from diary.services import create_entry
from garden.services import (
    create_area,
    create_crop,
    create_planting,
    create_planting_event,
)
from planner.queries import year_range

pytestmark = pytest.mark.django_db(transaction=True)

WEEKDAYS_SV = ["mån", "tis", "ons", "tors", "fre", "lör", "sön"]
WEEKDAYS_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@pytest.fixture
def garden(user):
    crop = create_crop(actor=user, name="Synthetic tomato").record
    area = create_area(actor=user, name="Synthetic bed").record
    planting = create_planting(
        actor=user, crop=crop, start_year=2026, area=area, label="Synthetic batch"
    ).record
    return SimpleNamespace(crop=crop, area=area, planting=planting)


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
    show_in_calendar=True,
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


def month_url(year=2026, month=5):
    return reverse("planner:month", args=[year, month])


def heading_names(response):
    return re.findall(r'<th scope="col">([^<]+)</th>', response.text)


def test_calendar_redirects_to_the_current_month(client, user):
    client.force_login(user)
    today = timezone.localdate()
    response = client.get(reverse("planner:current"))
    assert response.status_code == 302
    assert response.url == month_url(today.year, today.month)


def test_month_uses_localized_month_and_weekday_names(client, user, garden):
    make_event(user, garden, planned=date(2026, 5, 10))
    client.force_login(user)
    swedish = client.get(month_url())
    assert swedish.status_code == 200
    assert 'data-result-focus tabindex="-1">maj 2026</caption>' in swedish.text
    assert heading_names(swedish) == WEEKDAYS_SV
    assert "10 maj 2026" in swedish.text

    client.cookies["django_language"] = "en"
    english = client.get(month_url())
    assert 'tabindex="-1">May 2026</caption>' in english.text
    assert heading_names(english) == WEEKDAYS_EN
    assert "May 10, 2026" in english.text


def test_weeks_start_on_monday(client, user):
    client.force_login(user)
    response = client.get(month_url())
    assert response.status_code == 200
    assert heading_names(response)[0] == "mån"


def test_month_page_includes_the_leap_day_and_its_items(client, user, garden):
    record = make_event(user, garden, planned=date(2024, 2, 29), kind="harvest_start")
    client.force_login(user)
    response = client.get(month_url(2024, 2))
    assert response.status_code == 200
    assert 'data-date="2024-02-29"' in response.text
    assert f"item=event-{record.pk}" in response.text
    assert 'tabindex="-1">februari 2024</caption>' in response.text
    panel = client.get(f"{month_url(2024, 2)}?item=event-{record.pk}")
    assert f'href="{record.get_absolute_url()}"' in panel.text


def test_today_is_marked_with_aria_current(client, user):
    client.force_login(user)
    today = timezone.localdate()
    response = client.get(reverse("planner:month", args=[today.year, today.month]))
    assert response.status_code == 200
    cell = re.search(r'<td class="calendar-day is-today"[^>]*data-date="([^"]+)"', response.text)
    assert cell is not None
    assert cell.group(1) == today.isoformat()
    assert 'aria-current="date"' in response.text


def test_year_range_and_invalid_months_are_not_found(client, user):
    client.force_login(user)
    first_year, last_year = year_range()
    assert client.get(month_url(first_year - 1, 1)).status_code == 404
    assert client.get(month_url(last_year + 1, 1)).status_code == 404
    assert client.get(month_url(2026, 0)).status_code == 404
    assert client.get(month_url(2026, 13)).status_code == 404
    assert client.get(month_url(first_year, 1)).status_code == 200
    assert client.get(month_url(last_year, 12)).status_code == 200


def test_navigation_is_absent_at_the_year_bounds(client, user):
    client.force_login(user)
    first_year, last_year = year_range()
    first = client.get(month_url(first_year, 1))
    assert 'rel="prev"' not in first.text
    assert 'rel="next"' in first.text
    last = client.get(month_url(last_year, 12))
    assert 'rel="next"' not in last.text
    assert 'rel="prev"' in last.text


def test_anonymous_and_inactive_accounts_are_redirected(client, user):
    assert client.get(month_url()).status_code == 302
    client.force_login(user)
    assert client.get(month_url()).status_code == 200
    user.is_active = False
    user.save()
    assert client.get(month_url()).status_code == 302


def test_entry_and_event_render_once_with_both_links(client, user, garden):
    entry = make_entry(
        user,
        title="Synthetic combined observation",
        action=PlantingEventAction(planting=garden.planting.pk, kind="sowing"),
    )
    client.force_login(user)
    response = client.get(month_url())
    assert response.status_code == 200
    assert response.text.count("Synthetic combined observation") == 1
    assert f"item=entry-{entry.pk}" in response.text
    panel = client.get(f"{month_url()}?item=entry-{entry.pk}")
    assert f'href="{entry.get_absolute_url()}"' in panel.text
    assert f"#event-{entry.event_id}" in panel.text
    assert "Odlingshändelse" in panel.text


def test_untitled_photo_only_entry_shows_its_localized_date(client, user, image_upload):
    make_entry(user, day=date(2026, 5, 9), body="", uploads=[image_upload()])
    client.force_login(user)
    swedish = client.get(month_url())
    assert "9 maj 2026" in swedish.text
    client.cookies["django_language"] = "en"
    english = client.get(month_url())
    assert "May 9, 2026" in english.text


def test_empty_month_says_so(client, user):
    client.force_login(user)
    swedish = client.get(month_url(2026, 1))
    assert "Inga kalenderposter den här månaden." in swedish.text
    client.cookies["django_language"] = "en"
    english = client.get(month_url(2026, 1))
    assert "No calendar items this month." in english.text


def test_main_navigation_marks_the_calendar_page(client, user):
    client.force_login(user)
    client.cookies["django_language"] = "en"
    response = client.get(month_url())
    assert 'href="/calendar/" aria-current="page">Calendar</a>' in response.text


def edit_data(entry, **overrides):
    data = {
        "submission_key": str(uuid4()),
        "expected_updated_at": entry.updated_at.isoformat(),
        "date": entry.date.isoformat(),
        "title": entry.title,
        "body": entry.body,
    }
    data.update(overrides)
    return data


def test_edit_form_exposes_and_clears_calendar_visibility(client, user):
    entry = make_entry(user, show_in_calendar=True)
    client.force_login(user)
    edit = client.get(reverse("diary:edit", args=[entry.pk]))
    assert edit.status_code == 200
    assert 'name="show_in_calendar"' in edit.text
    assert re.search(r'name="show_in_calendar"[^>]*checked', edit.text)

    response = client.post(reverse("diary:edit", args=[entry.pk]), edit_data(entry))
    assert response.status_code == 302
    entry.refresh_from_db()
    assert entry.show_in_calendar is False
    assert entry.get_absolute_url() in response.url

    response = client.post(
        reverse("diary:edit", args=[entry.pk]),
        edit_data(entry, show_in_calendar="on"),
    )
    assert response.status_code == 302
    entry.refresh_from_db()
    assert entry.show_in_calendar is True


def test_capture_opt_in_adds_the_entry_to_the_calendar(client, user):
    client.force_login(user)
    response = client.post(
        reverse("diary:capture"),
        {
            "date": "2026-05-09",
            "body": "Synthetic chore",
            "submission_key": str(uuid4()),
            "show_in_calendar": "on",
        },
    )
    assert response.status_code == 302
    assert "Synthetic chore" in client.get(month_url()).text


def test_capture_without_opt_in_stays_out_of_the_calendar(client, user):
    client.force_login(user)
    response = client.post(
        reverse("diary:capture"),
        {
            "date": "2026-05-09",
            "body": "Synthetic private note",
            "submission_key": str(uuid4()),
        },
    )
    assert response.status_code == 302
    assert "Synthetic private note" not in client.get(month_url()).text


def test_the_month_page_needs_no_per_item_queries(client, user, garden, django_assert_num_queries):
    make_event(user, garden, planned=date(2026, 5, 10))
    make_event(user, garden, actual=date(2026, 5, 12))
    for day in (3, 9, 20):
        make_entry(user, day=date(2026, 5, day), show_in_calendar=True)
    client.force_login(user)
    # Session, user, events, entries and tasks with their links, the month note
    # and one query per filter choice.
    with django_assert_num_queries(15):
        response = client.get(month_url())
    assert response.status_code == 200


def test_divergent_event_date_wins_on_the_page(client, user, garden):
    entry = make_entry(
        user,
        day=date(2026, 5, 9),
        show_in_calendar=True,
        action=PlantingEventAction(
            planting=garden.planting.pk, kind="sowing", actual_date=date(2026, 5, 12)
        ),
    )
    client.force_login(user)
    response = client.get(month_url())
    days = re.findall(r'data-date="(\d{4}-\d{2}-\d{2})"', response.text)
    assert days.count("2026-05-09") == 1
    assert days.count("2026-05-12") == 1
    cell = re.search(
        r'<td class="calendar-day[^"]*" data-date="2026-05-12">(.*?)</td>', response.text, re.S
    )
    assert f"item=entry-{entry.pk}" in cell.group(1)
    panel = client.get(f"{month_url()}?item=entry-{entry.pk}")
    assert f'href="{entry.get_absolute_url()}"' in panel.text


def item_id(kind, pk):
    return f'calendar-item-{kind}-{pk}"'


def test_filters_narrow_the_month_and_survive_navigation(client, user, garden):
    other_crop = create_crop(actor=user, name="Synthetic bean").record
    other_area = create_area(actor=user, name="Synthetic far bed").record
    other = create_planting(actor=user, crop=other_crop, start_year=2026, area=other_area).record
    inside = make_event(user, garden, planned=date(2026, 5, 10))
    outside = create_planting_event(
        actor=user, planting=other, kind="sowing", planned_date=date(2026, 5, 11)
    ).record
    client.force_login(user)
    filtered = client.get(f"{month_url()}?area={garden.area.pk}")
    assert filtered.status_code == 200
    assert item_id("event", inside.pk) in filtered.text
    assert item_id("event", outside.pk) not in filtered.text
    previous = client.get(
        f"{month_url(2026, 6)}?area={garden.area.pk}&planting={garden.planting.pk}"
    )
    assert f"/calendar/2026/5/?area={garden.area.pk}&amp;planting={garden.planting.pk}" in (
        previous.text
    )
    assert "item=" not in re.search(r'rel="prev" href="([^"]+)"', previous.text).group(1)


def test_type_toggles_hide_one_source(client, user, garden):
    event = make_event(user, garden, planned=date(2026, 5, 10))
    entry = make_entry(user, day=date(2026, 5, 9), show_in_calendar=True)
    client.force_login(user)
    events_only = client.get(f"{month_url()}?type=event")
    assert item_id("event", event.pk) in events_only.text
    assert item_id("entry", entry.pk) not in events_only.text
    entries_only = client.get(f"{month_url()}?type=entry")
    assert item_id("entry", entry.pk) in entries_only.text
    assert item_id("event", event.pk) not in entries_only.text
    combined = client.get(f"{month_url()}?type=event&type=entry")
    assert item_id("event", event.pk) in combined.text
    assert item_id("entry", entry.pk) in combined.text


def test_invalid_filters_are_not_found(client, user, garden):
    client.force_login(user)
    assert client.get(f"{month_url()}?area=999999").status_code == 404
    assert client.get(f"{month_url()}?type=bogus").status_code == 404
    assert client.get(f"{month_url()}?planting=not-a-number").status_code == 404


def test_an_opened_item_shows_its_note_and_links(client, user, garden):
    entry = make_entry(
        user,
        day=date(2026, 5, 9),
        title="Synthetic panel observation",
        body="Synthetic panel note",
        show_in_calendar=True,
    )
    client.force_login(user)
    response = client.get(f"{month_url()}?item=entry-{entry.pk}")
    assert response.status_code == 200
    assert re.search(r"<dialog[^>]*data-calendar-panel[^>]*\bopen\b", response.text)
    assert 'data-return-focus="calendar-item-entry-' + str(entry.pk) + '"' in response.text
    assert 'data-close-url="/calendar/2026/5/"' in response.text
    assert response.text.count("Synthetic panel observation") == 2
    assert "Synthetic panel note" in response.text
    assert f'href="{entry.get_absolute_url()}"' in response.text
    assert 'class="calendar-item-title"' in response.text


def test_an_opened_item_outranks_an_opened_day(client, user, garden):
    event = make_event(user, garden, planned=date(2026, 5, 10))
    client.force_login(user)
    response = client.get(f"{month_url()}?item=event-{event.pk}&day=10")
    assert response.status_code == 200
    assert "Everything on this day" not in response.text
    assert 'data-return-focus="calendar-item-event-' + str(event.pk) + '"' in response.text


@pytest.mark.parametrize(
    "month,name",
    [
        (1, "january"),
        (2, "february"),
        (3, "march"),
        (4, "april"),
        (5, "may"),
        (6, "june"),
        (7, "july"),
        (8, "august"),
        (9, "september"),
        (10, "october"),
        (11, "november"),
        (12, "december"),
    ],
)
def test_each_month_renders_its_own_decorative_vignette(client, user, month, name):
    client.force_login(user)
    page = client.get(month_url(2026, month))
    assert page.status_code == 200
    assert f"images/calendar/{name}.webp" in page.text
    assert 'class="calendar-vignette"' in page.text
    assert 'alt=""' in page.text
    assert 'aria-hidden="true"' in page.text


def test_the_day_panel_does_not_repeat_item_ids(client, user, garden):
    for number in range(3):
        make_entry(user, day=date(2026, 5, 9), title=f"Synthetic day {number}")
    client.force_login(user)
    page = client.get(month_url() + "?day=9")
    assert page.status_code == 200
    ids = re.findall(r'id="(calendar-item-[^"]+)"', page.text)
    assert len(ids) == len(set(ids)) == 3


def test_a_full_day_caps_the_cell_and_lists_every_item(client, user, garden):
    records = [
        make_event(user, garden, kind=kind, planned=date(2026, 5, 10))
        for kind in ("sowing", "transplant", "replanting", "harvest_start")
    ]
    client.force_login(user)
    client.cookies["django_language"] = "en"
    month = client.get(month_url())
    cell = re.search(r'data-date="2026-05-10">(.*?)</td>', month.text, re.S).group(1)
    assert cell.count('class="calendar-item-title"') == 3
    assert "+1 more" in cell
    day = client.get(f"{month_url()}?day=10")
    assert day.status_code == 200
    # The day list addresses the same items without repeating their element ids.
    panel_items = re.search(r'class="calendar-panel-items">(.*?)</ul>', day.text, re.S).group(1)
    for record in records:
        assert f"item=event-{record.pk}" in panel_items


def test_a_day_without_items_and_unknown_addresses_are_not_found(client, user, garden):
    entry = make_entry(user, day=date(2026, 5, 9), show_in_calendar=False)
    client.force_login(user)
    assert client.get(f"{month_url()}?day=32").status_code == 404
    assert client.get(f"{month_url(2026, 4)}?day=31").status_code == 404
    assert client.get(f"{month_url()}?item=nonsense").status_code == 404
    assert client.get(f"{month_url()}?item=event-999999").status_code == 404
    assert client.get(f"{month_url()}?item=entry-{entry.pk}").status_code == 404


def test_a_day_without_items_says_so(client, user):
    client.force_login(user)
    client.cookies["django_language"] = "en"
    response = client.get(f"{month_url()}?day=10")
    assert response.status_code == 200
    assert "Nothing on this day with the current filters." in response.text


def test_previous_year_items_link_their_records(client, user, garden):
    last_year = make_event(user, garden, planned=date(2025, 5, 10))
    client.force_login(user)
    response = client.get(month_url())
    assert response.status_code == 200
    side = re.search(r'<aside class="calendar-side".*?</aside>', response.text, re.S).group(0)
    assert f'href="{last_year.get_absolute_url()}"' in side


def test_the_htmx_request_returns_only_the_results(client, user, garden):
    make_event(user, garden, planned=date(2026, 5, 10))
    client.force_login(user)
    partial = client.get(
        month_url(), headers={"HX-Request": "true", "HX-Target": "calendar-results"}
    )
    assert partial.status_code == 200
    assert "<html" not in partial.text
    assert 'id="calendar-results"' in partial.text
    assert 'class="calendar-grid"' in partial.text
    other = client.get(month_url(), headers={"HX-Request": "true", "HX-Target": "somewhere"})
    assert "<html" in other.text


def test_filters_plus_an_item_still_render_the_panel(client, user, garden):
    entry = make_entry(
        user, day=date(2026, 5, 9), body="Synthetic filtered note", show_in_calendar=True
    )
    client.force_login(user)
    response = client.get(f"{month_url()}?crop={garden.crop.pk}&item=entry-{entry.pk}")
    assert response.status_code == 200
    assert "Synthetic filtered note" in response.text
    cell = re.search(r'data-date="2026-05-09">(.*?)</td>', response.text, re.S).group(1)
    assert "calendar-item" not in cell


def test_the_current_month_redirect_keeps_the_filters(client, user):
    client.force_login(user)
    today = timezone.localdate()
    response = client.get(f"{reverse('planner:current')}?type=entry&item=entry-3&day=9")
    assert response.status_code == 302
    assert response.url == (
        f"{reverse('planner:month', args=[today.year, today.month])}?type=entry"
    )


def test_an_empty_filtered_month_says_so(client, user, garden):
    make_event(user, garden, planned=date(2026, 5, 10))
    client.force_login(user)
    client.cookies["django_language"] = "en"
    response = client.get(f"{month_url()}?type=entry")
    assert response.status_code == 200
    assert "No calendar items match these filters." in response.text


def test_the_panel_prefers_the_label_the_filters_show(client, user, garden):
    outside = create_area(actor=user, name="Synthetic drying shed").record
    entry = create_entry(
        author=user,
        date=date(2026, 5, 9),
        title="Synthetic shed note",
        body="Synthetic shed detail",
        uploads=(),
        submission_key=uuid4(),
        show_in_calendar=True,
        links=[("area", outside.pk)],
        action=PlantingEventAction(
            planting=garden.planting.pk, kind="sowing", actual_date=date(2026, 5, 12)
        ),
    ).entry
    client.force_login(user)
    filtered = client.get(f"{month_url()}?area={outside.pk}")
    cell = re.search(r'data-date="2026-05-09">(.*?)</td>', filtered.text, re.S).group(1)
    assert f"item=entry-{entry.pk}" in cell
    panel = client.get(f"{month_url()}?area={outside.pk}&item=entry-{entry.pk}")
    assert panel.status_code == 200
    assert 'data-date="2026-05-09"' in panel.text.split("<dialog")[1]
    assert "Open planting event" not in panel.text
    assert f"area={outside.pk}" in panel.text


def test_blank_selections_are_not_filters(client, user):
    client.force_login(user)
    client.cookies["django_language"] = "en"
    response = client.get(f"{month_url()}?area=&type=&crop=&variety=&planting=")
    assert response.status_code == 200
    assert 'class="filter-chip"' not in response.text
    assert "No calendar items this month." in response.text


def test_malformed_addresses_are_not_found(client, user):
    client.force_login(user)
    huge = "9" * 5000
    for query in (
        "day=%C2%B2",
        "item=entry-%C2%B2",
        "item=event-1_0",
        "day=00",
        f"day={huge}",
        f"item=entry-{huge}",
    ):
        assert client.get(f"{month_url()}?{query}").status_code == 404


def test_the_overflow_label_is_translated(client, user, garden):
    for kind in ("sowing", "transplant", "replanting", "harvest_start"):
        make_event(user, garden, kind=kind, planned=date(2026, 5, 10))
    client.force_login(user)
    assert "+1 till" in client.get(month_url()).text
    client.cookies["django_language"] = "en"
    assert "+1 more" in client.get(month_url()).text
