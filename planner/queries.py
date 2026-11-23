"""Month reads with one placement rule for every calendar source."""

import calendar
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.db.models import Prefetch, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from core.weeks import first_weekday
from diary.models import DiaryEntry, DiaryLink
from garden.models import GrowingArea, PlantingEvent
from garden.queries import descendant_ids

FIRST_YEAR = 2000
EXCERPT_LENGTH = 80
# Only planting links can pair an entry with an event; prefetch nothing else.
ENTRY_LINKS = Prefetch("links", queryset=DiaryLink.objects.filter(planting__isnull=False))


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


ITEM_SEPARATOR = "-"
ITEM_KINDS = ("event", "entry")
CELL_LIMIT = 3


def _positive_int(value):
    """Parse ASCII digits only; ``int()`` alone accepts signs, spaces and underscores."""
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        return None
    number = int(value)
    return number if number > 0 else None


def parse_item(value):
    """Return ``(kind, pk)`` for an ``item=`` address, or None when it is not one."""
    if not isinstance(value, str):
        return None
    kind, separator, number = value.partition(ITEM_SEPARATOR)
    if not separator or kind not in ITEM_KINDS:
        return None
    pk = _positive_int(number)
    return (kind, pk) if pk is not None else None


def parse_day(value, year, month):
    """Return the day of the month named by ``day=``, or None when it is not one."""
    number = _positive_int(value)
    if number is None or number > calendar.monthrange(year, month)[1]:
        return None
    return date(year, month, number)


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
    def item_param(self):
        """The ``?item=`` address of this label; a combined item is led by its entry."""
        if self.entry is not None:
            return f"entry{ITEM_SEPARATOR}{self.entry.pk}"
        return f"event{ITEM_SEPARATOR}{self.event.pk}"

    @property
    def element_id(self):
        return f"calendar-item-{self.item_param}"

    @property
    def entry_note(self):
        return self.entry.body.strip() if self.entry is not None else ""

    @property
    def event_note(self):
        return self.event.note.strip() if self.event is not None else ""

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


def _pair(items, event_item, entry, show_events):
    """One label for one save: the entry leads and the event keeps its link."""
    event = event_item.event
    items.pop(("event", event.pk), None)
    if show_events:
        items[("event", event.pk)] = CalendarItem(day=event_item.day, event=event, entry=entry)
    else:
        items[("entry", entry.pk)] = CalendarItem(day=event_item.day, entry=entry)


def _inferred_event(entry, by_planting, claimed):
    """The one event an unlinked entry belongs with, or None when that is a guess.

    Entries written before the link existed, and events recorded on the plantings
    page, are paired read-only. Between two plantings and between two events on the
    same day the pairing would be a guess, so those records stay separate.
    """
    planting_ids = {link.planting_id for link in entry.links.all() if link.planting_id is not None}
    if len(planting_ids) != 1:
        return None
    candidates = by_planting.get((planting_ids.pop(), entry.date), ())
    if len(candidates) != 1:
        return None
    event = candidates[0]
    return None if event.pk in claimed else event


def month_items(year, month, *, area=None, crop=None, variety=None, planting=None, kinds=None):
    """Every label for one month, with an entry and its event rendered once."""
    start, end = month_bounds(year, month)
    filters = {"area": area, "crop": crop, "variety": variety, "planting": planting}
    show_events = kinds is None or "event" in kinds
    show_entries = kinds is None or "entry" in kinds
    if not (show_events or show_entries):
        return []
    events = event_queryset(**filters).filter(
        Q(actual_date__range=(start, end))
        | Q(actual_date__isnull=True, planned_date__range=(start, end))
    )
    by_event = {}
    by_planting = defaultdict(list)
    for event in events:
        day = event.actual_date or event.planned_date
        by_event[event.pk] = CalendarItem(day=day, event=event)
        by_planting[(event.planting_id, day)].append(event)
    items = {("event", pk): item for pk, item in by_event.items()} if show_events else {}
    if show_entries:
        entries = (
            entry_queryset(**filters)
            .filter(show_in_calendar=True, date__range=(start, end))
            .prefetch_related(ENTRY_LINKS)
            .order_by("date", "created_at", "pk")
        )
        claimed = set()
        inferred = []
        # Recorded links first, so an inference can never take an event from its own save.
        for entry in entries:
            if entry.event_id is None:
                inferred.append(entry)
                continue
            claimed.add(entry.event_id)
            item = by_event.get(entry.event_id)
            if item is None:
                # The event is outside the month or filtered out: the entry keeps its date.
                items[("entry", entry.pk)] = CalendarItem(day=entry.date, entry=entry)
            else:
                _pair(items, item, entry, show_events)
        for entry in inferred:
            event = _inferred_event(entry, by_planting, claimed)
            if event is None:
                items[("entry", entry.pk)] = CalendarItem(day=entry.date, entry=entry)
                continue
            claimed.add(event.pk)
            _pair(items, by_event[event.pk], entry, show_events)
    return sorted(items.values(), key=lambda item: item.sort_key)


def find_item(items, kind, pk):
    """The month label an ``item=`` address names within one set of labels."""
    for item in items:
        if kind == "entry" and item.entry is not None and item.entry.pk == pk:
            return item
        if kind == "event" and item.event is not None and item.event.pk == pk:
            return item
    return None


def previous_year_items(year, month, **filters):
    """The same month one year earlier, bounded like month navigation."""
    previous = year - 1
    if previous < FIRST_YEAR:
        return []
    return month_items(previous, month, **filters)


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


def _panel(year, month, items, address=None, day=None):
    """One opened item outranks one opened day, and both are rendered server-side.

    The shown month is resolved first, so a panel agrees with the label that was
    selected. A shared link whose item the filters hide still opens, from the
    unfiltered month, rather than appearing to show nothing.
    """
    if address is not None:
        item = find_item(items, *address)
        if item is None:
            item = find_item(month_items(year, month), *address)
        if item is None:
            return None
        return {
            "kind": "item",
            "item": item,
            "date_value": item.day,
            "date_label": date_format(item.day, "l j F Y"),
            "return_focus": item.element_id,
        }
    if day is not None:
        return {
            "kind": "day",
            "day": day,
            "date_value": day,
            "items": [value for value in items if value.day == day],
            "date_label": date_format(day, "l j F Y"),
            "return_focus": f"calendar-more-{day.day}",
        }
    return None


def month_context(year, month, *, today=None, filters=None, address=None, day=None):
    today = today or timezone.localdate()
    filters = filters or {}
    start, _ = month_bounds(year, month)
    items = month_items(year, month, **filters)
    by_day = defaultdict(list)
    for value in items:
        by_day[value.day].append(value)
    weeks = [
        [
            CalendarDay(
                day=date_value,
                in_month=date_value.year == year and date_value.month == month,
                is_today=date_value == today,
                items=by_day.get(date_value, []),
            )
            for date_value in week
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
        "cell_limit": CELL_LIMIT,
        "previous": _navigation(*previous, today=today),
        "following": _navigation(*following, today=today),
        "previous_year_items": previous_year_items(year, month, **filters),
        "previous_year_label": date_format(date(year - 1, month, 1), "F Y")
        if year - 1 >= FIRST_YEAR
        else "",
        "panel": _panel(year, month, items, address, day),
    }
