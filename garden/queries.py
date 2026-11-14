"""Small current-area tree reads and explicit planting/year history queries."""

from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import F, Q, prefetch_related_objects
from django.utils.translation import gettext as _

from .models import GrowingArea, Planting, PlantingCycle


@dataclass
class AreaNode:
    area: GrowingArea
    children: list = field(default_factory=list)
    records: list = field(default_factory=list)
    count: int = 0
    pairs: set = field(default_factory=set)
    open: bool = False
    branch: bool = False
    structural: bool = False


def area_tree(*, areas=None, visible_ids=None, plantings=None):
    """Read a forest once, retain ancestors and attach batches to their exact area.

    Iterative traversal also renders deep trees without recursive template includes.
    Invalid legacy cycles are cut deterministically; every included area appears once.
    """
    if areas is None:
        areas = list(GrowingArea.objects.all())
    areas = sorted(areas, key=lambda area: (area.name_key, area.pk))
    by_id = {area.pk: area for area in areas}
    if plantings is not None:
        plantings = list(plantings)
        visible_ids = {record.area_id for record in plantings if record.area_id is not None}
    visible = set(by_id) if visible_ids is None else set(visible_ids)
    included = set()
    for pk in visible:
        while pk in by_id and pk not in included:
            included.add(pk)
            pk = by_id[pk].parent_id
    nodes = {
        area.pk: AreaNode(area=area, structural=area.pk not in visible)
        for area in areas
        if area.pk in included
    }
    children = {}
    for node in nodes.values():
        children.setdefault(node.area.parent_id, []).append(node)
    roots, ordered, seen = [], [], set()
    candidates = [node for node in nodes.values() if node.area.parent_id not in nodes]
    # Remaining candidates recover cyclic components, in normalized name order.
    for candidate in [*candidates, *nodes.values()]:
        if candidate.area.pk in seen:
            continue
        roots.append(candidate)
        pending = [(candidate, None)]
        while pending:
            node, parent = pending.pop()
            if node.area.pk in seen:
                continue
            seen.add(node.area.pk)
            ordered.append(node)
            if parent is not None:
                parent.children.append(node)
            pending.extend((child, node) for child in reversed(children.get(node.area.pk, [])))
    unassigned = []
    for record in plantings or []:
        if record.area_id is None:
            unassigned.append(record)
        else:
            nodes[record.area_id].records.append(record)
    for node in reversed(ordered):
        node.count = len(node.records) + sum(child.count for child in node.children)
        node.pairs = {(record.crop_id, record.variety_id) for record in node.records}
        for child in node.children:
            node.pairs.update(child.pairs)
        node.branch = bool(node.children) or plantings is not None
        node.open = plantings is not None and len(node.pairs) == 1
    events = []
    pending = [(node, False) for node in reversed(roots)]
    while pending:
        node, closing = pending.pop()
        events.append({"node": node, "closing": closing})
        if not closing:
            pending.append((node, True))
            pending.extend((child, False) for child in reversed(node.children))
    return {"roots": roots, "events": events, "unassigned": unassigned}


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
    *,
    year=None,
    crop=None,
    variety=None,
    area=None,
    area_only=False,
    unassigned=False,
    unknown_variety=False,
):
    records = Planting.objects.select_related("crop", "variety", "area", "cycle")
    if year is not None:
        records = plantings_in_year(year, records)
    if crop is not None:
        records = records.filter(crop_id=getattr(crop, "pk", crop))
    if variety is not None:
        records = records.filter(variety_id=getattr(variety, "pk", variety))
    if area is not None:
        area_id = getattr(area, "pk", area)
        if area_only:
            records = records.filter(area_id=area_id)
        else:
            areas = list(GrowingArea.objects.all())
            records = records.filter(area_id__in=descendant_ids(areas, area_id))
    if unassigned:
        records = records.filter(area__isnull=True)
    if unknown_variety:
        records = records.filter(variety__isnull=True)
    # No event/photo join: each planting/batch is returned exactly once.
    return records


def planting_rows(queryset, *, areas=None):
    if areas is None:
        areas = list(GrowingArea.objects.all())
    paths = area_paths(areas)
    records = list(queryset)
    for record in records:
        record.area_path = paths.get(record.area_id, "")
        record.display_label = record.label or _("Planting %(number)s") % {"number": record.pk}
    return records


def planting_groups(records):
    """Group only the matching records; filters never bring in unrelated members."""
    groups = {}
    for record in records:
        group = groups.setdefault(
            record.batch_key,
            {"key": record.batch_key, "crop": record.crop, "records": []},
        )
        group["records"].append(record)
    for group in groups.values():
        group["thumbnail"] = next(
            (photo for member in group["records"] for photo in member.photos.all()), None
        )
    return list(groups.values())


def cycle_years(records):
    years = []
    for record in records:
        if record.lifespan == Planting.Lifespan.ANNUAL:
            label = str(record.start_year)
        elif record.end_year is None:
            label = _("%(year)s onwards") % {"year": record.start_year}
        else:
            label = f"{record.start_year}–{record.end_year}"
        if label not in years:
            years.append(label)
    return "; ".join(years)


def variety_evaluations(variety):
    """Current completed results, once per member, including archived associations."""
    records = planting_rows(
        Planting.objects.filter(variety=variety, cycle__status=PlantingCycle.Status.COMPLETED)
        .exclude(evaluation="")
        .select_related("cycle", "crop", "variety", "area")
        .order_by(F("cycle__completed_on").desc(nulls_last=True), "-start_year", "-id")
    )
    results = []
    for record in records:
        if record.evaluation.strip():
            record.growing_years = cycle_years([record])
            results.append(record)
    return results


def cycle_groups(records):
    records = list(records)
    prefetch_related_objects(records, "photos", "cycle__categories", "cycle__members")
    groups = planting_groups(records)
    for group in groups:
        first = group["records"][0]
        group["cycle"] = first.cycle
        initial = min(first.cycle.members.all(), key=lambda member: member.pk, default=first)
        group["label"] = initial.label or _("Planting %(number)s") % {"number": initial.pk}
        group["years"] = cycle_years(first.cycle.members.all())
    return groups


def planting_tree(**filters):
    """Keep batches and their tree together when an area disappears between reads."""
    records = list(planting_history(**filters))
    prefetch_related_objects(records, "photos")
    areas = list(GrowingArea.objects.all())
    area_ids = {area.pk for area in areas}
    if any(record.area_id is not None and record.area_id not in area_ids for record in records):
        # A planting can move away and its former area can be deleted between reads.
        # The fallback transaction keeps the refreshed records and areas consistent.
        with transaction.atomic():
            records = list(planting_history(**filters))
            prefetch_related_objects(records, "photos")
            areas = list(GrowingArea.objects.all())
    records = planting_rows(records, areas=areas)
    tree = area_tree(areas=areas, plantings=records)
    for event in tree["events"]:
        event["node"].groups = planting_groups(event["node"].records)
    return {"records": records, **tree}
