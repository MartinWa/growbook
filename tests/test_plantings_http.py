from datetime import date
from uuid import uuid4

import pytest
from django.db import OperationalError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import Photo
from garden.models import Planting, PlantingEvent, PlantingSubmission
from garden.services import (
    archive_area,
    archive_crop,
    archive_variety,
    create_area,
    create_crop,
    create_planting,
    create_planting_event,
    create_variety,
    edit_planting,
    edit_variety,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def catalog(user):
    crop = create_crop(actor=user, name="Tomato").record
    other_crop = create_crop(actor=user, name="Bean").record
    variety = create_variety(actor=user, crop=crop, name="Golden").record
    other_variety = create_variety(actor=user, crop=other_crop, name="Blue").record
    root = create_area(actor=user, name="Greenhouse").record
    child = create_area(actor=user, name="Bed", parent=root).record
    other_area = create_area(actor=user, name="Border").record
    record = create_planting(
        actor=user,
        crop=crop,
        start_year=2026,
        variety=variety,
        area=child,
        label="First batch",
        notes="Synthetic planning notes",
        evaluation="Synthetic results",
    ).record
    event = create_planting_event(
        actor=user,
        planting=record,
        kind="sowing",
        planned_date=date(2025, 12, 20),
        actual_date=date(2026, 1, 2),
        note="Synthetic event note",
    ).record
    record.refresh_from_db()
    return {
        "crop": crop,
        "other_crop": other_crop,
        "variety": variety,
        "other_variety": other_variety,
        "root": root,
        "child": child,
        "other_area": other_area,
        "planting": record,
        "event": event,
    }


def payload(catalog, **overrides):
    return {
        "submission_key": str(uuid4()),
        "crop": catalog["crop"].pk,
        "start_year": 2026,
        "lifespan": "annual",
        "variety": "",
        "area": "",
        "label": "New batch",
        "quantity": "",
        "end_year": "",
        "notes": "Synthetic draft",
        "evaluation": "",
        **overrides,
    }


def test_empty_plantings_offer_crop_then_prefilled_planting_creation(client, user):
    client.force_login(user)
    path = reverse("garden:planting-list")
    response = client.get(path, {"year": 2026})
    assert response.context["empty_action_url"] == reverse("garden:crop-create")
    crop = create_crop(actor=user, name="First crop").record
    response = client.get(path, {"year": 2026, "crop": crop.pk})
    assert response.context["empty_action_url"] == response.context["create_url"]
    form = client.get(response.context["empty_action_url"]).context["form"]
    assert str(form["crop"].value()) == str(crop.pk)
    assert str(form["start_year"].value()) == "2026"


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("kind", ["crop", "variety", "area"])
def test_empty_history_actions_clear_adjustable_filters_and_keep_scope(
    client, user, catalog, partial, kind
):
    client.force_login(user)
    scope = catalog["root" if kind == "area" else kind]
    path = reverse(f"garden:{kind}-history", args=[scope.pk])
    headers = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "planting-results"} if partial else {}
    for filters in ({"year": 2027}, {"year": "invalid"}, {"unassigned": "on"}):
        response = client.get(path, filters, **headers)
        assert not response.context["records"]
        assert response.context["empty_action_url"] == path
        assert b'class="panel empty-state"' in response.content
        restored = client.get(response.context["empty_action_url"], **headers)
        assert [row.pk for row in restored.context["records"]] == [catalog["planting"].pk]


def test_empty_scope_offers_creation_instead_of_clearing_to_the_same_empty_history(
    client, user, catalog
):
    client.force_login(user)
    path = reverse("garden:crop-history", args=[catalog["other_crop"].pk])
    response = client.get(path)
    assert not response.context["records"]
    assert response.context["empty_action_url"] == response.context["create_url"]
    assert "crop=" + str(catalog["other_crop"].pk) in response.context["empty_action_url"]


def test_empty_year_overview_links_to_all_years_without_adjustable_filters(client, user, catalog):
    client.force_login(user)
    response = client.get(reverse("garden:planting-list"), {"year": 2027})
    assert response.context["empty_action_url"] == reverse("garden:planting-history")
    assert client.get(response.context["empty_action_url"]).context["records"]


def test_planting_partial_preserves_scope_and_returns_filter_errors(client, user, catalog):
    client.force_login(user)
    headers = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "planting-results"}
    path = reverse("garden:crop-history", args=[catalog["crop"].pk])
    response = client.get(path, {"crop": catalog["other_crop"].pk, "year": 2026}, **headers)
    assert response.status_code == 200
    assert response.context["records"][0].pk == catalog["planting"].pk
    assert response.context["locked_filters"] == {"crop"}
    assert b'id="planting-results"' in response.content
    assert b"<html" not in response.content
    invalid = client.get(path, {"year": "invalid"}, **headers)
    assert invalid.status_code == 422
    assert invalid.context["form"].errors["year"]
    assert b'data-field-errors="year"' in invalid.content


def test_invalid_planting_filter_renders_inline_field_feedback(client, user):
    client.force_login(user)
    response = client.get(reverse("garden:planting-list"), {"year": 10000})
    assert response.status_code == 422
    assert 'id="id_year_errors"' in response.text
    assert 'data-filter-field-errors="year"' in response.text


def test_optional_planting_groups_open_for_invalid_and_populated_values(client, user, catalog):
    client.force_login(user)
    path = reverse("garden:planting-create")
    form = client.get(path).context["form"]
    assert not form.details_open and not form.notes_open
    response = client.post(path, payload(catalog, quantity="invalid", notes="Keep note"))
    assert response.status_code == 422
    form = response.context["form"]
    assert form.details_open and form.notes_open
    assert 'id="planting-more-details" open' in response.content.decode()


def event_payload(**overrides):
    return {
        "submission_key": str(uuid4()),
        "kind": "sowing",
        "planned_date": "2025-12-31",
        "actual_date": "",
        "note": "Synthetic milestone",
        **overrides,
    }


def edit_payload(catalog, record, **overrides):
    values = {
        "crop": record.crop_id,
        "variety": record.variety_id or "",
        "area": record.area_id or "",
        "label": record.label,
        "start_year": record.start_year,
        "lifespan": record.lifespan,
        "end_year": record.end_year or "",
        "quantity": record.quantity if record.quantity is not None else "",
        "notes": record.notes,
        "evaluation": record.evaluation,
        "expected_updated_at": record.updated_at.isoformat(),
    }
    return payload(catalog, **{**values, **overrides})


@pytest.mark.parametrize("hx", [False, True])
def test_all_planting_history_event_and_photo_pages_require_login(client, catalog, hx):
    record, event = catalog["planting"], catalog["event"]
    paths = [
        reverse("garden:planting-list"),
        reverse("garden:planting-history"),
        reverse("garden:planting-create"),
        reverse("garden:planting-varieties"),
        record.get_absolute_url(),
        reverse("garden:planting-edit", args=[record.pk]),
        reverse("garden:planting-delete", args=[record.pk]),
        reverse("garden:event-create", args=[record.pk]),
        reverse("garden:event-edit", args=[record.pk, event.pk]),
        reverse("garden:event-delete", args=[record.pk, event.pk]),
        *(
            reverse(f"garden:{kind}-history", args=[catalog[kind].pk])
            for kind in ("crop", "variety")
        ),
        reverse("garden:area-history", args=[catalog["root"].pk]),
    ]
    headers = {"HTTP_HX_REQUEST": "true"} if hx else {}
    for path in paths:
        response = client.get(path, **headers)
        assert response.status_code == (401 if hx else 302)
        assert "no-store" in response["Cache-Control"]
    for name, args, data in (
        ("planting-create", [], payload(catalog)),
        ("planting-edit", [record.pk], edit_payload(catalog, record)),
        (
            "planting-delete",
            [record.pk],
            {"confirm": "on", "expected_updated_at": record.updated_at.isoformat()},
        ),
        ("event-create", [record.pk], event_payload()),
        (
            "event-edit",
            [record.pk, event.pk],
            event_payload(expected_updated_at=event.updated_at.isoformat()),
        ),
        (
            "event-delete",
            [record.pk, event.pk],
            {"confirm": "on", "expected_updated_at": event.updated_at.isoformat()},
        ),
    ):
        assert client.post(reverse("garden:" + name, args=args), data, **headers).status_code == (
            401 if hx else 302
        )
    assert Planting.objects.count() == 1 and PlantingEvent.objects.count() == 1


def test_default_year_and_all_years_do_not_infer_years_from_event_dates(client, user, catalog):
    client.force_login(user)
    assert client.get(reverse("garden:planting-list")).context["year"] == timezone.localdate().year
    response = client.get(reverse("garden:planting-history"))
    assert response.status_code == 200 and response.context["year"] is None
    assert response.context["records"] == [catalog["planting"]]
    assert client.get(reverse("garden:planting-list"), {"year": 2025}).context["records"] == []
    response = client.get(reverse("garden:planting-list"), {"year": 2026})
    assert '<table class="register-table planting-table' in response.text
    assert f'aria-controls="details-planting-{catalog["planting"].pk}"' in response.text
    assert "Synthetic event note" not in response.text
    assert "Unknown variety" not in response.text  # Swedish is the default locale.
    fragment = client.get(catalog["planting"].get_absolute_url(), HTTP_HX_REQUEST="true")
    assert fragment.status_code == 200 and "Synthetic event note" in fragment.text
    assert "2025-12-20" in fragment.text and "2026-01-02" in fragment.text
    assert "<!doctype" not in fragment.text and "<script" not in fragment.text
    full = client.get(catalog["planting"].get_absolute_url())
    assert "<!doctype" in full.text
    client.cookies["django_language"] = "en"
    assert "Plantings" in client.get(reverse("garden:planting-list")).text
    assert "Sowing" in client.get(catalog["planting"].get_absolute_url()).text


def test_year_navigation_keeps_filters_and_respects_calendar_bounds(client, user, catalog):
    client.force_login(user)
    path = reverse("garden:planting-list")
    response = client.get(path, {"year": 2026, "crop": catalog["crop"].pk, "unknown_variety": "on"})
    for name, year in (("previous_url", 2025), ("next_url", 2027)):
        url = response.context[name]
        assert f"year={year}" in url and f"crop={catalog['crop'].pk}" in url
        assert "unknown_variety=on" in url
    assert "year=" not in response.context["all_years_url"]
    assert f"crop={catalog['crop'].pk}" in response.context["all_years_url"]
    assert "unknown_variety=on" in response.context["all_years_url"]
    assert client.get(path, {"year": 1}).context["previous_url"] is None
    assert client.get(path, {"year": 9999}).context["next_url"] is None
    for invalid in (0, 10000, "invalid", "2026.5"):
        response = client.get(path, {"year": invalid})
        assert response.status_code == 422 and response.context["form"].errors
        assert response.context["records"] == []
    assert client.get(path, {"area": 99999}).status_code == 422


@pytest.mark.parametrize("year", [None, "", 2025])
def test_area_history_keeps_all_years_or_the_explicit_year(
    client, user, catalog, monkeypatch, year
):
    monkeypatch.setattr(timezone, "localdate", lambda: date(2026, 5, 9))
    older = create_planting(
        actor=user,
        crop=catalog["crop"],
        area=catalog["child"],
        start_year=2025,
        label="Historical batch",
    ).record
    client.force_login(user)
    data = {} if year is None else {"year": year}
    history = client.get(reverse("garden:planting-history"), data)
    area_url = reverse("garden:area-history", args=[catalog["child"].pk])
    assert f'href="{area_url}{"?year=2025" if year else ""}"' in history.text
    for area in (catalog["root"], catalog["child"]):
        response = client.get(reverse("garden:area-history", args=[area.pk]), data)
        assert response.status_code == 200
        assert response.context["year"] == (2025 if year else None)
        assert set(response.context["records"]) == (
            {older} if year else {older, catalog["planting"]}
        )
        all_years = client.get(response.context["all_years_url"])
        assert all_years.context["year"] is None
        assert set(all_years.context["records"]) == {older, catalog["planting"]}
        overview = client.get(response.context["overview_url"])
        assert overview.context["year"] == (2025 if year else 2026)
        assert overview.context["records"] == ([older] if year else [catalog["planting"]])


def test_area_rollups_persistent_years_unknown_and_unassigned_filters(client, user, catalog):
    current = catalog["planting"]
    persistent = create_planting(
        actor=user,
        crop=catalog["crop"],
        area=catalog["root"],
        start_year=2024,
        lifespan="persistent",
        end_year=2026,
        label="Parent perennial",
    ).record
    unknown = create_planting(
        actor=user, crop=catalog["crop"], start_year=2026, label="Early plan"
    ).record
    future = create_planting(actor=user, crop=catalog["other_crop"], start_year=2027).record
    archive_area(actor=user, pk=catalog["child"].pk)
    archive_variety(actor=user, pk=catalog["variety"].pk)
    archive_crop(actor=user, pk=catalog["crop"].pk)
    client.force_login(user)
    response = client.get(reverse("garden:area-history", args=[catalog["root"].pk]), {"year": 2026})
    assert set(response.context["records"]) == {current, persistent}
    assert "Greenhouse / Bed" in response.text
    assert (
        client.get(
            reverse("garden:area-history", args=[catalog["root"].pk]), {"year": 2027}
        ).context["records"]
        == []
    )
    assert set(
        client.get(reverse("garden:crop-history", args=[catalog["crop"].pk])).context["records"]
    ) == {current, persistent, unknown}
    assert client.get(reverse("garden:variety-history", args=[catalog["variety"].pk])).context[
        "records"
    ] == [current]
    path = reverse("garden:planting-list")
    assert client.get(path, {"year": 2026, "unassigned": "on"}).context["records"] == [unknown]
    assert set(client.get(path, {"year": 2026, "unknown_variety": "on"}).context["records"]) == {
        persistent,
        unknown,
    }
    assert client.get(path, {"year": 2027}).context["records"] == [future]
    # URL-scoped histories cannot be redirected to a different record by query parameters.
    scoped = client.get(
        reverse("garden:variety-history", args=[catalog["variety"].pk]),
        {"crop": catalog["other_crop"].pk},
    )
    assert scoped.context["records"] == [current]
    for kind, record in (
        ("crop", catalog["crop"]),
        ("variety", catalog["variety"]),
        ("area", catalog["child"]),
    ):
        assert (
            reverse(f"garden:{kind}-history", args=[record.pk])
            in client.get(record.get_absolute_url()).text
        )


@pytest.mark.parametrize("hx", [False, True])
def test_create_edit_forms_save_early_plans_and_preserve_zero(client, user, catalog, hx):
    client.force_login(user)
    headers = {"HTTP_HX_REQUEST": "true"} if hx else {}
    response = client.post(
        reverse("garden:planting-create"), payload(catalog, quantity=0), **headers
    )
    assert response.status_code == (204 if hx else 302)
    record = Planting.objects.get(label="New batch")
    assert record.quantity == 0 and record.variety_id is None and record.area_id is None
    path = reverse("garden:planting-edit", args=[record.pk])
    form = client.get(path).context["form"]
    assert form["expected_updated_at"].value() == record.updated_at.isoformat()
    response = client.post(
        path,
        edit_payload(catalog, record, variety=catalog["variety"].pk, area=catalog["child"].pk),
        **headers,
    )
    assert response.status_code == (204 if hx else 302)
    record.refresh_from_db()
    assert (record.variety_id, record.area_id) == (catalog["variety"].pk, catalog["child"].pk)
    if hx:
        assert response["HX-Redirect"] == record.get_absolute_url()


@pytest.mark.parametrize("hx", [False, True])
@pytest.mark.parametrize(
    "invalid", ["variety", "crop", "area", "lifespan", "year", "end", "quantity"]
)
def test_invalid_forms_show_useful_errors_and_keep_draft_key_without_writes(
    client, user, catalog, hx, invalid
):
    client.force_login(user)
    changes = {
        "variety": {"variety": catalog["other_variety"].pk},
        "crop": {"crop": 99999},
        "area": {"area": 99999},
        "lifespan": {"lifespan": "invalid"},
        "year": {"start_year": 0},
        "end": {"end_year": 2027},
        "quantity": {"quantity": -1},
    }[invalid]
    data = payload(catalog, **changes)
    headers = {"HTTP_HX_REQUEST": "true"} if hx else {}
    response = client.post(reverse("garden:planting-create"), data, **headers)
    assert response.status_code == 422 and "error-summary" in response.text
    assert Planting.objects.count() == 1
    if hx:
        assert 'id="register-form"' not in response.text
    else:
        assert response.context["form"]["notes"].value() == data["notes"]
        assert response.context["form"]["submission_key"].value() == data["submission_key"]


@pytest.mark.parametrize("hx", [False, True])
@pytest.mark.parametrize("editing", [False, True])
@pytest.mark.parametrize("field", ["crop", "variety", "area"])
@pytest.mark.parametrize(
    "value",
    [
        pytest.param(str(2**63), id="integer-overflow"),
        pytest.param("9" * 100, id="long-integer"),
    ],
)
def test_out_of_range_association_ids_show_errors_without_saving(
    client, user, catalog, image_upload, hx, editing, field, value
):
    client.force_login(user)
    record = catalog["planting"]
    version = record.updated_at
    data = (
        edit_payload(catalog, record, **{field: value})
        if editing
        else payload(catalog, **{field: value})
    )
    path = (
        reverse("garden:planting-edit", args=[record.pk])
        if editing
        else reverse("garden:planting-create")
    )
    headers = {"HTTP_HX_REQUEST": "true"} if hx else {}
    response = client.post(path, {**data, "photos": image_upload()}, **headers)
    assert response.status_code == 422 and "error-summary" in response.text
    assert "Välj en giltig post." in response.text
    if not hx:
        form = response.context["form"]
        assert field in form.errors
        assert form[field].value() == value
        assert form["notes"].value() == data["notes"]
        assert form["submission_key"].value() == data["submission_key"]
    assert not PlantingSubmission.objects.filter(key=data["submission_key"]).exists()
    assert Planting.objects.count() == 1 and not Photo.objects.exists()
    record.refresh_from_db()
    assert record.updated_at == version


@pytest.mark.parametrize("hx", [False, True])
@pytest.mark.parametrize("endpoint", ["planting-create", "planting-varieties"])
@pytest.mark.parametrize(
    "crop_id",
    [
        pytest.param(str(2**63), id="integer-overflow"),
        pytest.param(str(-(2**63) - 1), id="integer-underflow"),
        pytest.param("9" * 100, id="long-integer"),
    ],
)
def test_out_of_range_crop_prefills_offer_no_varieties(
    client, user, catalog, hx, endpoint, crop_id
):
    client.force_login(user)
    headers = {"HTTP_HX_REQUEST": "true"} if hx else {}
    response = client.get(reverse("garden:" + endpoint), {"crop": crop_id}, **headers)
    assert response.status_code == 200 and "<select" in response.text
    assert "Golden" not in response.text and "Blue" not in response.text
    if endpoint == "planting-create":
        assert not response.context["form"].fields["variety"].queryset.exists()
    assert Planting.objects.count() == 1


@pytest.mark.parametrize("hx", [False, True])
@pytest.mark.parametrize("field", ["planting", "variety"])
def test_out_of_range_choice_context_ids_are_handled_before_queries(
    client, user, catalog, hx, field
):
    client.force_login(user)
    headers = {"HTTP_HX_REQUEST": "true"} if hx else {}
    response = client.get(
        reverse("garden:planting-varieties"),
        {"crop": catalog["crop"].pk, field: str(2**63)},
        **headers,
    )
    assert response.status_code == (400 if field == "planting" else 200)
    if field == "variety":
        assert 'value="" selected' in response.text


def test_dependent_variety_options_are_scoped_clear_mismatch_and_preserve_current_archive(
    client, user, catalog
):
    client.force_login(user)
    archive_variety(actor=user, pk=catalog["variety"].pk)
    path = reverse("garden:planting-varieties")
    response = client.get(
        path,
        {"crop": catalog["other_crop"].pk, "variety": catalog["variety"].pk},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200 and "<select" in response.text
    assert "Blue" in response.text and "Golden" not in response.text
    assert f'value="{catalog["other_variety"].pk}" selected' not in response.text
    assert "<!doctype" not in response.text
    edit_response = client.get(
        path,
        {
            "crop": catalog["crop"].pk,
            "variety": catalog["variety"].pk,
            "planting": catalog["planting"].pk,
        },
    )
    assert f'value="{catalog["variety"].pk}" selected' in edit_response.text
    new_response = client.get(path, {"crop": catalog["crop"].pk})
    assert "Golden" not in new_response.text
    for crop in ("", "invalid", 99999):
        assert 'value=""' in client.get(path, {"crop": crop}).text
    assert client.get(path, {"planting": "invalid"}).status_code == 400
    assert client.get(path, {"planting": 99999}).status_code == 404
    assert 'aria-describedby="id_variety_errors variety-status"' in new_response.text


def test_variety_status_stays_associated_beside_errors(client, user, catalog):
    client.force_login(user)
    create_response = client.get(reverse("garden:planting-create"), {"crop": catalog["crop"].pk})
    assert 'aria-describedby="id_variety_errors variety-status"' in create_response.text
    swap_response = client.get(
        reverse("garden:planting-varieties"),
        {"crop": catalog["crop"].pk},
        HTTP_HX_REQUEST="true",
    )
    assert 'aria-describedby="id_variety_errors variety-status"' in swap_response.text


def test_new_choices_hide_archives_edit_retains_existing_and_server_rechecks(client, user, catalog):
    archive_crop(actor=user, pk=catalog["crop"].pk)
    archive_variety(actor=user, pk=catalog["variety"].pk)
    archive_area(actor=user, pk=catalog["child"].pk)
    client.force_login(user)
    form = client.get(reverse("garden:planting-create")).context["form"]
    assert catalog["crop"] not in form.fields["crop"].queryset
    assert catalog["child"] not in form.fields["area"].queryset
    record = catalog["planting"]
    path = reverse("garden:planting-edit", args=[record.pk])
    form = client.get(path).context["form"]
    assert catalog["crop"] in form.fields["crop"].queryset
    assert catalog["variety"] in form.fields["variety"].queryset
    assert catalog["child"] in form.fields["area"].queryset
    assert client.post(path, edit_payload(catalog, record)).status_code == 302
    assert client.post(reverse("garden:planting-create"), payload(catalog)).status_code == 422


@pytest.mark.parametrize("hx", [False, True])
@pytest.mark.parametrize("change", ["archive", "reclassify", "delete_old_choices"])
def test_immutable_http_retry_precedes_mutable_association_eligibility(
    client, user, catalog, image_upload, monkeypatch, hx, change
):
    from garden.services import delete_area, delete_variety
    from garden.views import plantings

    client.force_login(user)
    writer, attempts = plantings.services.create_planting, 0

    def lost_response(**values):
        nonlocal attempts
        result = writer(**values)
        attempts += 1
        if attempts == 1:
            raise OperationalError("Synthetic lost response after commit")
        return result

    monkeypatch.setattr(plantings.services, "create_planting", lost_response)
    headers = {"HTTP_HX_REQUEST": "true"} if hx else {}
    key = uuid4()
    data = payload(
        catalog, submission_key=key, variety=catalog["variety"].pk, area=catalog["child"].pk
    )
    path = reverse("garden:planting-create")
    first_response = client.post(path, {**data, "photos": image_upload()}, **headers)
    assert first_response.status_code == 503
    if not hx:
        assert first_response.context["form"]["variety"].value() == str(data["variety"])
        assert first_response.context["form"]["area"].value() == str(data["area"])
    saved = Planting.objects.get(label="New batch")
    if change == "archive":
        archive_crop(actor=user, pk=catalog["crop"].pk)
        archive_variety(actor=user, pk=catalog["variety"].pk)
        archive_area(actor=user, pk=catalog["child"].pk)
    else:
        # Explicit reconciliation permits later variety reclassification or deletion.
        edit_planting(
            actor=user,
            pk=saved.pk,
            crop=catalog["crop"],
            start_year=2026,
            label="Later edit",
            expected_updated_at=saved.updated_at,
        )
        edit_planting(
            actor=user,
            pk=catalog["planting"].pk,
            crop=catalog["crop"],
            start_year=2026,
            expected_updated_at=catalog["planting"].updated_at,
        )
        if change == "reclassify":
            edit_variety(
                actor=user,
                pk=catalog["variety"].pk,
                crop=catalog["other_crop"],
                name="Reclassified",
                expected_updated_at=catalog["variety"].updated_at,
            )
        else:
            delete_variety(
                actor=user,
                pk=catalog["variety"].pk,
                expected_updated_at=catalog["variety"].updated_at,
            )
            delete_area(
                actor=user, pk=catalog["child"].pk, expected_updated_at=catalog["child"].updated_at
            )
    # Ordinary-page validation must still render the original selections for a
    # second retry after mutable choices disappeared, not silently submit blanks.
    draft_response = client.post(path, {**data, "start_year": 0}, **headers)
    assert draft_response.status_code == 422
    if not hx:
        draft = draft_response.context["form"]
        assert f'value="{data["variety"]}" selected' in str(draft["variety"])
        assert f'value="{data["area"]}" selected' in str(draft["area"])
        assert f'value="{data["crop"]}" selected' in str(draft["crop"])
        assert draft["submission_key"].value() == str(key)
    response = client.post(path, {**data, "photos": image_upload()}, **headers)
    assert response.status_code == (204 if hx else 302)
    assert Planting.objects.count() == 2 and Photo.objects.count() == 1
    assert (
        client.post(path, {**data, "quantity": 1, "photos": image_upload()}, **headers).status_code
        == 409
    )


def test_edit_retry_resolves_after_later_edit_but_stale_new_submission_conflicts(
    client, user, catalog, image_upload
):
    client.force_login(user)
    record = catalog["planting"]
    path = reverse("garden:planting-edit", args=[record.pk])
    data = edit_payload(catalog, record)
    assert client.post(path, {**data, "photos": image_upload()}).status_code == 302
    record.refresh_from_db()
    edit_planting(
        actor=user,
        pk=record.pk,
        crop=catalog["other_crop"],
        start_year=2027,
        label="Later edit",
        expected_updated_at=record.updated_at,
    )
    archive_variety(actor=user, pk=catalog["variety"].pk)
    archive_area(actor=user, pk=catalog["child"].pk)
    archive_crop(actor=user, pk=catalog["crop"].pk)
    assert client.post(path, {**data, "photos": image_upload()}).status_code == 302
    assert client.post(path, {**data, "submission_key": str(uuid4())}).status_code == 409
    record.refresh_from_db()
    assert record.crop_id == catalog["other_crop"].pk and record.start_year == 2027
    assert record.photos.count() == 1
    assert client.post(path, payload(catalog)).status_code == 422


def test_no_javascript_crop_choice_refresh_preserves_draft_without_saving(client, user, catalog):
    client.force_login(user)
    data = payload(
        catalog, crop=catalog["other_crop"].pk, variety=catalog["variety"].pk, choose_crop="1"
    )
    response = client.post(reverse("garden:planting-create"), data)
    assert response.status_code == 200 and Planting.objects.count() == 1
    form = response.context["form"]
    assert "Blue" in str(form["variety"]) and "Golden" not in str(form["variety"])
    assert (
        form["notes"].value() == data["notes"]
        and form["submission_key"].value() == data["submission_key"]
    )


@pytest.mark.parametrize("hx", [False, True])
def test_event_create_edit_dates_versions_retry_and_confirmed_deletion(client, user, catalog, hx):
    client.force_login(user)
    record = catalog["planting"]
    headers = {"HTTP_HX_REQUEST": "true"} if hx else {}
    path = reverse("garden:event-create", args=[record.pk])
    invalid = client.post(path, event_payload(planned_date=""), **headers)
    assert invalid.status_code == 422 and "error-summary" in invalid.text
    data = event_payload(actual_date="2027-01-01")
    response = client.post(path, data, **headers)
    assert response.status_code == (204 if hx else 302)
    assert client.post(path, data, **headers).status_code == (204 if hx else 302)
    assert record.events.count() == 2
    event = record.events.get(note="Synthetic milestone")
    edit_path = reverse("garden:event-edit", args=[record.pk, event.pk])
    initial = client.get(edit_path).context["form"]
    assert 'value="2025-12-31"' in str(initial["planned_date"])
    data = event_payload(
        expected_updated_at=event.updated_at.isoformat(),
        kind="harvest_end",
        actual_date="2027-02-03",
    )
    assert client.post(edit_path, data, **headers).status_code == (204 if hx else 302)
    assert client.post(edit_path, data, **headers).status_code == (204 if hx else 302)
    assert (
        client.post(edit_path, {**data, "submission_key": str(uuid4())}, **headers).status_code
        == 409
    )
    assert client.post(edit_path, event_payload(), **headers).status_code == 422
    event.refresh_from_db()
    delete_path = reverse("garden:event-delete", args=[record.pk, event.pk])
    assert client.get(delete_path).status_code == 200
    assert (
        client.post(delete_path, {"expected_updated_at": event.updated_at.isoformat()}).status_code
        == 422
    )
    assert (
        client.post(
            delete_path, {"expected_updated_at": event.updated_at.isoformat(), "confirm": "on"}
        ).status_code
        == 302
    )
    assert record.events.count() == 1
    assert client.get(edit_path).status_code == 404


def test_planting_delete_is_confirmed_and_stale_safe_and_receipts_prevent_recreation(
    client, user, catalog
):
    client.force_login(user)
    data = payload(catalog)
    create_path = reverse("garden:planting-create")
    assert client.post(create_path, data).status_code == 302
    record = Planting.objects.get(label="New batch")
    path = reverse("garden:planting-delete", args=[record.pk])
    assert client.get(path).status_code == 200 and Planting.objects.count() == 2
    assert (
        client.post(path, {"expected_updated_at": record.updated_at.isoformat()}).status_code == 422
    )
    edit_planting(
        actor=user,
        pk=record.pk,
        crop=record.crop_id,
        start_year=2026,
        label="Changed",
        expected_updated_at=record.updated_at,
    )
    assert (
        client.post(
            path, {"expected_updated_at": record.updated_at.isoformat(), "confirm": "on"}
        ).status_code
        == 422
    )
    record.refresh_from_db()
    assert (
        client.post(
            path, {"expected_updated_at": record.updated_at.isoformat(), "confirm": "on"}
        ).status_code
        == 302
    )
    assert client.post(create_path, data).status_code == 409
    assert Planting.objects.count() == 1


def test_shared_members_csrf_inactive_access_and_referenced_variety_edit(
    client, user, catalog, django_user_model
):
    other = django_user_model.objects.create_user(username="second-gardener")
    client.force_login(other)
    record = catalog["planting"]
    assert client.get(record.get_absolute_url()).status_code == 200
    path = reverse("garden:planting-edit", args=[record.pk])
    assert client.post(path, edit_payload(catalog, record)).status_code == 302
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(other)
    assert csrf_client.post(path, edit_payload(catalog, record)).status_code == 403
    variety = catalog["variety"]
    response = client.post(
        reverse("garden:variety-edit", args=[variety.pk]),
        {
            "name": "Renamed",
            "crop": catalog["other_crop"].pk,
            "submission_key": str(uuid4()),
            "expected_updated_at": variety.updated_at.isoformat(),
        },
    )
    assert response.status_code == 422 and "crop" in response.context["form"].errors
    other.is_active = False
    other.save(update_fields=["is_active"])
    for path in (
        record.get_absolute_url(),
        reverse("garden:planting-list"),
        reverse("garden:planting-varieties"),
    ):
        assert client.get(path).status_code == 302
        assert client.get(path, HTTP_HX_REQUEST="true").status_code == 401


def test_user_content_is_escaped_in_lists_details_events_and_fragments(client, user, catalog):
    hostile = '<script>alert("x")</script>'
    record = create_planting(
        actor=user, crop=catalog["crop"], start_year=2026, label=hostile, evaluation=hostile
    ).record
    create_planting_event(
        actor=user, planting=record, kind="other", actual_date=date(2026, 1, 1), note=hostile
    )
    client.force_login(user)
    for path, headers in (
        (reverse("garden:planting-list"), {}),
        (record.get_absolute_url(), {}),
        (record.get_absolute_url(), {"HTTP_HX_REQUEST": "true"}),
    ):
        response = client.get(path, {"year": 2026}, **headers)
        assert hostile not in response.text and "&lt;script&gt;" in response.text
        assert "unsafe-eval" not in response["Content-Security-Policy"]
        assert "no-store" in response["Cache-Control"]
