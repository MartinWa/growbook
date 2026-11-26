"""The OpenAPI 3.1 document for the version 1 API.

Both the documented fields and the documented filters are read from the serializers
and the views, so the committed document cannot drift away from what the API does.
"""

import json

from django.conf import settings
from rest_framework import serializers

from . import views
from .pagination import BoundedPageNumberPagination
from .serializers import (
    AreaSerializer,
    CropSerializer,
    DocumentedField,
    EvaluationSerializer,
    EventSerializer,
    ObservationSerializer,
    PlantingSerializer,
    VarietySerializer,
)

# (path segment, list view, detail view or None, serializer) for each collection.
ENDPOINTS = (
    ("crops", views.CropList, views.CropDetail, CropSerializer),
    ("varieties", views.VarietyList, views.VarietyDetail, VarietySerializer),
    ("areas", views.AreaList, views.AreaDetail, AreaSerializer),
    ("plantings", views.PlantingList, views.PlantingDetail, PlantingSerializer),
    ("events", views.EventList, views.EventDetail, EventSerializer),
    ("evaluations", views.EvaluationList, None, EvaluationSerializer),
    ("observations", views.ObservationList, views.ObservationDetail, ObservationSerializer),
)

DOCUMENTED_PAGE_SIZE = settings.REST_FRAMEWORK["PAGE_SIZE"]
DOCUMENTED_MAX_PAGE_SIZE = BoundedPageNumberPagination.max_page_size

ERROR_SCHEMA = {
    "type": "object",
    "properties": {"detail": {"type": "string"}},
    "required": ["detail"],
    "additionalProperties": False,
}
PARAMETER_ERROR_SCHEMA = {
    "type": "object",
    "properties": {"detail": {"type": "string"}, "parameter": {"type": "string"}},
    "required": ["detail", "parameter"],
    "additionalProperties": False,
}
DISCOVERY_SCHEMA = {
    "type": "object",
    "properties": {
        "version": {"type": "string"},
        "authentication": {"type": "string"},
        "resources": {
            "type": "object",
            "additionalProperties": {"type": "string", "format": "uri"},
        },
    },
    "required": ["version", "authentication", "resources"],
    "additionalProperties": False,
}
PAGE_PARAMETERS = (
    {
        "name": "page",
        "in": "query",
        "required": False,
        "schema": {"type": "integer", "minimum": 1},
        "description": "Page number, counted from 1.",
    },
    {
        "name": BoundedPageNumberPagination.page_size_query_param,
        "in": "query",
        "required": False,
        "schema": {
            "type": "integer",
            "minimum": 1,
            "maximum": DOCUMENTED_MAX_PAGE_SIZE,
        },
        "description": (
            f"Rows per page. The default is {DOCUMENTED_PAGE_SIZE}; a larger request is "
            f"capped at {DOCUMENTED_MAX_PAGE_SIZE}."
        ),
    },
)


def _field_schema(field):
    if isinstance(field, DocumentedField):
        return field.json_schema
    if isinstance(field, serializers.BooleanField):
        schema = {"type": "boolean"}
    elif isinstance(field, serializers.IntegerField):
        schema = {"type": "integer"}
    elif isinstance(field, serializers.DateTimeField):
        schema = {"type": "string", "format": "date-time"}
    elif isinstance(field, serializers.DateField):
        schema = {"type": "string", "format": "date"}
    elif isinstance(field, serializers.UUIDField):
        schema = {"type": "string", "format": "uuid"}
    elif isinstance(field, serializers.CharField):
        schema = {"type": "string"}
    else:
        raise TypeError(f"No JSON Schema mapping for {type(field).__name__}.")
    if field.allow_null:
        schema = {**schema, "type": [schema["type"], "null"]}
    return schema


def component_name(serializer_class):
    return serializer_class.__name__.removesuffix("Serializer")


def object_schema(serializer_class):
    """One response object: the serializer's fields, in declaration order."""
    properties = {
        field.field_name: _field_schema(field) for field in serializer_class().fields.values()
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def paginated_schema(name):
    reference = f"#/components/schemas/{name}"
    return {
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "next": {"type": ["string", "null"], "format": "uri"},
            "previous": {"type": ["string", "null"], "format": "uri"},
            "results": {"type": "array", "items": {"$ref": reference}},
        },
        "required": ["count", "next", "previous", "results"],
        "additionalProperties": False,
    }


def _content(schema):
    return {"application/json": {"schema": schema}}


def _authentication_failure():
    return {
        "description": (
            "Missing, malformed, unknown, revoked or expired credential, or an inactive account."
        ),
        "content": _content({"$ref": "#/components/schemas/Error"}),
    }


def _method_not_allowed():
    return {
        "description": "The endpoint is read-only: only GET, HEAD and OPTIONS are allowed.",
        "content": _content({"$ref": "#/components/schemas/Error"}),
    }


def _not_found(name):
    return {
        "description": f"No {name} with that id.",
        "content": _content({"$ref": "#/components/schemas/Error"}),
    }


def _page_not_found():
    return {
        "description": "The requested page is outside the available results.",
        "content": _content({"$ref": "#/components/schemas/Error"}),
    }


def _list_operation(name, view):
    responses = {
        "200": {
            "description": f"A page of {name}.",
            "content": _content({"$ref": f"#/components/schemas/{name}Page"}),
        },
        "401": _authentication_failure(),
        "404": _page_not_found(),
        "405": _method_not_allowed(),
    }
    if view.filters:
        responses["400"] = {
            "description": (
                "A supported filter was given an unusable value. Unsupported parameter "
                "names are ignored instead."
            ),
            "content": _content({"$ref": "#/components/schemas/ParameterError"}),
        }
    return {
        "summary": f"List {name}",
        "operationId": f"list-{name}",
        "tags": [name],
        "parameters": [
            *(query_parameter(parameter) for parameter in view.filters),
            *PAGE_PARAMETERS,
        ],
        "responses": responses,
    }


def _detail_operation(name):
    return {
        "summary": f"Read one {name.removesuffix('s')}",
        "operationId": f"read-{name.removesuffix('s')}",
        "tags": [name],
        "parameters": [
            {
                "name": "pk",
                "in": "path",
                "required": True,
                "schema": {"type": "integer", "minimum": 1},
                "description": "Primary key of the record.",
            }
        ],
        "responses": {
            "200": {
                "description": f"One {name.removesuffix('s')}.",
                "content": _content({"$ref": f"#/components/schemas/{name}"}),
            },
            "401": _authentication_failure(),
            "404": _not_found(name.removesuffix("s")),
            "405": _method_not_allowed(),
        },
    }


def query_parameter(parameter):
    return {
        "name": parameter.name,
        "in": "query",
        "required": False,
        "schema": parameter.schema,
        "description": parameter.description,
    }


def _discovery_operation():
    return {
        "summary": "Discover the version, the authentication scheme and every collection",
        "operationId": "discover",
        "tags": ["discovery"],
        "parameters": [],
        "responses": {
            "200": {
                "description": "The entry point of this API version.",
                "content": _content(DISCOVERY_SCHEMA),
            },
            "401": _authentication_failure(),
            "405": _method_not_allowed(),
        },
    }


def build_document():
    schemas = {
        "Error": ERROR_SCHEMA,
        "ParameterError": PARAMETER_ERROR_SCHEMA,
    }
    paths = {"/": {"get": _discovery_operation()}}
    for name, list_view, detail_view, serializer_class in ENDPOINTS:
        schemas[name] = object_schema(serializer_class)
        schemas[f"{name}Page"] = paginated_schema(name)
        paths[f"/{name}/"] = {"get": _list_operation(name, list_view)}
        if detail_view is not None:
            paths[f"/{name}/{{pk}}/"] = {"get": _detail_operation(name)}
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "GrowBook garden API",
            "version": "1.0.0",
            "summary": "Read-only, paginated access to the garden registers, plantings and diary.",
            "description": (
                "Every endpoint is read-only and requires a bearer token issued to the "
                "account that owns it. Text is returned exactly as it was stored, and a "
                "null value means it was never recorded."
            ),
            "license": {"name": "PolyForm Noncommercial 1.0.0"},
        },
        "security": [{"bearerAuth": []}],
        # Paths are relative to the version prefix, which the URLconf mounts.
        "servers": [{"url": "/api/v1"}],
        "paths": paths,
        "components": {
            "schemas": schemas,
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": (
                        "A token you create on the API tokens page, sent as "
                        "'Authorization: Bearer <token>'."
                    ),
                }
            },
        },
    }


def render_document():
    """The committed file and the command output share one rendering."""
    return json.dumps(build_document(), indent=2, ensure_ascii=False) + "\n"
