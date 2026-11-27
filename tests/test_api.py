"""Read-only version 1 API: authentication, filters, field shape and documentation."""

import io
import json
import re
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from api import schema
from core.api_tokens import issue_token, revoke_token, secret_hash
from core.writes import validated_write
from diary.actions import PlantingEventAction
from diary.services import create_entry
from garden.models import Crop, Planting
from garden.services import (
    archive_area,
    archive_crop,
    archive_variety,
    create_area,
    create_category,
    create_crop,
    create_planting,
    create_planting_event,
    create_variety,
)

pytestmark = pytest.mark.django_db(transaction=True)

API = "/api/v1"
DOCUMENT_PATH = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"
LISTS = (
    "crops",
    "varieties",
    "areas",
    "plantings",
    "events",
    "evaluations",
    "observations",
)
DETAILS = ("crops", "varieties", "areas", "plantings", "events", "observations")


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def read(client, token, path, **params):
    return client.get(path, params or None, headers=bearer(token))


def identifiers(response):
    return [record["id"] for record in response.json()["results"]]


def evaluated(response):
    return [record["planting"] for record in response.json()["results"]]


@pytest.fixture
def token(user):
    _, secret = issue_token(actor=user, name="Synthetic client")
    return secret


@pytest.fixture
def staff_token(staff_user):
    _, secret = issue_token(actor=staff_user, name="Synthetic staff client")
    return secret


@pytest.fixture
def garden(user):
    """One synthetic garden: archived registers, an area tree, cycles and diary entries."""
    tomato = create_crop(actor=user, name="Tomato", notes="Line one\nLine two & <b>bold</b>").record
    bean = create_crop(actor=user, name="Bean").record
    retired_crop = create_crop(actor=user, name="Retired crop").record
    archive_crop(actor=user, pk=retired_crop.pk)
    category = create_category(actor=user, name="Nightshade", crops=[tomato]).record

    golden = create_variety(
        actor=user, crop=tomato, name="Golden sunrise", source="Seed swap"
    ).record
    cherry = create_variety(actor=user, crop=tomato, name="Cherry red").record
    blue = create_variety(actor=user, crop=bean, name="Blue lake").record
    retired_variety = create_variety(actor=user, crop=bean, name="Retired variety").record
    archive_variety(actor=user, pk=retired_variety.pk)

    greenhouse = create_area(
        actor=user,
        name="Greenhouse",
        area_type="greenhouse",
        length_cm=1200,
        width_cm=400,
        description="",
        notes="",
    ).record
    bed = create_area(actor=user, name="Bed one", parent=greenhouse, area_type="bed").record
    border = create_area(actor=user, name="Border", area_type="border").record
    retired_area = create_area(actor=user, name="Retired area").record
    archive_area(actor=user, pk=retired_area.pk)

    annual = create_planting(
        actor=user,
        crop=tomato,
        start_year=2026,
        variety=golden,
        area=bed,
        label="",
        notes="Water daily",
        evaluation="",
    ).record
    persistent = create_planting(
        actor=user,
        crop=tomato,
        start_year=2024,
        lifespan="persistent",
        end_year=2026,
        variety=cherry,
        area=border,
        label="Old vine",
        quantity=0,
    ).record
    unassigned = create_planting(
        actor=user,
        crop=bean,
        start_year=2025,
        lifespan="persistent",
        variety=blue,
        quantity=None,
    ).record
    completed = create_planting(
        actor=user,
        crop=bean,
        start_year=2025,
        area=border,
        status="completed",
        started_on=date(2025, 5, 1),
        completed_on=date(2025, 9, 1),
        label="Synthetic trial",
        evaluation="Good yield",
    ).record
    ongoing = create_planting(
        actor=user,
        crop=bean,
        start_year=2026,
        status="ongoing",
        started_on=date(2026, 4, 1),
    ).record
    # New plantings get a derived label, and a stored whitespace-only evaluation is
    # not a result; both are reachable stored states the API must report unchanged.
    with validated_write():
        Planting.objects.filter(pk=annual.pk).update(label="")
        Planting.objects.filter(pk=ongoing.pk).update(evaluation="   ")

    sowing = create_planting_event(
        actor=user,
        planting=annual,
        kind="sowing",
        planned_date=date(2026, 3, 1),
        actual_date=date(2026, 3, 4),
        note="Sown indoors",
    ).record
    harvest = create_planting_event(
        actor=user, planting=persistent, kind="harvest_start", actual_date=date(2026, 7, 1)
    ).record
    closing = create_planting_event(
        actor=user, planting=completed, kind="harvest_end", actual_date=date(2025, 9, 20)
    ).record

    noted = create_entry(
        author=user,
        date=date(2026, 3, 5),
        title="Sowing day",
        body="Pricked out",
        uploads=[],
        submission_key=uuid4(),
        links=[bed, golden, tomato, annual],
        show_in_calendar=True,
    ).entry
    quiet = create_entry(
        author=user,
        date=date(2025, 8, 9),
        body="Quiet note",
        uploads=[],
        submission_key=uuid4(),
    ).entry
    harvest_note = create_entry(
        author=user,
        date=date(2026, 7, 2),
        body="Harvest started",
        uploads=[],
        submission_key=uuid4(),
        links=[border, bean],
    ).entry
    event_entry = create_entry(
        author=user,
        date=date(2026, 3, 4),
        body="Moved the seedlings",
        uploads=[],
        submission_key=uuid4(),
        action=PlantingEventAction(
            planting=annual.pk, kind="transplant", actual_date=date(2026, 3, 4)
        ),
    ).entry
    event_entry.refresh_from_db()

    return {
        "tomato": tomato,
        "bean": bean,
        "retired_crop": retired_crop,
        "category": category,
        "golden": golden,
        "cherry": cherry,
        "blue": blue,
        "retired_variety": retired_variety,
        "greenhouse": greenhouse,
        "bed": bed,
        "border": border,
        "retired_area": retired_area,
        "annual": annual,
        "persistent": persistent,
        "unassigned": unassigned,
        "completed": completed,
        "ongoing": ongoing,
        "sowing": sowing,
        "harvest": harvest,
        "closing": closing,
        "noted": noted,
        "quiet": quiet,
        "harvest_note": harvest_note,
        "event_entry": event_entry,
    }


# Authentication


@pytest.mark.parametrize("collection", LISTS)
def test_missing_credential_is_a_json_401_not_a_login_redirect(client, collection):
    response = client.get(f"{API}/{collection}/")

    assert response.status_code == 401
    assert response["Content-Type"].startswith("application/json")
    assert "Location" not in response
    assert response.json()["detail"]


def test_every_api_view_is_wrapped_as_login_not_required():
    from api import urls

    for pattern in urls.urlpatterns:
        assert getattr(pattern.callback, "login_required", None) is False


@pytest.mark.parametrize(
    ("label", "header"),
    (
        ("scheme without a secret", "Bearer"),
        ("scheme with only spaces", "Bearer     "),
        ("another scheme", "Token synthetic-secret"),
        ("basic scheme", "Basic Z2FyZGVuZXI6U3ludGhldGljLXBhc3N3b3JkLTQy"),
        ("unknown secret", "Bearer no-such-credential-was-ever-issued"),
    ),
)
def test_unusable_authorization_header_is_refused(client, user, token, label, header):
    response = client.get(f"{API}/crops/", headers={"Authorization": header})

    assert response.status_code == 401, label
    assert response["Content-Type"].startswith("application/json"), label
    assert response.json()["detail"], label


def test_a_secret_matching_only_the_lookup_prefix_is_refused(client, token):
    # The stored prefix narrows the candidates; the secret hash decides.
    response = read(client, token[:8], f"{API}/crops/")

    assert response.status_code == 401
    assert response.json()["detail"]


def test_revoked_credential_is_refused_immediately(client, user, token):
    record = user.api_tokens.get(name="Synthetic client")
    assert read(client, token, f"{API}/crops/").status_code == 200

    revoke_token(actor=user, token=record)

    response = read(client, token, f"{API}/crops/")
    assert response.status_code == 401
    assert response.json()["detail"]


def test_expired_credential_is_refused(client, user, token):
    # issue_token refuses a past expiry, so an elapsed credential is a stored state.
    with validated_write():
        user.api_tokens.update(expires_at=timezone.now() - timedelta(minutes=1))

    response = read(client, token, f"{API}/crops/")

    assert response.status_code == 401
    assert response.json()["detail"]


def test_one_expired_credential_leaves_another_working(client, user, token):
    _, second = issue_token(actor=user, name="Synthetic second client")

    with validated_write():
        user.api_tokens.filter(name="Synthetic second client").update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )

    assert read(client, second, f"{API}/crops/").status_code == 401
    assert read(client, token, f"{API}/crops/").status_code == 200


def test_credential_for_an_inactive_account_is_refused(client, user, token):
    user.is_active = False
    user.save(update_fields=["is_active"])

    response = read(client, token, f"{API}/crops/")

    assert response.status_code == 401
    assert response.json()["detail"]


def test_a_second_credential_needs_a_different_name(user):
    issue_token(actor=user, name="Synthetic client")

    with pytest.raises(ValidationError):
        issue_token(actor=user, name="Synthetic client")

    record, secret = issue_token(actor=user, name="Synthetic other client")

    assert secret not in record.key_hash
    assert record.key_hash == secret_hash(secret)


def test_a_bearer_header_never_authenticates_a_browser_page(client, garden, token):
    response = client.get(reverse("garden:planting-list"), headers=bearer(token))

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("login"))


def test_a_valid_credential_reads_a_collection(client, garden, token):
    response = read(client, token, f"{API}/crops/")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/json")
    assert response.json()["count"] == 3


# Discovery document


def test_discovery_document_names_the_version_authentication_and_every_collection(client, token):
    response = read(client, token, f"{API}/")

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == "v1"
    assert body["authentication"] == "Bearer <token>"
    assert "documentation" not in body
    assert list(body["resources"]) == list(LISTS)
    for name, url in body["resources"].items():
        assert url == f"http://testserver{API}/{name}/"
        assert read(client, token, url).status_code == 200


# Field shape, nested records and source links


def test_crop_exposes_its_categories_and_register_page(client, garden, token):
    crop = garden["tomato"]

    record = read(client, token, f"{API}/crops/{crop.pk}/").json()

    assert list(record) == [
        "id",
        "name",
        "notes",
        "archived",
        "categories",
        "created_at",
        "updated_at",
        "url",
    ]
    assert record["name"] == "Tomato"
    assert record["notes"] == "Line one\nLine two & <b>bold</b>"
    assert record["archived"] is False
    assert record["categories"] == [
        {
            "id": garden["category"].pk,
            "name": "Nightshade",
            "url": f"http://testserver/garden/categories/{garden['category'].pk}/",
        }
    ]
    assert record["url"] == f"http://testserver/garden/crops/{crop.pk}/"


def test_variety_nests_its_crop_and_links_its_page(client, garden, token):
    variety = garden["golden"]

    record = read(client, token, f"{API}/varieties/{variety.pk}/").json()

    assert list(record) == [
        "id",
        "crop",
        "name",
        "description",
        "source",
        "experiences",
        "archived",
        "created_at",
        "updated_at",
        "url",
    ]
    assert record["crop"] == {
        "id": garden["tomato"].pk,
        "name": "Tomato",
        "url": f"http://testserver/garden/crops/{garden['tomato'].pk}/",
    }
    assert record["source"] == "Seed swap"
    assert record["experiences"] == ""
    assert record["url"] == f"http://testserver/garden/varieties/{variety.pk}/"


def test_area_nests_its_parent_and_keeps_unmeasured_dimensions_null(client, garden, token):
    parent = read(client, token, f"{API}/areas/{garden['greenhouse'].pk}/").json()
    child = read(client, token, f"{API}/areas/{garden['bed'].pk}/").json()

    assert list(child) == [
        "id",
        "parent",
        "name",
        "area_type",
        "length_cm",
        "width_cm",
        "description",
        "notes",
        "archived",
        "created_at",
        "updated_at",
        "url",
    ]
    assert parent["parent"] is None
    assert parent["length_cm"] == 1200
    assert parent["width_cm"] == 400
    assert child["length_cm"] is None
    assert child["width_cm"] is None
    assert child["parent"] == {
        "id": garden["greenhouse"].pk,
        "name": "Greenhouse",
        "url": f"http://testserver/garden/areas/{garden['greenhouse'].pk}/",
    }
    assert child["url"] == f"http://testserver/garden/areas/{garden['bed'].pk}/"


def test_planting_nests_cycle_crop_variety_and_area(client, garden, token):
    planting = garden["persistent"]

    record = read(client, token, f"{API}/plantings/{planting.pk}/").json()

    assert list(record) == [
        "id",
        "cycle",
        "start_year",
        "lifespan",
        "end_year",
        "crop",
        "variety",
        "area",
        "label",
        "quantity",
        "notes",
        "evaluation",
        "created_at",
        "updated_at",
        "url",
    ]
    assert record["cycle"] == {
        "id": str(planting.cycle_id),
        "status": "planned",
        "started_on": None,
        "completed_on": None,
    }
    assert record["lifespan"] == "persistent"
    assert record["end_year"] == 2026
    assert record["crop"]["name"] == "Tomato"
    assert record["variety"]["name"] == "Cherry red"
    assert record["area"]["name"] == "Border"
    assert record["label"] == "Old vine"
    assert record["url"] == f"http://testserver/garden/plantings/{planting.pk}/"


def test_annual_planting_has_no_end_year_and_unset_relations_are_null(client, garden, token):
    annual = read(client, token, f"{API}/plantings/{garden['annual'].pk}/").json()
    unassigned = read(client, token, f"{API}/plantings/{garden['unassigned'].pk}/").json()

    assert annual["lifespan"] == "annual"
    assert annual["end_year"] is None
    assert unassigned["area"] is None
    assert unassigned["end_year"] is None
    assert unassigned["variety"]["name"] == "Blue lake"


def test_event_links_its_planting_page_and_the_event_anchor(client, garden, token):
    event = garden["sowing"]

    record = read(client, token, f"{API}/events/{event.pk}/").json()

    assert list(record) == [
        "id",
        "planting",
        "planting_url",
        "kind",
        "planned_date",
        "actual_date",
        "note",
        "diary_entry",
        "created_at",
        "updated_at",
        "url",
    ]
    assert record["planting"] == garden["annual"].pk
    assert record["planting_url"] == (f"http://testserver/garden/plantings/{garden['annual'].pk}/")
    assert record["kind"] == "sowing"
    assert record["planned_date"] == "2026-03-01"
    assert record["actual_date"] == "2026-03-04"
    assert record["diary_entry"] is None
    assert record["url"] == (
        f"http://testserver/garden/plantings/{garden['annual'].pk}/#event-{event.pk}"
    )


def test_event_reports_the_diary_entry_created_with_it(client, garden, token):
    entry = garden["event_entry"]
    event = entry.event

    record = read(client, token, f"{API}/events/{event.pk}/").json()

    assert record["diary_entry"] == entry.pk
    assert record["kind"] == "transplant"
    assert record["note"] == ""


def test_evaluation_keeps_its_planting_cycle_and_stored_result(client, garden, token):
    planting = garden["completed"]

    response = read(client, token, f"{API}/evaluations/", cycle=planting.cycle_id)

    assert response.status_code == 200
    assert response.json()["count"] == 1
    record = response.json()["results"][0]
    assert list(record) == [
        "planting",
        "cycle",
        "cycle_status",
        "start_year",
        "lifespan",
        "end_year",
        "crop",
        "variety",
        "area",
        "label",
        "evaluation",
        "updated_at",
        "url",
    ]
    assert record["planting"] == planting.pk
    assert record["cycle"] == str(planting.cycle_id)
    assert record["cycle_status"] == "completed"
    assert record["lifespan"] == "annual"
    assert record["end_year"] is None
    assert record["variety"] is None
    assert record["area"]["name"] == "Border"
    assert record["label"] == "Synthetic trial"
    assert record["evaluation"] == "Good yield"
    assert record["url"] == f"http://testserver/garden/plantings/{planting.pk}/"


def test_observation_links_its_targets_and_its_diary_page(client, garden, token):
    entry = garden["noted"]

    record = read(client, token, f"{API}/observations/{entry.pk}/").json()

    assert list(record) == [
        "id",
        "date",
        "title",
        "body",
        "show_in_calendar",
        "event",
        "links",
        "created_at",
        "updated_at",
        "url",
    ]
    assert record["date"] == "2026-03-05"
    assert record["title"] == "Sowing day"
    assert record["body"] == "Pricked out"
    assert record["show_in_calendar"] is True
    assert record["event"] is None
    assert record["links"] == [
        {"type": "area", "id": garden["bed"].pk},
        {"type": "crop", "id": garden["tomato"].pk},
        {"type": "variety", "id": garden["golden"].pk},
        {"type": "planting", "id": garden["annual"].pk},
    ]
    assert record["url"] == f"http://testserver/entries/{entry.pk}/"


def test_observation_reports_the_event_it_was_captured_with(client, garden, token):
    entry = garden["event_entry"]

    record = read(client, token, f"{API}/observations/{entry.pk}/").json()

    assert record["event"] == entry.event_id
    assert {"type": "planting", "id": garden["annual"].pk} in record["links"]


def test_no_record_exposes_photos_author_or_submission_keys(client, garden, token):
    forbidden = {"photos", "author", "submission_key", "name_key", "search_text"}

    def keys(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from keys(item)
        elif isinstance(value, list):
            for item in value:
                yield from keys(item)

    for collection in LISTS:
        for record in read(client, token, f"{API}/{collection}/").json()["results"]:
            assert not (set(keys(record)) & forbidden), collection


# Stored values


def test_empty_text_and_unrecorded_quantity_are_preserved(client, garden, token):
    annual = read(client, token, f"{API}/plantings/{garden['annual'].pk}/").json()
    persistent = read(client, token, f"{API}/plantings/{garden['persistent'].pk}/").json()
    unassigned = read(client, token, f"{API}/plantings/{garden['unassigned'].pk}/").json()

    assert annual["label"] == ""
    assert annual["evaluation"] == ""
    assert annual["notes"] == "Water daily"
    assert persistent["quantity"] == 0
    assert unassigned["quantity"] is None


def test_stored_text_is_returned_unchanged(client, garden, token):
    record = read(client, token, f"{API}/crops/{garden['tomato'].pk}/").json()

    garden["tomato"].refresh_from_db()
    assert record["notes"] == garden["tomato"].notes
    assert "<b>" in record["notes"]
    assert "\n" in record["notes"]


# Missing records and refused methods


@pytest.mark.parametrize("collection", DETAILS)
def test_unknown_detail_is_a_json_404(client, token, collection):
    response = read(client, token, f"{API}/{collection}/999999/")

    assert response.status_code == 404
    assert response["Content-Type"].startswith("application/json")
    assert response.json()["detail"]


def test_evaluations_are_read_only_and_refuse_writes(client, garden, token):
    url = f"{API}/evaluations/"
    before = Planting.objects.count()

    for method in (client.post, client.put, client.patch, client.delete):
        response = method(url, {}, content_type="application/json", headers=bearer(token))
        assert response.status_code == 405, method
        assert response["Content-Type"].startswith("application/json")
        assert response.json()["detail"]

    assert Planting.objects.count() == before


def test_head_and_options_are_answered_on_a_list_and_a_detail(client, garden, token):
    listing = client.head(f"{API}/crops/", headers=bearer(token))
    detail = client.options(f"{API}/crops/{garden['tomato'].pk}/", headers=bearer(token))

    assert listing.status_code == 200
    assert detail.status_code == 200
    assert "GET" in detail["Allow"]


def test_detail_ignores_filters(client, garden, token):
    response = read(
        client, token, f"{API}/crops/{garden['tomato'].pk}/", archived="true", search="zzz"
    )

    assert response.status_code == 200
    assert response.json()["id"] == garden["tomato"].pk


# Pagination


@pytest.fixture
def many_crops(user):
    with validated_write():
        Crop.objects.bulk_create(
            [
                Crop(name=f"Bulk crop {index:03d}", name_key=f"bulk crop {index:03d}")
                for index in range(202)
            ]
        )
    return Crop.objects.count()


def test_list_defaults_to_the_configured_page_size(client, token, many_crops):
    body = read(client, token, f"{API}/crops/").json()

    assert body["count"] == many_crops
    assert len(body["results"]) == 50
    assert body["previous"] is None
    assert body["next"].endswith("page=2")


def test_page_size_is_bounded_by_the_documented_maximum(client, token, many_crops):
    body = read(client, token, f"{API}/crops/", page_size=1000).json()

    assert body["count"] == many_crops
    assert len(body["results"]) == 200
    assert body["next"] is not None


def test_second_page_continues_without_repeating_records(client, garden, token):
    first = read(client, token, f"{API}/crops/", page_size=2).json()
    second = read(client, token, first["next"]).json()

    assert len(first["results"]) == 2
    assert second["previous"].endswith("page_size=2")
    assert not {record["id"] for record in first["results"]} & {
        record["id"] for record in second["results"]
    }


# Filters


def test_crop_filters(client, garden, token):
    assert identifiers(read(client, token, f"{API}/crops/", archived="true")) == [
        garden["retired_crop"].pk
    ]
    assert set(identifiers(read(client, token, f"{API}/crops/", archived="false"))) == {
        garden["tomato"].pk,
        garden["bean"].pk,
    }
    assert identifiers(read(client, token, f"{API}/crops/", category=garden["category"].pk)) == [
        garden["tomato"].pk
    ]
    assert identifiers(read(client, token, f"{API}/crops/", search="TOM")) == [garden["tomato"].pk]
    assert read(client, token, f"{API}/crops/", unknown="ignored").status_code == 200
    assert read(client, token, f"{API}/crops/", unknown="ignored").json()["count"] == 3


def test_variety_filters(client, garden, token):
    assert set(identifiers(read(client, token, f"{API}/varieties/", crop=garden["tomato"].pk))) == {
        garden["golden"].pk,
        garden["cherry"].pk,
    }
    assert identifiers(read(client, token, f"{API}/varieties/", archived="true")) == [
        garden["retired_variety"].pk
    ]
    assert identifiers(read(client, token, f"{API}/varieties/", search="golden")) == [
        garden["golden"].pk
    ]


def test_area_filters(client, garden, token):
    assert set(identifiers(read(client, token, f"{API}/areas/", parent="none"))) == {
        garden["greenhouse"].pk,
        garden["border"].pk,
        garden["retired_area"].pk,
    }
    assert identifiers(read(client, token, f"{API}/areas/", parent=garden["greenhouse"].pk)) == [
        garden["bed"].pk
    ]
    assert identifiers(read(client, token, f"{API}/areas/", archived="true")) == [
        garden["retired_area"].pk
    ]
    assert identifiers(read(client, token, f"{API}/areas/", area_type="bed")) == [garden["bed"].pk]
    assert identifiers(read(client, token, f"{API}/areas/", area_type="bedroom")) == []
    assert identifiers(read(client, token, f"{API}/areas/", search="green")) == [
        garden["greenhouse"].pk
    ]


def test_planting_filters(client, garden, token):
    year = set(identifiers(read(client, token, f"{API}/plantings/", year=2026)))
    assert year == {
        garden["annual"].pk,
        garden["persistent"].pk,
        garden["unassigned"].pk,
        garden["ongoing"].pk,
    }
    assert set(identifiers(read(client, token, f"{API}/plantings/", crop=garden["tomato"].pk))) == {
        garden["annual"].pk,
        garden["persistent"].pk,
    }
    assert identifiers(read(client, token, f"{API}/plantings/", variety=garden["cherry"].pk)) == [
        garden["persistent"].pk
    ]
    descendants = set(
        identifiers(read(client, token, f"{API}/plantings/", area=garden["greenhouse"].pk))
    )
    assert descendants == {garden["annual"].pk}
    assert identifiers(read(client, token, f"{API}/plantings/", area=garden["border"].pk)) == [
        garden["completed"].pk,
        garden["persistent"].pk,
    ]
    assert identifiers(
        read(client, token, f"{API}/plantings/", cycle=garden["annual"].cycle_id)
    ) == [garden["annual"].pk]
    assert identifiers(read(client, token, f"{API}/plantings/", status="completed")) == [
        garden["completed"].pk
    ]
    assert identifiers(read(client, token, f"{API}/plantings/", status="ongoing")) == [
        garden["ongoing"].pk
    ]
    assert set(identifiers(read(client, token, f"{API}/plantings/", lifespan="persistent"))) == {
        garden["persistent"].pk,
        garden["unassigned"].pk,
    }


def test_event_filters(client, garden, token):
    annual_events = identifiers(read(client, token, f"{API}/events/", planting=garden["annual"].pk))
    assert set(annual_events) == {
        garden["sowing"].pk,
        garden["event_entry"].event_id,
    }
    assert identifiers(read(client, token, f"{API}/events/", kind="sowing")) == [
        garden["sowing"].pk
    ]
    assert set(identifiers(read(client, token, f"{API}/events/", year=2026))) == {
        garden["sowing"].pk,
        garden["harvest"].pk,
        garden["event_entry"].event_id,
    }
    assert identifiers(read(client, token, f"{API}/events/", crop=garden["bean"].pk)) == [
        garden["closing"].pk
    ]
    assert set(
        identifiers(read(client, token, f"{API}/events/", area=garden["greenhouse"].pk))
    ) == {garden["sowing"].pk, garden["event_entry"].event_id}
    assert set(identifiers(read(client, token, f"{API}/events/", area=garden["border"].pk))) == {
        garden["harvest"].pk,
        garden["closing"].pk,
    }


def test_evaluation_filters_and_blank_results_are_omitted(client, garden, token):
    body = read(client, token, f"{API}/evaluations/").json()

    assert [record["planting"] for record in body["results"]] == [garden["completed"].pk]
    assert evaluated(
        read(client, token, f"{API}/evaluations/", cycle=garden["completed"].cycle_id)
    ) == [garden["completed"].pk]
    assert evaluated(read(client, token, f"{API}/evaluations/", crop=garden["bean"].pk)) == [
        garden["completed"].pk
    ]
    assert evaluated(read(client, token, f"{API}/evaluations/", crop=garden["tomato"].pk)) == []
    assert evaluated(read(client, token, f"{API}/evaluations/", variety=garden["golden"].pk)) == []
    assert evaluated(read(client, token, f"{API}/evaluations/", area=garden["border"].pk)) == [
        garden["completed"].pk
    ]
    assert evaluated(read(client, token, f"{API}/evaluations/", area=garden["greenhouse"].pk)) == []
    assert evaluated(read(client, token, f"{API}/evaluations/", year=2025)) == [
        garden["completed"].pk
    ]
    assert evaluated(read(client, token, f"{API}/evaluations/", year=2026)) == []


def test_observation_filters(client, garden, token):
    noted, quiet, harvest_note = garden["noted"], garden["quiet"], garden["harvest_note"]
    event_entry = garden["event_entry"]

    assert identifiers(read(client, token, f"{API}/observations/", **{"from": "2026-03-04"})) == [
        harvest_note.pk,
        noted.pk,
        event_entry.pk,
    ]
    assert identifiers(read(client, token, f"{API}/observations/", to="2025-12-31")) == [quiet.pk]
    assert set(identifiers(read(client, token, f"{API}/observations/", year=2026))) == {
        noted.pk,
        harvest_note.pk,
        event_entry.pk,
    }
    assert set(
        identifiers(read(client, token, f"{API}/observations/", area=garden["greenhouse"].pk))
    ) == {noted.pk, event_entry.pk}
    assert set(
        identifiers(read(client, token, f"{API}/observations/", crop=garden["tomato"].pk))
    ) == {noted.pk, event_entry.pk}
    assert set(
        identifiers(read(client, token, f"{API}/observations/", variety=garden["golden"].pk))
    ) == {noted.pk, event_entry.pk}
    assert set(
        identifiers(read(client, token, f"{API}/observations/", planting=garden["annual"].pk))
    ) == {noted.pk, event_entry.pk}
    assert identifiers(read(client, token, f"{API}/observations/", calendar="true")) == [noted.pk]
    assert set(identifiers(read(client, token, f"{API}/observations/", calendar="false"))) == {
        quiet.pk,
        harvest_note.pk,
        event_entry.pk,
    }
    assert identifiers(read(client, token, f"{API}/observations/", q="pricked")) == [noted.pk]
    assert identifiers(read(client, token, f"{API}/observations/", q="Sowing day")) == [noted.pk]
    assert identifiers(read(client, token, f"{API}/observations/", q="no such text")) == []


def test_observation_filter_for_a_missing_record_returns_an_empty_page(client, garden, token):
    body = read(client, token, f"{API}/observations/", planting=999999).json()

    assert body["count"] == 0
    assert body["results"] == []


@pytest.mark.parametrize(
    ("collection", "parameter", "value"),
    (
        ("crops", "archived", "maybe"),
        ("crops", "category", "abc"),
        ("varieties", "crop", "0"),
        ("areas", "parent", "abc"),
        ("plantings", "year", "0"),
        ("plantings", "year", "10000"),
        ("plantings", "cycle", "not-a-uuid"),
        ("plantings", "status", "archived"),
        ("plantings", "lifespan", "everlasting"),
        ("events", "kind", "weeding"),
        ("events", "year", "abc"),
        ("evaluations", "cycle", "123"),
        ("observations", "from", "2026-13-40"),
        ("observations", "calendar", "yes"),
        ("observations", "planting", "first"),
    ),
)
def test_invalid_filter_value_names_the_parameter(client, token, collection, parameter, value):
    response = read(client, token, f"{API}/{collection}/", **{parameter: value})

    assert response.status_code == 400
    assert response["Content-Type"].startswith("application/json")
    assert response.json()["parameter"] == parameter
    assert response.json()["detail"]


# Shared data and query count


def test_staff_and_non_staff_owners_read_the_same_garden(client, garden, token, staff_token):
    as_owner = read(client, token, f"{API}/plantings/").json()
    as_staff = read(client, staff_token, f"{API}/plantings/").json()

    assert as_owner["count"] == as_staff["count"] == 5
    assert [record["id"] for record in as_owner["results"]] == [
        record["id"] for record in as_staff["results"]
    ]


def query_count(client, token, path, **params):
    with CaptureQueriesContext(connection) as captured:
        response = read(client, token, path, **params)
    assert response.status_code == 200
    return len(captured)


def test_plantings_list_does_not_fan_out_queries(client, garden, token, django_assert_num_queries):
    with django_assert_num_queries(3):
        response = read(client, token, f"{API}/plantings/")
    assert response.status_code == 200
    few = query_count(client, token, f"{API}/plantings/", variety=garden["cherry"].pk)
    many = query_count(client, token, f"{API}/plantings/")

    assert many == few


def test_observations_list_does_not_fan_out_queries(
    client, garden, token, django_assert_num_queries
):
    with django_assert_num_queries(4):
        response = read(client, token, f"{API}/observations/")
    assert response.status_code == 200
    few = query_count(client, token, f"{API}/observations/", q="pricked")
    many = query_count(client, token, f"{API}/observations/")

    assert many == few


# Documentation


def document_on_disk():
    return json.loads(DOCUMENT_PATH.read_text(encoding="utf-8"))


def test_committed_openapi_document_matches_generated_output():
    buffer = io.StringIO()

    call_command("api_schema", stdout=buffer)

    assert buffer.getvalue() == DOCUMENT_PATH.read_text(encoding="utf-8")


def test_api_schema_output_argument_writes_the_same_document(tmp_path):
    target = tmp_path / "openapi.json"

    call_command("api_schema", "--output", str(target))

    assert target.read_text(encoding="utf-8") == DOCUMENT_PATH.read_text(encoding="utf-8")


def urlconf_paths():
    from api import urls

    paths = set()
    for pattern in urls.urlpatterns:
        route = str(pattern.pattern)
        paths.add("/" + re.sub(r"<(?:int|uuid|str):(\w+)>", r"{\1}", route))
    return paths


def test_every_api_route_appears_in_the_document():
    assert set(document_on_disk()["paths"]) == urlconf_paths()


def test_document_describes_every_supported_filter():
    paths = document_on_disk()["paths"]

    for name, list_view, _, _ in schema.ENDPOINTS:
        documented = {parameter["name"] for parameter in paths[f"/{name}/"]["get"]["parameters"]}
        assert documented == {parameter.name for parameter in list_view.filters} | {
            "page",
            "page_size",
        }


def test_document_lists_the_serialized_fields_in_order():
    schemas = document_on_disk()["components"]["schemas"]

    for name, _, _, serializer_class in schema.ENDPOINTS:
        assert list(schemas[name]["properties"]) == list(serializer_class().fields)


def test_document_declares_bearer_authentication_for_every_operation():
    document = document_on_disk()

    assert document["openapi"] == "3.1.0"
    assert document["security"] == [{"bearerAuth": []}]
    assert document["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"
    expected = {"/": ["get"], "/schema/": ["get"]}
    for name, _, detail_view, _ in schema.ENDPOINTS:
        expected[f"/{name}/"] = ["get", "post"] if name in schema.WRITABLE else ["get"]
        if detail_view is not None:
            expected[f"/{name}/{{pk}}/"] = (
                ["get", "put", "patch", "delete"] if name in schema.WRITABLE else ["get"]
            )
    assert {path: list(operations) for path, operations in document["paths"].items()} == expected


def test_search_folds_case_beyond_ascii_like_the_interface(client, user, token):
    crop = create_crop(actor=user, name="Ärtor").record
    variety = create_variety(actor=user, crop=crop, name="Örtagård").record
    area = create_area(actor=user, name="Södra bäddet").record

    assert identifiers(read(client, token, f"{API}/crops/", search="ärtor")) == [crop.pk]
    assert identifiers(read(client, token, f"{API}/crops/", search="ÄRTOR")) == [crop.pk]
    assert identifiers(read(client, token, f"{API}/varieties/", search="örtagård")) == [variety.pk]
    assert identifiers(read(client, token, f"{API}/areas/", search="södra")) == [area.pk]


def test_a_write_ignores_an_unusable_filter(client, token):
    # Filters apply to reads only; a write is never refused over a filter it does not read.
    response = client.post(
        f"{API}/crops/?archived=nonsense",
        {"name": "Synthetic filter-ignored crop"},
        content_type="application/json",
        headers=bearer(token),
    )

    assert response.status_code == 201
    assert response.json()["name"] == "Synthetic filter-ignored crop"


def test_a_format_parameter_does_not_answer_before_authentication(client, token):
    anonymous = client.get(f"{API}/crops/?format=xml")
    assert anonymous.status_code == 401

    served = read(client, token, f"{API}/crops/", format="xml")
    assert served.status_code == 200
    assert served["Content-Type"].startswith("application/json")


@pytest.mark.parametrize("resource", ["crops", "varieties", "areas"])
def test_a_blank_search_is_ignored_rather_than_refused(client, garden, token, resource):
    unfiltered = read(client, token, f"{API}/{resource}/").json()["count"]
    assert unfiltered > 0

    for blank in ("", "   "):
        response = read(client, token, f"{API}/{resource}/", search=blank)
        assert response.status_code == 200, response.content
        assert response.json()["count"] == unfiltered


def test_a_blank_diary_search_is_ignored(client, garden, token):
    unfiltered = read(client, token, f"{API}/observations/").json()["count"]

    for blank in ("", "   "):
        response = read(client, token, f"{API}/observations/", q=blank)
        assert response.status_code == 200, response.content
        assert response.json()["count"] == unfiltered


def test_a_blank_area_type_is_ignored_rather_than_meaning_no_type(client, garden, token):
    unfiltered = read(client, token, f"{API}/areas/").json()["count"]

    assert read(client, token, f"{API}/areas/", area_type="").json()["count"] == unfiltered
    assert read(client, token, f"{API}/areas/", area_type="bed").json()["count"] == 1


@pytest.mark.parametrize(
    ("collection", "parameter"),
    (
        ("crops", "archived"),
        ("crops", "category"),
        ("areas", "parent"),
        ("plantings", "year"),
        ("plantings", "status"),
        ("events", "kind"),
        ("observations", "from"),
    ),
)
def test_a_blank_typed_filter_is_ignored_rather_than_refused(
    client, garden, token, collection, parameter
):
    unfiltered = read(client, token, f"{API}/{collection}/").json()["count"]
    assert unfiltered > 0

    for blank in ("", "   "):
        response = read(client, token, f"{API}/{collection}/", **{parameter: blank})
        assert response.status_code == 200, response.content
        assert response.json()["count"] == unfiltered
