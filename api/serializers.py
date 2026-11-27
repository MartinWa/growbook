"""Serializers for the version 1 JSON API.

Only the listed fields are exposed: stored user text is returned unchanged and a
nullable value keeps its meaning, so an unrecorded quantity stays ``null`` and never
becomes an invented zero or an empty display label. The ``*WriteSerializer`` classes
are the request bodies; the views hand them to the same validated service operations
the interface uses.
"""

from django.urls import reverse
from rest_framework import serializers

URL_SCHEMA = {"type": "string", "format": "uri"}
NULLABLE_INTEGER_SCHEMA = {"type": ["integer", "null"]}
REFERENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "name": {"type": "string"},
        "url": URL_SCHEMA,
    },
    "required": ["id", "name", "url"],
    "additionalProperties": False,
}
NULLABLE_REFERENCE_SCHEMA = {**REFERENCE_SCHEMA, "type": ["object", "null"]}
CYCLE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "format": "uuid"},
        "status": {"type": "string", "enum": ["planned", "ongoing", "completed"]},
        "started_on": {"type": ["string", "null"], "format": "date"},
        "completed_on": {"type": ["string", "null"], "format": "date"},
    },
    "required": ["id", "status", "started_on", "completed_on"],
    "additionalProperties": False,
}
LINK_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["area", "crop", "variety", "planting"]},
        "id": {"type": "integer"},
    },
    "required": ["type", "id"],
    "additionalProperties": False,
}


class DocumentedField(serializers.SerializerMethodField):
    """A computed field that also states its JSON Schema type for the OpenAPI document."""

    def __init__(self, schema, **kwargs):
        self.json_schema = schema
        super().__init__(**kwargs)


def absolute(serializer, path):
    """Build an absolute link from the request so the page works behind any hostname."""
    request = serializer.context.get("request")
    return request.build_absolute_uri(path) if request is not None else path


def reference(serializer, record, view_name):
    if record is None:
        return None
    return {
        "id": record.pk,
        "name": record.name,
        "url": absolute(serializer, reverse(view_name, args=[record.pk])),
    }


class CropSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(read_only=True)
    notes = serializers.CharField(read_only=True)
    archived = serializers.BooleanField(source="is_archived", read_only=True)
    categories = DocumentedField(schema={"type": "array", "items": REFERENCE_SCHEMA})
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)
    url = DocumentedField(schema=URL_SCHEMA)

    def get_categories(self, crop):
        return [
            reference(self, category, "garden:category-detail")
            for category in crop.categories.all()
        ]

    def get_url(self, crop):
        return absolute(self, crop.get_absolute_url())


class VarietySerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    crop = DocumentedField(schema=REFERENCE_SCHEMA)
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)
    source = serializers.CharField(read_only=True)
    experiences = serializers.CharField(read_only=True)
    archived = serializers.BooleanField(source="is_archived", read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)
    url = DocumentedField(schema=URL_SCHEMA)

    def get_crop(self, variety):
        return reference(self, variety.crop, "garden:crop-detail")

    def get_url(self, variety):
        return absolute(self, variety.get_absolute_url())


class AreaSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    parent = DocumentedField(schema=NULLABLE_REFERENCE_SCHEMA)
    name = serializers.CharField(read_only=True)
    area_type = serializers.CharField(read_only=True)
    length_cm = serializers.IntegerField(read_only=True, allow_null=True)
    width_cm = serializers.IntegerField(read_only=True, allow_null=True)
    description = serializers.CharField(read_only=True)
    notes = serializers.CharField(read_only=True)
    archived = serializers.BooleanField(source="is_archived", read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)
    url = DocumentedField(schema=URL_SCHEMA)

    def get_parent(self, area):
        return reference(self, area.parent, "garden:area-detail")

    def get_url(self, area):
        return absolute(self, area.get_absolute_url())


class PlantingSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    cycle = DocumentedField(schema=CYCLE_SCHEMA)
    start_year = serializers.IntegerField(read_only=True)
    lifespan = serializers.CharField(read_only=True)
    end_year = serializers.IntegerField(read_only=True, allow_null=True)
    crop = DocumentedField(schema=REFERENCE_SCHEMA)
    variety = DocumentedField(schema=NULLABLE_REFERENCE_SCHEMA)
    area = DocumentedField(schema=NULLABLE_REFERENCE_SCHEMA)
    label = serializers.CharField(read_only=True)
    quantity = serializers.IntegerField(read_only=True, allow_null=True)
    notes = serializers.CharField(read_only=True)
    evaluation = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)
    url = DocumentedField(schema=URL_SCHEMA)

    def get_cycle(self, planting):
        cycle = planting.cycle
        return {
            "id": str(cycle.pk),
            "status": cycle.status,
            "started_on": cycle.started_on.isoformat() if cycle.started_on else None,
            "completed_on": cycle.completed_on.isoformat() if cycle.completed_on else None,
        }

    def get_crop(self, planting):
        return reference(self, planting.crop, "garden:crop-detail")

    def get_variety(self, planting):
        return reference(self, planting.variety, "garden:variety-detail")

    def get_area(self, planting):
        return reference(self, planting.area, "garden:area-detail")

    def get_url(self, planting):
        return absolute(self, planting.get_absolute_url())


class EventSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    planting = serializers.IntegerField(source="planting_id", read_only=True)
    planting_url = DocumentedField(schema=URL_SCHEMA)
    kind = serializers.CharField(read_only=True)
    planned_date = serializers.DateField(read_only=True, allow_null=True)
    actual_date = serializers.DateField(read_only=True, allow_null=True)
    note = serializers.CharField(read_only=True)
    diary_entry = DocumentedField(schema=NULLABLE_INTEGER_SCHEMA)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)
    url = DocumentedField(schema=URL_SCHEMA)

    def get_planting_url(self, event):
        return absolute(self, reverse("garden:planting-detail", args=[event.planting_id]))

    def get_diary_entry(self, event):
        # The reverse one-to-one raises an AttributeError subclass when unset.
        entry = getattr(event, "diary_entry", None)
        return entry.pk if entry is not None else None

    def get_url(self, event):
        return absolute(self, event.get_absolute_url())


class EvaluationSerializer(serializers.Serializer):
    """One stored result, still attached to the planting and cycle that hold it."""

    planting = serializers.IntegerField(source="pk", read_only=True)
    cycle = serializers.UUIDField(source="cycle_id", read_only=True)
    cycle_status = serializers.CharField(source="cycle.status", read_only=True)
    start_year = serializers.IntegerField(read_only=True)
    lifespan = serializers.CharField(read_only=True)
    end_year = serializers.IntegerField(read_only=True, allow_null=True)
    crop = DocumentedField(schema=REFERENCE_SCHEMA)
    variety = DocumentedField(schema=NULLABLE_REFERENCE_SCHEMA)
    area = DocumentedField(schema=NULLABLE_REFERENCE_SCHEMA)
    label = serializers.CharField(read_only=True)
    evaluation = serializers.CharField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)
    url = DocumentedField(schema=URL_SCHEMA)

    def get_crop(self, planting):
        return reference(self, planting.crop, "garden:crop-detail")

    def get_variety(self, planting):
        return reference(self, planting.variety, "garden:variety-detail")

    def get_area(self, planting):
        return reference(self, planting.area, "garden:area-detail")

    def get_url(self, planting):
        return absolute(self, planting.get_absolute_url())


class ObservationSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    date = serializers.DateField(read_only=True)
    title = serializers.CharField(read_only=True)
    body = serializers.CharField(read_only=True)
    show_in_calendar = serializers.BooleanField(read_only=True)
    event = DocumentedField(schema=NULLABLE_INTEGER_SCHEMA)
    links = DocumentedField(schema={"type": "array", "items": LINK_SCHEMA})
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)
    url = DocumentedField(schema=URL_SCHEMA)

    def get_event(self, entry):
        return entry.event_id

    def get_links(self, entry):
        # Grouped by kind in a fixed order, so the list reads the same on every request.
        stored = list(entry.links.all())
        links = []
        for kind in ("area", "crop", "variety", "planting"):
            for link in stored:
                value = getattr(link, f"{kind}_id")
                if value is not None:
                    links.append({"type": kind, "id": value})
        return links

    def get_url(self, entry):
        return absolute(self, entry.get_absolute_url())


class LinkWriteSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["area", "crop", "variety", "planting"])
    id = serializers.IntegerField()


class CropWriteSerializer(serializers.Serializer):
    name = serializers.CharField()
    notes = serializers.CharField(required=False, allow_blank=True)
    archived = serializers.BooleanField(required=False)


class VarietyWriteSerializer(serializers.Serializer):
    crop = serializers.IntegerField()
    name = serializers.CharField()
    description = serializers.CharField(required=False, allow_blank=True)
    source = serializers.CharField(required=False, allow_blank=True)
    experiences = serializers.CharField(required=False, allow_blank=True)
    archived = serializers.BooleanField(required=False)


class AreaWriteSerializer(serializers.Serializer):
    name = serializers.CharField()
    parent = serializers.IntegerField(required=False, allow_null=True)
    area_type = serializers.CharField(required=False, allow_blank=True)
    length_cm = serializers.IntegerField(required=False, allow_null=True)
    width_cm = serializers.IntegerField(required=False, allow_null=True)
    description = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    archived = serializers.BooleanField(required=False)


class PlantingWriteSerializer(serializers.Serializer):
    crop = serializers.IntegerField()
    start_year = serializers.IntegerField()
    variety = serializers.IntegerField(required=False, allow_null=True)
    area = serializers.IntegerField(required=False, allow_null=True)
    lifespan = serializers.ChoiceField(choices=["annual", "persistent"], required=False)
    end_year = serializers.IntegerField(required=False, allow_null=True)
    label = serializers.CharField(required=False, allow_blank=True)
    quantity = serializers.IntegerField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    evaluation = serializers.CharField(required=False, allow_blank=True)


class EventWriteSerializer(serializers.Serializer):
    planting = serializers.IntegerField()
    kind = serializers.ChoiceField(
        choices=["sowing", "transplant", "replanting", "harvest_start", "harvest_end", "other"]
    )
    planned_date = serializers.DateField(required=False, allow_null=True)
    actual_date = serializers.DateField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True)


class ObservationWriteSerializer(serializers.Serializer):
    date = serializers.DateField()
    # A photo-only entry has an empty note; the diary service decides whether that is enough.
    body = serializers.CharField(allow_blank=True)
    title = serializers.CharField(required=False, allow_blank=True)
    links = serializers.ListField(child=LinkWriteSerializer(), required=False)
    show_in_calendar = serializers.BooleanField(required=False)
