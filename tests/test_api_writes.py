"""Write operations of the version 1 API.

Every write goes through the same validated service operations the interface uses,
with If-Match optimistic locking on updates and deletes.
"""

import pytest

from core.api_tokens import issue_token
from diary.models import DiaryEntry
from garden.models import Crop, GrowingArea, Planting, PlantingEvent, Variety

pytestmark = pytest.mark.django_db(transaction=True)

API = "/api/v1"


@pytest.fixture
def token(user):
    _, secret = issue_token(actor=user, name="Synthetic writer")
    return secret


@pytest.fixture
def crop(user):
    from garden.services.registers import create_crop

    return create_crop(actor=user, name="Synthetic tomato").record


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def write(client, token, method, path, payload=None, if_match=None):
    headers = bearer(token)
    if if_match is not None:
        headers["If-Match"] = if_match
    return getattr(client, method)(
        path,
        payload if payload is not None else {},
        content_type="application/json",
        headers=headers,
    )


def version_of(client, token, path):
    response = client.get(path, headers=bearer(token))
    assert response.status_code == 200
    return response.json()["updated_at"]


# Crops: the smallest full create/update/delete cycle


def test_create_read_update_delete_a_crop(client, token):
    created = write(client, token, "post", f"{API}/crops/", {"name": "Synthetic API crop"})
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Synthetic API crop"
    assert body["archived"] is False
    pk = body["id"]

    detail = f"{API}/crops/{pk}/"
    assert client.get(detail, headers=bearer(token)).json()["name"] == "Synthetic API crop"

    updated = write(
        client,
        token,
        "patch",
        detail,
        {"notes": "edited via API"},
        if_match=version_of(client, token, detail),
    )
    assert updated.status_code == 200
    assert updated.json()["notes"] == "edited via API"
    assert updated.json()["name"] == "Synthetic API crop"

    archived = write(
        client,
        token,
        "patch",
        detail,
        {"archived": True},
        if_match=version_of(client, token, detail),
    )
    assert archived.status_code == 200
    assert archived.json()["archived"] is True
    assert Crop.objects.get(pk=pk).is_archived is True

    deleted = write(client, token, "delete", detail, if_match=version_of(client, token, detail))
    assert deleted.status_code == 204
    assert not Crop.objects.filter(pk=pk).exists()
    assert client.get(detail, headers=bearer(token)).status_code == 404


def test_update_and_delete_require_if_match(client, token):
    write(client, token, "post", f"{API}/crops/", {"name": "Synthetic versioned crop"})
    detail = f"{API}/crops/{Crop.objects.get(name='Synthetic versioned crop').pk}/"

    for method in ("put", "patch", "delete"):
        response = write(client, token, method, detail, {"name": "x"})
        assert response.status_code == 428, method
        assert response.json()["detail"]

    stale = write(
        client, token, "patch", detail, {"notes": "x"}, if_match="2020-01-01T00:00:00+01:00"
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]

    malformed = write(client, token, "patch", detail, {"notes": "x"}, if_match="not-a-date")
    assert malformed.status_code == 400


def test_write_to_an_unknown_record_is_a_json_404(client, token):
    for method in ("put", "patch", "delete"):
        response = write(
            client,
            token,
            method,
            f"{API}/crops/999999/",
            {"name": "x"},
            if_match="2026-01-01T00:00:00+01:00",
        )
        assert response.status_code == 404, method
        assert response.json()["detail"]


def test_invalid_and_duplicate_payloads_are_400_with_field_errors(client, crop, token):
    missing = write(client, token, "post", f"{API}/crops/", {})
    assert missing.status_code == 400
    assert "name" in missing.json()

    duplicate = write(client, token, "post", f"{API}/crops/", {"name": crop.name})
    assert duplicate.status_code == 400
    assert duplicate.json()["name"]


def test_writes_need_a_credential(client, crop):

    detail = f"{API}/crops/{crop.pk}/"
    assert write(client, None, "post", f"{API}/crops/", {"name": "Anonymous"}).status_code == 401
    for method in ("put", "patch", "delete"):
        response = getattr(client, method)(detail, {}, content_type="application/json")
        assert response.status_code == 401, method
    assert Crop.objects.filter(name="Anonymous").count() == 0


# The other writable collections


def test_variety_area_planting_event_and_observation_roundtrip(client, token, crop):

    variety = write(
        client,
        token,
        "post",
        f"{API}/varieties/",
        {"crop": crop.pk, "name": "Synthetic API variety"},
    )
    assert variety.status_code == 201
    variety_pk = variety.json()["id"]

    area = write(
        client, token, "post", f"{API}/areas/", {"name": "Synthetic API bed", "area_type": "bed"}
    )
    assert area.status_code == 201
    area_pk = area.json()["id"]

    planting = write(
        client,
        token,
        "post",
        f"{API}/plantings/",
        {
            "crop": crop.pk,
            "start_year": 2026,
            "variety": variety_pk,
            "area": area_pk,
            "quantity": 3,
        },
    )
    assert planting.status_code == 201
    planting_pk = planting.json()["id"]
    assert planting.json()["quantity"] == 3

    event = write(
        client,
        token,
        "post",
        f"{API}/events/",
        {
            "planting": planting_pk,
            "kind": "sowing",
            "planned_date": "2026-03-01",
            "note": "indoors",
        },
    )
    assert event.status_code == 201
    event_pk = event.json()["id"]

    observation = write(
        client,
        token,
        "post",
        f"{API}/observations/",
        {
            "date": "2026-11-27",
            "body": "Sown indoors.",
            "title": "Sowing",
            "links": [{"type": "crop", "id": crop.pk}, {"type": "planting", "id": planting_pk}],
        },
    )
    assert observation.status_code == 201
    observation_pk = observation.json()["id"]
    assert observation.json()["links"] == [
        {"type": "crop", "id": crop.pk},
        {"type": "planting", "id": planting_pk},
    ]

    # Each detail can be patched with its current version and then deleted, in
    # reverse dependency order: a still-referenced record is refused with 400.
    for collection, pk, model, patch in (
        ("observations", observation_pk, DiaryEntry, {"body": "Sown and covered."}),
        ("events", event_pk, PlantingEvent, {"note": "Done"}),
        ("plantings", planting_pk, Planting, {"notes": "Thinned"}),
        ("varieties", variety_pk, Variety, {"source": "Saved seed"}),
        ("areas", area_pk, GrowingArea, {"notes": "Covered"}),
    ):
        detail = f"{API}/{collection}/{pk}/"
        updated = write(
            client, token, "patch", detail, patch, if_match=version_of(client, token, detail)
        )
        assert updated.status_code == 200, collection
        assert list(patch.values())[0] in str(updated.json())
        deleted = write(client, token, "delete", detail, if_match=version_of(client, token, detail))
        assert deleted.status_code == 204, collection
        assert not model.objects.filter(pk=pk).exists(), collection


def test_evaluations_are_read_only(client, token):
    response = write(client, token, "post", f"{API}/evaluations/", {})
    assert response.status_code == 405
    assert response.json()["detail"]


# The document describes the writes


def test_document_describes_every_write_operation():
    import json

    from api.schema import WRITABLE, render_document

    document = json.loads(render_document())
    for name in WRITABLE:
        list_path = document["paths"][f"/{name}/"]
        assert "post" in list_path
        assert list_path["post"]["requestBody"]["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{name}Write"
        }
        assert f"{name}Write" in document["components"]["schemas"]
        detail_path = document["paths"][f"/{name}/{{pk}}/"]
        assert list(detail_path) == ["get", "put", "patch", "delete"]
        for method in ("put", "patch", "delete"):
            parameters = detail_path[method]["parameters"]
            assert any(p["name"] == "If-Match" and p["required"] for p in parameters)
    assert list(document["paths"]["/evaluations/"]) == ["get"]
