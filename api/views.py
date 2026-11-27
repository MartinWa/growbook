"""Version 1 API views over the garden registers, plantings and diary.

Reads are unguarded in the domain layer; writes go through the same validated
service operations the interface uses, so an API write cannot bypass a rule the
screens enforce. Methods outside the documented set answer a JSON 405 and never
write.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET
from rest_framework import generics
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import APIView

from diary.models import DiaryEntry
from diary.queries import filter_entries
from diary.services import (
    EntryConflict,
    SubmissionConflict,
    SubmissionDeleted,
    create_entry,
    delete_entry,
    update_entry,
)
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
from garden.services._shared import RegisterConflict
from garden.services.events import (
    create_planting_event,
    delete_planting_event,
    edit_planting_event,
)
from garden.services.plantings import create_planting, delete_planting, edit_planting
from garden.services.registers import (
    archive_area,
    archive_crop,
    archive_variety,
    create_area,
    create_crop,
    create_variety,
    delete_area,
    delete_crop,
    delete_variety,
    edit_area,
    edit_crop,
    edit_variety,
)

from .serializers import (
    AreaSerializer,
    AreaWriteSerializer,
    CropSerializer,
    CropWriteSerializer,
    EvaluationSerializer,
    EventSerializer,
    EventWriteSerializer,
    ObservationSerializer,
    ObservationWriteSerializer,
    PlantingSerializer,
    PlantingWriteSerializer,
    VarietySerializer,
    VarietyWriteSerializer,
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


class BaseView:
    """The shared API surface: reads and validated writes; other methods answer a JSON 405."""

    http_method_names = ["get", "head", "options", "post", "put", "patch", "delete"]


CONFLICT_ERRORS = (RegisterConflict, EntryConflict, SubmissionConflict, SubmissionDeleted)
PRECONDITION_MESSAGE = "Send If-Match with the record's current updated_at value."


def call_service(func, *args, **kwargs):
    """Run one validated write operation, mapping its failures to API responses."""
    try:
        return func(*args, **kwargs), None
    except CONFLICT_ERRORS as exc:
        return None, Response({"detail": "; ".join(exc.messages)}, status=409)
    except DjangoValidationError as exc:
        if hasattr(exc, "error_dict"):
            return None, Response(
                {field: list(messages) for field, messages in exc.message_dict.items()},
                status=400,
            )
        return None, Response({"detail": "; ".join(exc.messages)}, status=400)


def expected_version(request):
    """The If-Match header as an aware datetime, or an error response to return."""
    raw = request.headers.get("If-Match")
    if raw is None:
        return None, Response({"detail": PRECONDITION_MESSAGE}, status=428)
    try:
        parsed = datetime.fromisoformat(raw.strip().strip('"'))
    except ValueError:
        return None, Response({"detail": "If-Match must be an ISO 8601 timestamp."}, status=400)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, datetime.UTC)
    return parsed, None


def _result_record(result):
    """Service results wrap the record differently per app; unwrap either shape."""
    for attribute in ("record", "entry"):
        if hasattr(result, attribute):
            return getattr(result, attribute)
    return result


def _apply_archive(actor, record, archived, archive):
    """Apply an archived flag after the edit, and return the refreshed record."""
    if archived is not None and archived != record.is_archived:
        archive(actor=actor, pk=record.pk, archived=archived)
        record.refresh_from_db()
    return record


def create_crop_record(request, data):
    return create_crop(actor=request.user, name=data["name"], notes=data.get("notes", "")).record


def crop_defaults(record):
    return {"name": record.name, "notes": record.notes}


def edit_crop_record(request, record, data, expected):
    archived = data.pop("archived", None)
    result = edit_crop(
        actor=request.user,
        pk=record.pk,
        name=data["name"],
        notes=data.get("notes", ""),
        expected_updated_at=expected,
    )
    return _apply_archive(request.user, result.record, archived, archive_crop)


def delete_crop_record(request, record, expected):
    delete_crop(actor=request.user, pk=record.pk, expected_updated_at=expected)


def create_variety_record(request, data):
    return create_variety(
        actor=request.user,
        crop=data["crop"],
        name=data["name"],
        description=data.get("description", ""),
        source=data.get("source", ""),
        experiences=data.get("experiences", ""),
    ).record


def variety_defaults(record):
    return {
        "crop": record.crop_id,
        "name": record.name,
        "description": record.description,
        "source": record.source,
        "experiences": record.experiences,
    }


def edit_variety_record(request, record, data, expected):
    archived = data.pop("archived", None)
    result = edit_variety(
        actor=request.user,
        pk=record.pk,
        crop=data["crop"],
        name=data["name"],
        description=data.get("description", ""),
        source=data.get("source", ""),
        experiences=data.get("experiences", ""),
        expected_updated_at=expected,
    )
    return _apply_archive(request.user, result.record, archived, archive_variety)


def delete_variety_record(request, record, expected):
    delete_variety(actor=request.user, pk=record.pk, expected_updated_at=expected)


def create_area_record(request, data):
    return create_area(
        actor=request.user,
        name=data["name"],
        parent=data.get("parent"),
        area_type=data.get("area_type", ""),
        length_cm=data.get("length_cm"),
        width_cm=data.get("width_cm"),
        description=data.get("description", ""),
        notes=data.get("notes", ""),
    ).record


def area_defaults(record):
    return {
        "name": record.name,
        "parent": record.parent_id,
        "area_type": record.area_type,
        "length_cm": record.length_cm,
        "width_cm": record.width_cm,
        "description": record.description,
        "notes": record.notes,
    }


def edit_area_record(request, record, data, expected):
    archived = data.pop("archived", None)
    result = edit_area(
        actor=request.user,
        pk=record.pk,
        name=data["name"],
        parent=data.get("parent"),
        area_type=data.get("area_type", ""),
        length_cm=data.get("length_cm"),
        width_cm=data.get("width_cm"),
        description=data.get("description", ""),
        notes=data.get("notes", ""),
        expected_updated_at=expected,
    )
    return _apply_archive(request.user, result.record, archived, archive_area)


def delete_area_record(request, record, expected):
    delete_area(actor=request.user, pk=record.pk, expected_updated_at=expected)


def planting_defaults(record):
    return {
        "crop": record.crop_id,
        "start_year": record.start_year,
        "variety": record.variety_id,
        "area": record.area_id,
        "lifespan": record.lifespan,
        "end_year": record.end_year,
        "label": record.label,
        "quantity": record.quantity,
        "notes": record.notes,
        "evaluation": record.evaluation,
    }


def create_planting_record(request, data):
    return create_planting(
        actor=request.user,
        crop=data["crop"],
        start_year=data["start_year"],
        variety=data.get("variety"),
        area=data.get("area"),
        lifespan=data.get("lifespan", Planting.Lifespan.ANNUAL),
        end_year=data.get("end_year"),
        label=data.get("label", ""),
        quantity=data.get("quantity"),
        notes=data.get("notes", ""),
        evaluation=data.get("evaluation", ""),
    ).record


def edit_planting_record(request, record, data, expected):
    return edit_planting(
        actor=request.user,
        pk=record.pk,
        crop=data["crop"],
        start_year=data["start_year"],
        variety=data.get("variety"),
        area=data.get("area"),
        lifespan=data.get("lifespan", Planting.Lifespan.ANNUAL),
        end_year=data.get("end_year"),
        label=data.get("label", ""),
        quantity=data.get("quantity"),
        notes=data.get("notes", ""),
        evaluation=data.get("evaluation", ""),
        expected_updated_at=expected,
    ).record


def delete_planting_record(request, record, expected):
    delete_planting(actor=request.user, pk=record.pk, expected_updated_at=expected)


def event_defaults(record):
    return {
        "planting": record.planting_id,
        "kind": record.kind,
        "planned_date": record.planned_date,
        "actual_date": record.actual_date,
        "note": record.note,
    }


def create_event_record(request, data):
    return _result_record(
        create_planting_event(
            actor=request.user,
            planting=data["planting"],
            kind=data["kind"],
            planned_date=data.get("planned_date"),
            actual_date=data.get("actual_date"),
            note=data.get("note", ""),
        )
    )


def edit_event_record(request, record, data, expected):
    return _result_record(
        edit_planting_event(
            actor=request.user,
            pk=record.pk,
            planting=data["planting"],
            kind=data["kind"],
            planned_date=data.get("planned_date"),
            actual_date=data.get("actual_date"),
            note=data.get("note", ""),
            expected_updated_at=expected,
        )
    )


def delete_event_record(request, record, expected):
    delete_planting_event(actor=request.user, pk=record.pk, expected_updated_at=expected)


def _link_tuples(links):
    return [(link["type"], link["id"]) for link in links]


def observation_defaults(record):
    links = []
    for kind in ("area", "crop", "variety", "planting"):
        for link in record.links.all():
            target = getattr(link, f"{kind}_id")
            if target is not None:
                links.append({"type": kind, "id": target})
    return {
        "date": record.date,
        "body": record.body,
        "title": record.title,
        "links": links,
        "show_in_calendar": record.show_in_calendar,
    }


def create_observation_record(request, data):
    return create_entry(
        author=request.user,
        date=data["date"],
        body=data["body"],
        uploads=[],
        submission_key=None,
        links=_link_tuples(data.get("links", [])),
        title=data.get("title", ""),
        show_in_calendar=data.get("show_in_calendar", False),
    ).entry


def edit_observation_record(request, record, data, expected):
    return _result_record(
        update_entry(
            actor=request.user,
            pk=record.pk,
            date=data["date"],
            body=data["body"],
            title=data.get("title", ""),
            links=_link_tuples(data.get("links", [])),
            uploads=[],
            remove_photo_ids=[],
            expected_updated_at=expected,
            submission_key=None,
            show_in_calendar=data.get("show_in_calendar", False),
        )
    )


def delete_observation_record(request, record, expected):
    delete_entry(actor=request.user, pk=record.pk, expected_updated_at=expected)


class ListView(BaseView, generics.ListAPIView):
    """A paginated list whose query parameters are parsed once per request."""

    filters = ()
    write_serializer_class = None
    write_create = None

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.parameters = {}
        # Filters apply to reads only: a write is never a 400 about a filter it does not read.
        if request.method.lower() not in ("get", "head", "options"):
            return
        for parameter in self.filters:
            raw = request.query_params.get(parameter.name)
            if raw is not None:
                self.parameters[parameter.name] = parameter.parse(parameter.name, raw)

    def post(self, request):
        if self.write_create is None or self.write_serializer_class is None:
            return Response({"detail": 'Method "POST" not allowed.'}, status=405)
        serializer = self.write_serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        record, error = call_service(self.write_create, request, serializer.validated_data)
        if error is not None:
            return error
        return Response(
            self.serializer_class(record, context={"request": request}).data, status=201
        )


class DetailView(BaseView, generics.RetrieveAPIView):
    """One record; filters are ignored and an unknown id is a JSON 404."""

    write_serializer_class = None
    write_update = None
    write_delete = None
    write_defaults = None

    def put(self, request, pk):
        return self._update(request, pk, partial=False)

    def patch(self, request, pk):
        return self._update(request, pk, partial=True)

    def _update(self, request, pk, partial):
        if self.write_update is None or self.write_serializer_class is None:
            return Response({"detail": f'Method "{request.method}" not allowed.'}, status=405)
        record = self.get_object()
        expected, error = expected_version(request)
        if error is not None:
            return error
        serializer = self.write_serializer_class(data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        if partial:
            data = {**self.write_defaults(record), **data}
        record, error = call_service(self.write_update, request, record, data, expected)
        if error is not None:
            return error
        return Response(self.serializer_class(record, context={"request": request}).data)

    def delete(self, request, pk):
        if self.write_delete is None:
            return Response({"detail": 'Method "DELETE" not allowed.'}, status=405)
        record = self.get_object()
        expected, error = expected_version(request)
        if error is not None:
            return error
        _, error = call_service(self.write_delete, request, record, expected)
        if error is not None:
            return error
        return Response(status=204)


RESOURCES = (
    ("crops", "crop-list"),
    ("varieties", "variety-list"),
    ("areas", "area-list"),
    ("plantings", "planting-list"),
    ("events", "event-list"),
    ("evaluations", "evaluation-list"),
    ("observations", "observation-list"),
)


class DiscoveryView(BaseView, APIView):
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
    write_serializer_class = CropWriteSerializer
    write_create = staticmethod(create_crop_record)
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
    write_serializer_class = CropWriteSerializer
    write_update = staticmethod(edit_crop_record)
    write_delete = staticmethod(delete_crop_record)
    write_defaults = staticmethod(crop_defaults)


class VarietyList(ListView):
    serializer_class = VarietySerializer
    write_serializer_class = VarietyWriteSerializer
    write_create = staticmethod(create_variety_record)
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
    write_serializer_class = VarietyWriteSerializer
    write_update = staticmethod(edit_variety_record)
    write_delete = staticmethod(delete_variety_record)
    write_defaults = staticmethod(variety_defaults)


class AreaList(ListView):
    serializer_class = AreaSerializer
    write_serializer_class = AreaWriteSerializer
    write_create = staticmethod(create_area_record)
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
    write_serializer_class = AreaWriteSerializer
    write_update = staticmethod(edit_area_record)
    write_delete = staticmethod(delete_area_record)
    write_defaults = staticmethod(area_defaults)


class PlantingList(ListView):
    serializer_class = PlantingSerializer
    write_serializer_class = PlantingWriteSerializer
    write_create = staticmethod(create_planting_record)
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
    write_serializer_class = PlantingWriteSerializer
    write_update = staticmethod(edit_planting_record)
    write_delete = staticmethod(delete_planting_record)
    write_defaults = staticmethod(planting_defaults)


class EventList(ListView):
    serializer_class = EventSerializer
    write_serializer_class = EventWriteSerializer
    write_create = staticmethod(create_event_record)
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
    write_serializer_class = EventWriteSerializer
    write_update = staticmethod(edit_event_record)
    write_delete = staticmethod(delete_event_record)
    write_defaults = staticmethod(event_defaults)


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
    write_serializer_class = ObservationWriteSerializer
    write_create = staticmethod(create_observation_record)
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
    write_serializer_class = ObservationWriteSerializer
    write_update = staticmethod(edit_observation_record)
    write_delete = staticmethod(delete_observation_record)
    write_defaults = staticmethod(observation_defaults)


@csrf_exempt
def schema_document(request: HttpRequest) -> HttpResponse:
    """The OpenAPI document as JSON. Public: it describes the API and serves no data."""
    from .schema import render_document  # Imported here: api.schema imports this module.

    if request.method not in ("GET", "HEAD"):
        detail = f'Method "{request.method}" not allowed.'
        body = json.dumps({"detail": detail})
        return HttpResponse(body, content_type="application/json", status=405)
    return HttpResponse(render_document(), content_type="application/json")


@require_GET
def docs_page(request: HttpRequest):
    """The public page that renders the OpenAPI document with the self-hosted Swagger UI."""
    return render(request, "api/docs.html")
