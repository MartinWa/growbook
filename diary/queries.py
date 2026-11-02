"""Diary list filtering with derived, deduplicated relationships."""

from django.db.models import Q

from garden.models import GrowingArea
from garden.queries import descendant_ids

from .models import DiaryEntry
from .search import normalize_search


def filter_entries(*, q=None, year=None, area=None, crop=None, variety=None, planting=None):
    entries = DiaryEntry.objects.select_related("author")
    if q:
        entries = entries.filter(search_text__contains=normalize_search(q))
    if year is not None:
        entries = entries.filter(date__year=year)
    if area is not None:
        area_ids = descendant_ids(list(GrowingArea.objects.all()), area.pk)
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


def observations_for(kind, record, limit=5):
    queryset = filter_entries(**{kind: record})
    return queryset.order_by("-date", "-created_at", "-id")[:limit]


def observation_count(kind, record):
    return filter_entries(**{kind: record}).count()


def diary_years():
    return [value.year for value in DiaryEntry.objects.dates("date", "year", order="DESC")]
