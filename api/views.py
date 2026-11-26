"""Read-only version 1 API views over the garden registers, plantings and diary.

Every view answers GET, HEAD and OPTIONS only. Reads are unguarded in the domain
layer, and nothing here writes: a write would need an explicit validated operation.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from django.db.models import Q
from django.http import HttpRequest
from django.urls import reverse
from rest_framework import generics
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import APIView

from diary.models import DiaryEntry
from diary.queries import filter_entries
from garden.models import (
    Crop,
    GrowingArea,
    Planting,
    PlantingCycle,
    PlantingEvent,
    Variety,
)
from garden.names import normalize_name
from garden.queries import descendant_ids, planting_history

from .serializers import (
    AreaSerializer,
    CropSerializer,
    EvaluationSerializer,
    EventSerializer,
    ObservationSerializer,
    PlantingSerializer,
    VarietySerializer,
)


class InvalidParameter(APIException):
    """An unusable query value, reported with the parameter that was refused."""

    status_code = 400

    def __init__(self, parameter, message):
        super().__init__({"detail": message, "parameter": parameter})


@dataclass(frozen=True)
class Parameter:
    """One supported query parameter, its reader and its documented type."""

    name: str
    parse: Callable[[str, str], object]
    schema: dict
    description: str


def _integer(parameter, raw, *, minimum, maximum):
    try:
        value = int(str(raw).strip())
    except ValueError:
        message = f'"{raw}" is not a whole number for {parameter}.'
        raise InvalidParameter(parameter, message) from None
    if not minimum <= value <= maximum:
        raise InvalidParameter(
            parameter, f"{parameter} must be a whole number from {minimum} to {maximum}."
        )
    return value


def parse_identifier(parameter, raw):
    return _integer(parameter, raw, minimum=1, maximum=2**63 - 1)


def parse_year(parameter, raw):
    return _integer(parameter, raw, minimum=1, maximum=9999)


def parse_flag(parameter, raw):
    value = str(raw).strip().lower()
    if value in {"true", "false"}:
        return value == "true"
    raise InvalidParameter(parameter, f'Use true or false for {parameter}, not "{raw}".')


def parse_text(parameter, raw):
    """Trimmed text. A blank value filters nothing, like an empty form field."""
    return str(raw).strip() or None


def parse_date(parameter, raw):
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        message = f'Use a date like 2026-05-01 for {parameter}, not "{raw}".'
        raise InvalidParameter(parameter, message) from None


def parse_uuid(parameter, raw):
    try:
        return UUID(str(raw).strip())
    except ValueError:
        message = f'"{raw}" is not a valid {parameter} identifier.'
        raise InvalidParameter(parameter, message) from None


def parse_choice(allowed):
    def parse(parameter, raw):
        value = str(raw).strip()
        if value in allowed:
            return value
        raise InvalidParameter(
            parameter, f'Use one of {", ".join(allowed)} for {parameter}, not "{raw}".'
        )

    return parse


def parse_parent(parameter, raw):
    if str(raw).strip().lower() == "none":
        return None
    return parse_identifier(parameter, raw)


def name_search_key(values):
    """The normalized key for a name search, or None when nothing was typed."""
    text = values.get("search")
    return normalize_name(text)[1] if text else None


ID_SCHEMA = {"type": "integer", "minimum": 1}
YEAR_SCHEMA = {"type": "integer", "minimum": 1, "maximum": 9999}
FLAG_SCHEMA = {"type": "boolean"}
DATE_SCHEMA = {"type": "string", "format": "date"}
UUID_SCHEMA = {"type": "string", "format": "uuid"}
TEXT_SCHEMA = {"type": "string"}

ARCHIVED = Parameter(
    "archived",
    parse_flag,
    FLAG_SCHEMA,
    "Only archived records (true) or only current records (false).",
)
CROP = Parameter("crop", parse_identifier, ID_SCHEMA, "Only records of this crop.")
VARIETY = Parameter("variety", parse_identifier, ID_SCHEMA, "Only records of this variety.")
AREA = Parameter("area", parse_identifier, ID_SCHEMA, "This growing area and its descendants.")
YEAR = Parameter("year", parse_year, YEAR_SCHEMA, "Only records that belong to this growing year.")
CYCLE = Parameter("cycle", parse_uuid, UUID_SCHEMA, "Only members of this cycle.")
LIFESPAN = Parameter(
    "lifespan",
    parse_choice(Planting.Lifespan.values),
    {"type": "string", "enum": list(Planting.Lifespan.values)},
    "Only annual or only persistent plantings.",
)


class ReadOnlyView:
    """The shared read-only surface: write methods answer a JSON 405 and never write."""

    http_method_names = ["get", "head", "options"]


class ListView(ReadOnlyView, generics.ListAPIView):
    """A paginated list whose query parameters are parsed once per request."""

    filters = ()

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.parameters = {}
        # Parse after the method is known to be answered here: a write must be a 405,
        # not a 400 about a filter it never reads.
        if request.method.lower() not in self.http_method_names:
            return
        for parameter in self.filters:
            raw = request.query_params.get(parameter.name)
            if raw is not None:
                self.parameters[parameter.name] = parameter.parse(parameter.name, raw)


class DetailView(ReadOnlyView, generics.RetrieveAPIView):
    """One record; filters are ignored and an unknown id is a JSON 404."""


RESOURCES = (
    ("crops", "crop-list"),
    ("varieties", "variety-list"),
    ("areas", "area-list"),
    ("plantings", "planting-list"),
    ("events", "event-list"),
    ("evaluations", "evaluation-list"),
    ("observations", "observation-list"),
)


class DiscoveryView(ReadOnlyView, APIView):
    """Where to start: the version, how to authenticate and every collection."""

    def get(self, request: HttpRequest):
        return Response(
            {
                "version": "v1",
                "authentication": "Bearer <token>",
                "resources": {
                    name: request.build_absolute_uri(reverse(f"api:{route}"))
                    for name, route in RESOURCES
                },
            }
        )


class CropList(ListView):
    serializer_class = CropSerializer
    filters = (
        ARCHIVED,
        Parameter("category", parse_identifier, ID_SCHEMA, "Only crops in this category."),
        Parameter("search", parse_text, TEXT_SCHEMA, "Only crops whose name contains this text."),
    )

    def get_queryset(self):
        records = Crop.objects.all()
        values = self.parameters
        if "archived" in values:
            records = records.filter(is_archived=values["archived"])
        if "category" in values:
            records = records.filter(categories__pk=values["category"])
        key = name_search_key(values)
        if key is not None:
            records = records.filter(name_key__contains=key)
        return records.prefetch_related("categories")


class CropDetail(DetailView):
    serializer_class = CropSerializer
    queryset = Crop.objects.prefetch_related("categories")


class VarietyList(ListView):
    serializer_class = VarietySerializer
    filters = (
        CROP,
        ARCHIVED,
        Parameter(
            "search", parse_text, TEXT_SCHEMA, "Only varieties whose name contains this text."
        ),
    )

    def get_queryset(self):
        records = Variety.objects.select_related("crop")
        values = self.parameters
        if "crop" in values:
            records = records.filter(crop_id=values["crop"])
        if "archived" in values:
            records = records.filter(is_archived=values["archived"])
        key = name_search_key(values)
        if key is not None:
            records = records.filter(name_key__contains=key)
        return records


class VarietyDetail(DetailView):
    serializer_class = VarietySerializer
    queryset = Variety.objects.select_related("crop")


class AreaList(ListView):
    serializer_class = AreaSerializer
    filters = (
        Parameter(
            "parent",
            parse_parent,
            {"type": ["integer", "string"], "minimum": 1},
            "Only top-level areas (none) or only children of this area.",
        ),
        ARCHIVED,
        Parameter(
            "area_type", parse_text, TEXT_SCHEMA, "Only areas whose stored type matches exactly."
        ),
        Parameter("search", parse_text, TEXT_SCHEMA, "Only areas whose name contains this text."),
    )

    def get_queryset(self):
        records = GrowingArea.objects.select_related("parent")
        values = self.parameters
        if "parent" in values:
            parent = values["parent"]
            records = (
                records.filter(parent__isnull=True)
                if parent is None
                else records.filter(parent_id=parent)
            )
        if "archived" in values:
            records = records.filter(is_archived=values["archived"])
        # A blank area_type filters nothing; None would otherwise mean "no type stored".
        if values.get("area_type"):
            records = records.filter(area_type=values["area_type"])
        key = name_search_key(values)
        if key is not None:
            records = records.filter(name_key__contains=key)
        return records


class AreaDetail(DetailView):
    serializer_class = AreaSerializer
    queryset = GrowingArea.objects.select_related("parent")


class PlantingList(ListView):
    serializer_class = PlantingSerializer
    filters = (
        YEAR,
        CROP,
        VARIETY,
        AREA,
        CYCLE,
        Parameter(
            "status",
            parse_choice(PlantingCycle.Status.values),
            {"type": "string", "enum": list(PlantingCycle.Status.values)},
            "Only members of a cycle in this state.",
        ),
        LIFESPAN,
    )

    def get_queryset(self):
        values = self.parameters
        records = planting_history(
            year=values.get("year"),
            crop=values.get("crop"),
            variety=values.get("variety"),
            area=values.get("area"),
        )
        if "cycle" in values:
            records = records.filter(cycle_id=values["cycle"])
        if "status" in values:
            records = records.filter(cycle__status=values["status"])
        if "lifespan" in values:
            records = records.filter(lifespan=values["lifespan"])
        return records


class PlantingDetail(DetailView):
    serializer_class = PlantingSerializer
    queryset = Planting.objects.select_related("cycle", "crop", "variety", "area")


class EventList(ListView):
    serializer_class = EventSerializer
    filters = (
        Parameter("planting", parse_identifier, ID_SCHEMA, "Only events of this planting."),
        Parameter(
            "kind",
            parse_choice(PlantingEvent.Kind.values),
            {"type": "string", "enum": list(PlantingEvent.Kind.values)},
            "Only events of this kind.",
        ),
        YEAR,
        CROP,
        AREA,
    )

    def get_queryset(self):
        records = PlantingEvent.objects.select_related("planting", "diary_entry")
        values = self.parameters
        if "planting" in values:
            records = records.filter(planting_id=values["planting"])
        if "kind" in values:
            records = records.filter(kind=values["kind"])
        if "year" in values:
            year = values["year"]
            records = records.filter(Q(planned_date__year=year) | Q(actual_date__year=year))
        if "crop" in values:
            records = records.filter(planting__crop_id=values["crop"])
        if "area" in values:
            areas = list(GrowingArea.objects.all())
            records = records.filter(planting__area_id__in=descendant_ids(areas, values["area"]))
        return records


class EventDetail(DetailView):
    serializer_class = EventSerializer
    queryset = PlantingEvent.objects.select_related("planting", "diary_entry")


class EvaluationList(ListView):
    """Stored results only: an evaluation that is blank after stripping is not a result."""

    serializer_class = EvaluationSerializer
    filters = (CYCLE, CROP, VARIETY, AREA, YEAR)

    def get_queryset(self):
        values = self.parameters
        records = planting_history(
            year=values.get("year"),
            crop=values.get("crop"),
            variety=values.get("variety"),
            area=values.get("area"),
        )
        if "cycle" in values:
            records = records.filter(cycle_id=values["cycle"])
        return records.filter(evaluation__regex=r"\S")


class ObservationList(ListView):
    serializer_class = ObservationSerializer
    filters = (
        Parameter("from", parse_date, DATE_SCHEMA, "Only entries on this date or later."),
        Parameter("to", parse_date, DATE_SCHEMA, "Only entries on this date or earlier."),
        YEAR,
        AREA,
        CROP,
        VARIETY,
        Parameter("planting", parse_identifier, ID_SCHEMA, "Only entries linked to this planting."),
        Parameter(
            "calendar", parse_flag, FLAG_SCHEMA, "Only entries shown (true) or hidden (false)."
        ),
        Parameter("q", parse_text, TEXT_SCHEMA, "Only entries whose text matches this search."),
    )

    def get_queryset(self):
        values = self.parameters
        links = {}
        for name, model in (
            ("area", GrowingArea),
            ("crop", Crop),
            ("variety", Variety),
            ("planting", Planting),
        ):
            if name in values:
                record = model.objects.filter(pk=values[name]).first()
                if record is None:
                    return DiaryEntry.objects.none()
                links[name] = record
        entries = filter_entries(
            q=values.get("q"), year=values.get("year"), **links
        ).prefetch_related("links")
        if "from" in values:
            entries = entries.filter(date__gte=values["from"])
        if "to" in values:
            entries = entries.filter(date__lte=values["to"])
        if "calendar" in values:
            entries = entries.filter(show_in_calendar=values["calendar"])
        return entries


class ObservationDetail(DetailView):
    serializer_class = ObservationSerializer
    queryset = DiaryEntry.objects.prefetch_related("links")
