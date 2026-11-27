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
    AreaWriteSerializer,
    CropSerializer,
    CropWriteSerializer,
    DocumentedField,
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

# Collections that accept writes, with the request-body serializer each one validates.
WRITABLE = {
    "crops": CropWriteSerializer,
    "varieties": VarietyWriteSerializer,
    "areas": AreaWriteSerializer,
    "plantings": PlantingWriteSerializer,
    "events": EventWriteSerializer,
    "observations": ObservationWriteSerializer,
}

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
FIELD_ERROR_SCHEMA = {
    "type": "object",
    "additionalProperties": {"type": "array", "items": {"type": "string"}},
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
    elif isinstance(field, serializers.ChoiceField):
        schema = {"type": "string", "enum": [str(choice) for choice in field.choices]}
    elif isinstance(field, serializers.ListField):
        schema = {"type": "array", "items": _field_schema(field.child)}
    elif isinstance(field, serializers.Serializer):
        schema = object_schema(type(field), all_required=False)
    elif isinstance(field, serializers.CharField):
        schema = {"type": "string"}
    else:
        raise TypeError(f"No JSON Schema mapping for {type(field).__name__}.")
    if field.allow_null:
        schema = {**schema, "type": [schema["type"], "null"]}
    return schema


def component_name(serializer_class):
    return serializer_class.__name__.removesuffix("Serializer")


def object_schema(serializer_class, *, all_required=True):
    """One object schema: the serializer's fields, in declaration order.

    Response serializers mark every field as required; a write serializer only
    requires the fields the service actually needs.
    """
    fields = serializer_class().fields
    properties = {name: _field_schema(field) for name, field in fields.items()}
    if all_required:
        required = list(properties)
    else:
        required = [
            name for name, field in fields.items() if field.required and not field.read_only
        ]
    return {
        "type": "object",
        "properties": properties,
        "required": required,
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
        "description": "The method is not allowed on this endpoint.",
        "content": _content({"$ref": "#/components/schemas/Error"}),
    }


IF_MATCH_PARAMETER = {
    "name": "If-Match",
    "in": "header",
    "required": True,
    "schema": {"type": "string", "format": "date-time"},
    "description": (
        "The record's current updated_at, so a lost update is refused instead of "
        "overwriting a newer change."
    ),
}


def _validation_failure():
    return {
        "description": (
            "The request body failed validation, or a supported filter was given an "
            "unusable value. Unsupported parameter names are ignored instead."
        ),
        "content": _content(
            {
                "oneOf": [
                    {"$ref": "#/components/schemas/Error"},
                    {"$ref": "#/components/schemas/FieldError"},
                ]
            }
        ),
    }


def _conflict():
    return {
        "description": (
            "The record changed since it was read (the If-Match version no longer "
            "matches), or the save was already applied differently."
        ),
        "content": _content({"$ref": "#/components/schemas/Error"}),
    }


def _precondition_required():
    return {
        "description": (
            "PUT, PATCH and DELETE require an If-Match header with the current updated_at."
        ),
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


def _schema_operation():
    return {
        "summary": "This document as JSON, without authentication",
        "operationId": "schema",
        "tags": ["discovery"],
        "parameters": [],
        "security": [],
        "responses": {
            "200": {
                "description": "The OpenAPI document that describes this API.",
                "content": _content({"type": "object"}),
            },
            "405": {
                "description": "The document is read-only: only GET and HEAD are allowed.",
                "content": _content({"$ref": "#/components/schemas/Error"}),
            },
        },
    }


def _create_operation(name):
    singular = name.removesuffix("s")
    return {
        "summary": f"Create a {singular}",
        "operationId": f"create-{singular}",
        "tags": [name],
        "parameters": [],
        "requestBody": {
            "required": True,
            "content": _content({"$ref": f"#/components/schemas/{name}Write"}),
        },
        "responses": {
            "201": {
                "description": f"The created {singular}.",
                "content": _content({"$ref": f"#/components/schemas/{name}"}),
            },
            "400": _validation_failure(),
            "401": _authentication_failure(),
            "409": _conflict(),
        },
    }


def _update_operation(name, method):
    singular = name.removesuffix("s")
    return {
        "summary": (
            f"Replace a {singular}" if method == "put" else f"Update parts of a {singular}"
        ),
        "operationId": f"{method}-{singular}",
        "tags": [name],
        "parameters": [IF_MATCH_PARAMETER],
        "requestBody": {
            "required": True,
            "content": _content({"$ref": f"#/components/schemas/{name}Write"}),
        },
        "responses": {
            "200": {
                "description": f"The updated {singular}.",
                "content": _content({"$ref": f"#/components/schemas/{name}"}),
            },
            "400": _validation_failure(),
            "401": _authentication_failure(),
            "404": _not_found(singular),
            "409": _conflict(),
            "428": _precondition_required(),
        },
    }


def _delete_operation(name):
    singular = name.removesuffix("s")
    return {
        "summary": f"Delete a {singular}",
        "operationId": f"delete-{singular}",
        "tags": [name],
        "parameters": [IF_MATCH_PARAMETER],
        "responses": {
            "204": {"description": f"The {singular} was deleted."},
            "400": {
                "description": "The If-Match header is not an ISO 8601 timestamp.",
                "content": _content({"$ref": "#/components/schemas/Error"}),
            },
            "401": _authentication_failure(),
            "404": _not_found(singular),
            "409": _conflict(),
            "428": _precondition_required(),
        },
    }


def build_document():
    schemas = {
        "Error": ERROR_SCHEMA,
        "ParameterError": PARAMETER_ERROR_SCHEMA,
        "FieldError": FIELD_ERROR_SCHEMA,
    }
    paths = {
        "/": {"get": _discovery_operation()},
        "/schema/": {"get": _schema_operation()},
    }
    for name, list_view, detail_view, serializer_class in ENDPOINTS:
        schemas[name] = object_schema(serializer_class)
        schemas[f"{name}Page"] = paginated_schema(name)
        write_serializer = WRITABLE.get(name)
        if write_serializer is not None:
            schemas[f"{name}Write"] = object_schema(write_serializer, all_required=False)
        list_operations = {"get": _list_operation(name, list_view)}
        if write_serializer is not None:
            list_operations["post"] = _create_operation(name)
        paths[f"/{name}/"] = list_operations
        if detail_view is not None:
            detail_operations = {"get": _detail_operation(name)}
            if write_serializer is not None:
                detail_operations["put"] = _update_operation(name, "put")
                detail_operations["patch"] = _update_operation(name, "patch")
                detail_operations["delete"] = _delete_operation(name)
            paths[f"/{name}/{{pk}}/"] = detail_operations
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "GrowBook garden API",
            "version": "1.0.0",
            "summary": "Read-only, paginated access to the garden registers, plantings and diary.",
            "description": (
                "Every request requires a bearer token issued to the account that owns it, "
                "except this schema. Text is returned exactly as it was stored, and a null "
                "value means it was never recorded. Writes go through the same validated "
                "operations as the interface, with If-Match optimistic locking; evaluations "
                "are read-only. A browsable rendering of this document is served at /api/docs/."
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
