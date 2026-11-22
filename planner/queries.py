"""Month reads with one placement rule for every calendar source."""

import calendar
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from core.weeks import first_weekday
from diary.models import DiaryEntry
from garden.models import GrowingArea, PlantingEvent
from garden.queries import descendant_ids

FIRST_YEAR = 2000
EXCERPT_LENGTH = 80


def year_range(today=None):
    today = today or timezone.localdate()
    return FIRST_YEAR, today.year + 1


def year_is_valid(year, today=None):
    first, last = year_range(today)
    return first <= year <= last


def month_is_valid(month):
    return type(month) is int and 1 <= month <= 12


def month_bounds(year, month):
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def entry_label(entry):
    """Title, then the first note line, then the localized date for a photo-only entry."""
    if entry.title:
        return entry.title
    lines = entry.body.strip().splitlines()
    excerpt = lines[0].strip() if lines else ""
    if not excerpt:
        return date_format(entry.date, "DATE_FORMAT")
    if len(excerpt) > EXCERPT_LENGTH:
        return excerpt[: EXCERPT_LENGTH - 1].rstrip() + "…"
    return excerpt


@dataclass(frozen=True)
class CalendarItem:
    """One rendered label: an event, an entry, or the pair a save created together."""

    day: date
    event: PlantingEvent | None = None
    entry: DiaryEntry | None = None

    @property
    def pk(self):
        return self.event.pk if self.event is not None else self.entry.pk

    @property
    def kind(self):
        if self.event is not None and self.entry is not None:
            return "combined"
        return "event" if self.event is not None else "entry"

    @property
    def planned(self):
        return self.event is not None and self.event.actual_date is None

    @property
    def state_label(self):
        if self.event is None:
            return ""
        return _("Planned") if self.planned else _("Done")

    @property
    def title(self):
        if self.entry is not None:
            return entry_label(self.entry)
        return str(self.event.get_kind_display())

    @property
    def context(self):
        if self.event is None:
            return ""
        planting = self.event.planting
        parts = [planting.label or str(planting.crop)]
        if planting.area_id:
            parts.append(planting.area.name)
        return " · ".join(parts)

    @property
    def meta(self):
        """Type and state as text, never colour alone."""
        type_label = ""
        context = self.context
        if self.entry is not None:
            type_label = _("Diary")
            if self.event is not None:
                context = f"{self.event.get_kind_display()} · {context}"
        parts = [self.state_label, type_label, context]
        return " · ".join(part for part in parts if part)

    @property
    def entry_url(self):
        return self.entry.get_absolute_url() if self.entry is not None else ""

    @property
    def event_url(self):
        return self.event.get_absolute_url() if self.event is not None else ""

    @property
    def primary_url(self):
        return self.entry_url or self.event_url

    @property
    def sort_key(self):
        kinds = {"combined": 0, "event": 1, "entry": 2}
        return (self.day, not self.planned, kinds[self.kind], self.title.casefold(), self.pk)


@dataclass(frozen=True)
class CalendarDay:
    day: date
    in_month: bool
    is_today: bool
    items: list = field(default_factory=list)


def _area_ids(area):
    return descendant_ids(list(GrowingArea.objects.all()), area.pk)


def event_queryset(*, area=None, crop=None, variety=None, planting=None):
    """Planting events filtered the way their planting is recorded."""
    events = PlantingEvent.objects.select_related(
        "planting__crop", "planting__variety", "planting__area", "planting__cycle"
    )
    if area is not None:
        events = events.filter(planting__area_id__in=_area_ids(area))
    if crop is not None:
        events = events.filter(planting__crop_id=crop.pk)
    if variety is not None:
        events = events.filter(planting__variety_id=variety.pk)
    if planting is not None:
        events = events.filter(planting_id=planting.pk)
    return events


def entry_queryset(*, area=None, crop=None, variety=None, planting=None):
    """Entries filtered through their links, including child areas and linked plantings."""
    entries = DiaryEntry.objects.select_related("event")
    if area is not None:
        area_ids = _area_ids(area)
        entries = entries.filter(
            Q(links__area_id__in=area_ids) | Q(links__planting__area_id__in=area_ids)
        )
    if crop is not None:
        entries = entries.filter(
            Q(links__crop_id=crop.pk)
            | Q(links__variety__crop_id=crop.pk)
            | Q(links__planting__crop_id=crop.pk)
        )
    if variety is not None:
        entries = entries.filter(
            Q(links__variety_id=variety.pk) | Q(links__planting__variety_id=variety.pk)
        )
    if planting is not None:
        entries = entries.filter(links__planting_id=planting.pk)
    return entries.distinct()


def month_items(year, month, *, area=None, crop=None, variety=None, planting=None):
    """Every label for one month, with an entry and its event rendered once."""
    start, end = month_bounds(year, month)
    filters = {"area": area, "crop": crop, "variety": variety, "planting": planting}
    events = event_queryset(**filters).filter(
        Q(actual_date__range=(start, end))
        | Q(actual_date__isnull=True, planned_date__range=(start, end))
    )
    items = {}
    for event in events:
        items[("event", event.pk)] = CalendarItem(
            day=event.actual_date or event.planned_date, event=event
        )
    entries = entry_queryset(**filters).filter(show_in_calendar=True, date__range=(start, end))
    for entry in entries:
        key = ("event", entry.event_id)
        if entry.event_id is not None and key in items:
            current = items[key]
            items[key] = CalendarItem(day=current.day, event=current.event, entry=entry)
        else:
            items[("entry", entry.pk)] = CalendarItem(day=entry.date, entry=entry)
    return sorted(items.values(), key=lambda item: item.sort_key)


def previous_year_items(year, month):
    """The same month one year earlier, bounded like month navigation."""
    previous = year - 1
    if previous < FIRST_YEAR:
        return []
    return month_items(previous, month)


def _shift(year, month, delta):
    index = year * 12 + (month - 1) + delta
    shifted_year, shifted_month = divmod(index, 12)
    return shifted_year, shifted_month + 1


def _navigation(year, month, today=None):
    first_year, last_year = year_range(today)
    if not first_year <= year <= last_year:
        return None
    return {
        "year": year,
        "month": month,
        "label": date_format(date(year, month, 1), "F Y"),
        "url": reverse("planner:month", args=[year, month]),
    }


def weekday_names():
    base = date(2024, 1, 1) + timedelta(days=first_weekday())
    return [date_format(base + timedelta(days=offset), "D") for offset in range(7)]


def month_context(year, month, *, today=None):
    today = today or timezone.localdate()
    start, _ = month_bounds(year, month)
    items = month_items(year, month)
    by_day = defaultdict(list)
    for item in items:
        by_day[item.day].append(item)
    weeks = [
        [
            CalendarDay(
                day=day,
                in_month=day.year == year and day.month == month,
                is_today=day == today,
                items=by_day.get(day, []),
            )
            for day in week
        ]
        for week in calendar.Calendar(first_weekday()).monthdatescalendar(year, month)
    ]
    previous = _shift(year, month, -1)
    following = _shift(year, month, 1)
    return {
        "year": year,
        "month": month,
        "month_label": date_format(start, "F Y"),
        "weekdays": weekday_names(),
        "weeks": weeks,
        "items": items,
        "has_items": bool(items),
        "previous": _navigation(*previous, today=today),
        "following": _navigation(*following, today=today),
    }
