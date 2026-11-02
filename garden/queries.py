"""Small current-area tree reads and explicit planting/year history queries."""

from django.db.models import Q
from django.utils.translation import gettext as _

from .models import GrowingArea, Planting


def area_paths(areas):
    by_id = {area.pk: area for area in areas}
    paths = {}
    for area in areas:
        names, seen = [], set()
        current = area
        while current is not None and current.pk not in seen:
            seen.add(current.pk)
            names.append(current.name)
            current = by_id.get(current.parent_id)
        paths[area.pk] = " / ".join(reversed(names))
    return paths


def descendant_ids(areas, pk):
    children = {}
    for area in areas:
        children.setdefault(area.parent_id, []).append(area.pk)
    found, pending = {pk}, [pk]
    while pending:
        for child in children.get(pending.pop(), []):
            if child not in found:
                found.add(child)
                pending.append(child)
    return found


def plantings_in_year(year, queryset=None):
    if type(year) is not int or not 1 <= year <= 9999:
        raise ValueError("A growing year must be an integer from 1 to 9999.")
    if queryset is None:
        queryset = Planting.objects.all()
    return queryset.filter(
        Q(lifespan=Planting.Lifespan.ANNUAL, start_year=year)
        | (
            Q(lifespan=Planting.Lifespan.PERSISTENT, start_year__lte=year)
            & (Q(end_year__isnull=True) | Q(end_year__gte=year))
        )
    )


def planting_history(
    *, year=None, crop=None, variety=None, area=None, unassigned=False, unknown_variety=False
):
    records = Planting.objects.select_related("crop", "variety", "area")
    if year is not None:
        records = plantings_in_year(year, records)
    if crop is not None:
        records = records.filter(crop_id=getattr(crop, "pk", crop))
    if variety is not None:
        records = records.filter(variety_id=getattr(variety, "pk", variety))
    if area is not None:
        areas = list(GrowingArea.objects.all())
        records = records.filter(area_id__in=descendant_ids(areas, getattr(area, "pk", area)))
    if unassigned:
        records = records.filter(area__isnull=True)
    if unknown_variety:
        records = records.filter(variety__isnull=True)
    # No event/photo join: each planting/batch is returned exactly once.
    return records


def planting_rows(queryset):
    paths = area_paths(list(GrowingArea.objects.all()))
    records = list(queryset)
    for record in records:
        record.area_path = paths.get(record.area_id, "")
        record.display_label = record.label or _("Batch %(number)s") % {"number": record.pk}
    return records
