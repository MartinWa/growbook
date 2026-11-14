"""Management, surviving deletion destinations and live completed Experiences."""

from datetime import date
from urllib.parse import quote, urlencode
from uuid import uuid4

import pytest
from django.db import OperationalError
from django.urls import reverse

from diary.actions import CycleStatusAction
from diary.services import create_entry
from garden.queries import variety_evaluations
from garden.services import (
    archive_area,
    archive_variety,
    create_area,
    create_category,
    create_crop,
    create_planting,
    create_variety,
    edit_variety,
)
from garden.services.cycles import create_planting_category, edit_cycle

pytestmark = pytest.mark.django_db(transaction=True)


def test_area_management_owns_maintenance_and_preserves_context(client, user, image_upload):
    parent = create_area(actor=user, name="North garden").record
    child = create_area(actor=user, name="Bed", parent=parent, uploads=[image_upload()]).record
    retired = create_area(actor=user, name="Retired").record
    archive_area(actor=user, pk=retired.pk)
    client.force_login(user)
    browse = client.get(child.get_absolute_url())
    photo = child.photos.get()
    for route, pk in (
        ("area-edit", child.pk),
        ("area-delete", child.pk),
        ("area-archive", child.pk),
        ("photo-edit", photo.pk),
        ("photo-delete", photo.pk),
    ):
        assert reverse("garden:" + route, args=[pk]) not in browse.text
    assert photo.thumbnail_url in browse.text
    assert client.get(reverse("garden:area-list")).text.count('href="/garden/areas/manage/"') == 1
    path = reverse("garden:area-manage", query={"archived": "1", "selected": child.pk})
    managed = client.get(path)
    assert set(managed.context["records"]) == {parent, child, retired}
    assert "North garden / Bed" in managed.text
    assert managed.context["browsing_url"] == reverse("garden:area-list") + "?archived=1"
    edit = reverse("garden:area-edit", args=[child.pk])
    form = client.get(edit, {"next": path})
    assert form.context["cancel_url"] == path
    payload = {
        "name": "Updated bed",
        "parent": parent.pk,
        "submission_key": uuid4(),
        "expected_updated_at": child.updated_at.isoformat(),
        "next": path,
    }
    assert client.post(edit, payload).url == path
    assert client.post(edit, payload).url == path
    assert (
        client.post(
            reverse("garden:area-archive", args=[child.pk]), {"archived": "true", "next": path}
        ).url
        == path
    )
    child.refresh_from_db()
    assert (
        client.post(
            reverse("garden:photo-edit", args=[photo.pk]),
            {
                "caption": "Updated",
                "position": 0,
                "expected_updated_at": child.updated_at.isoformat(),
                "next": path,
            },
        ).url
        == path
    )
    child.refresh_from_db()
    assert (
        client.post(
            reverse("garden:photo-delete", args=[photo.pk]),
            {"confirm": "on", "expected_updated_at": child.updated_at.isoformat(), "next": path},
        ).url
        == path
    )
    child.refresh_from_db()
    assert (
        client.post(
            reverse("garden:area-delete", args=[child.pk]),
            {"confirm": "on", "expected_updated_at": child.updated_at.isoformat(), "next": path},
        ).url
        == path
    )


@pytest.mark.parametrize("kind", ["category", "planting-category"])
def test_categories_maintained_centrally_with_overview_return(client, user, kind):
    category = (create_category if kind == "category" else create_planting_category)(
        actor=user, name="Summer"
    ).record
    client.force_login(user)
    origin = (
        reverse("garden:variety-list")
        if kind == "category"
        else reverse("garden:planting-list") + "?year=2024&unassigned=1"
    )
    browse = client.get(origin)
    edit = reverse(f"garden:{kind}-edit", args=[category.pk])
    delete = reverse(f"garden:{kind}-delete", args=[category.pk])
    assert edit not in browse.text and delete not in browse.text
    management = reverse(f"garden:{kind}-manage", query={"next": origin})
    assert client.get(management).context["browsing_url"] == origin
    assert edit in client.get(management).text
    assert client.get(edit, {"next": management}).context["cancel_url"] == management
    assert (
        client.post(
            delete,
            {
                "confirm": "on",
                "expected_updated_at": category.updated_at.isoformat(),
                "next": management,
            },
        ).url
        == management
    )
    assert not client.get(management).context["records"]


@pytest.mark.parametrize("kind", ["category", "planting-category"])
@pytest.mark.parametrize("from_management", [True, False])
def test_category_deletion_removes_back_link_to_deleted_category(
    client, user, kind, from_management
):
    category = (create_category if kind == "category" else create_planting_category)(
        actor=user, name="Summer"
    ).record
    management = reverse(f"garden:{kind}-manage")
    origin = category.get_absolute_url() + "?q=summer&year=2024"
    destination = (
        reverse(f"garden:{kind}-manage", query={"next": origin}) if from_management else origin
    )
    client.force_login(user)
    response = client.post(
        reverse(f"garden:{kind}-delete", args=[category.pk]),
        {
            "next": destination,
            "confirm": "on",
            "expected_updated_at": category.updated_at.isoformat(),
        },
    )
    assert response.url == management
    overview = client.get(response.url).context["browsing_url"]
    assert client.get(overview).status_code == 200


def test_related_record_links_keep_category_origin(client, user, planting_catalog):
    crop, variety, other, _ = planting_catalog
    origin = reverse("garden:all-crops") + "?q=tomato&archived=1"
    client.force_login(user)
    crop_page = client.get(crop.get_absolute_url(), {"next": origin})
    variety_url = variety.get_absolute_url() + "?next=" + quote(origin, safe="/")
    assert variety_url.replace("&", "&amp;") in crop_page.text
    variety_page = client.get(variety_url)
    assert variety_page.context["destination"] == origin
    assert "next=" in variety_page.context["delete_url"]
    first = create_planting(actor=user, crop=crop, variety=variety, start_year=2024).record
    second = create_planting(
        actor=user, crop=crop, variety=other, start_year=2024, batch_planting=first
    ).record
    planting_origin = reverse("garden:planting-status", args=["planned"]) + "?year=2024"
    page = client.get(first.get_absolute_url(), {"next": planting_origin})
    member_url = second.get_absolute_url() + "?next=" + quote(planting_origin, safe="/")
    assert member_url in page.text


@pytest.mark.parametrize(
    "scope", ["category", "all-crops", "uncategorized", "planned", "custom", "history"]
)
def test_delete_last_record_preserves_surviving_category_and_filters(client, user, scope):
    crop = create_crop(actor=user, name="Tomato").record
    variety = create_variety(actor=user, name="Golden", crop=crop).record
    if scope in ("category", "all-crops", "uncategorized"):
        record, kind = variety, "variety"
        archive_variety(actor=user, pk=record.pk)
        record.refresh_from_db()
        if scope == "category":
            category = create_category(actor=user, name="Vegetables", crops=[crop]).record
            path = category.get_absolute_url()
        else:
            path = reverse("garden:" + scope)
        destination = path + "?q=gold&archived=1"
    else:
        custom = create_planting_category(actor=user, name="Summer").record
        record = create_planting(
            actor=user, crop=crop, variety=variety, start_year=2024, category_ids=[custom.pk]
        ).record
        kind = "planting"
        path = (
            custom.get_absolute_url()
            if scope == "custom"
            else reverse("garden:planting-status", args=["planned"])
            if scope == "planned"
            else reverse("garden:planting-history")
        )
        destination = (
            path
            + "?"
            + urlencode(
                {"all_years": "1", "crop": crop.pk, "variety": variety.pk, "unassigned": "1"}
            )
        )
    client.force_login(user)
    delete = reverse(f"garden:{kind}-delete", args=[record.pk])
    confirmation = client.get(delete, {"next": destination})
    assert confirmation.context["destination"] == destination
    assert 'name="next"' in confirmation.text
    response = client.post(
        delete,
        {
            "next": destination,
            "confirm": "on",
            "expected_updated_at": record.updated_at.isoformat(),
        },
    )
    assert response.url == destination
    page = client.get(destination)
    assert page.status_code == 200 and not page.context["records"]


@pytest.mark.parametrize(
    "origin",
    [
        "https://example.invalid/",
        "//example.invalid/",
        "/operator/",
        "self",
        "own-history",
        "unrelated",
    ],
)
def test_deletion_rejects_unsafe_or_deleted_record_origin(client, user, origin):
    crop = create_crop(actor=user, name="Bean").record
    record = create_variety(actor=user, crop=crop, name="Blue").record
    destination = (
        record.get_absolute_url()
        if origin == "self"
        else reverse("garden:variety-history", args=[record.pk])
        if origin == "own-history"
        else reverse("garden:planting-list")
        if origin == "unrelated"
        else origin
    )
    client.force_login(user)
    response = client.post(
        reverse("garden:variety-delete", args=[record.pk]),
        {
            "next": destination,
            "confirm": "on",
            "expected_updated_at": record.updated_at.isoformat(),
        },
    )
    assert response.url == reverse("garden:variety-list")


def test_delete_errors_keep_original_destination(client, user, monkeypatch):
    crop = create_crop(actor=user, name="Bean").record
    variety = create_variety(actor=user, crop=crop, name="Blue").record
    destination = reverse("garden:all-crops") + "?q=Bean&archived=1"
    client.force_login(user)
    payload = {
        "next": destination,
        "confirm": "on",
        "expected_updated_at": crop.updated_at.isoformat(),
    }
    path = reverse("garden:crop-delete", args=[crop.pk])
    protected = client.post(path, payload)
    assert protected.status_code == 422 and protected.context["destination"] == destination
    assert variety.pk == crop.varieties.get().pk

    def unavailable(**kwargs):
        raise OperationalError("Synthetic unavailable database")

    monkeypatch.setattr("garden.views.registers.services.delete_crop", unavailable)
    failed = client.post(path, payload)
    assert failed.status_code == 503 and failed.context["destination"] == destination


def test_completed_experiences_are_exact_live_member_results(client, user, planting_catalog):
    crop, variety, other, area = planting_catalog
    variety = edit_variety(
        actor=user,
        pk=variety.pk,
        name=variety.name,
        crop=crop,
        experiences="Manual notes",
        expected_updated_at=variety.updated_at,
    ).record
    first = create_planting(
        actor=user,
        crop=crop,
        variety=variety,
        area=area,
        start_year=2023,
        evaluation="Old result",
        status="completed",
        completed_on=date(2023, 9, 1),
    ).record
    second = create_planting(
        actor=user,
        crop=crop,
        variety=variety,
        start_year=2024,
        lifespan="persistent",
        end_year=2025,
        evaluation="<script>Escaped result</script>",
        status="completed",
        completed_on=date(2025, 9, 1),
    ).record
    create_planting(
        actor=user,
        crop=crop,
        variety=other,
        start_year=2023,
        batch_planting=first,
        evaluation="Different variety",
    )
    unknown_date = create_planting(
        actor=user, crop=crop, variety=variety, start_year=2022, evaluation="Unknown date"
    ).record
    from core.writes import validated_write
    from garden.models import PlantingCycle

    with validated_write():
        PlantingCycle.objects.filter(pk=unknown_date.cycle_id).update(status="completed")
    create_planting(
        actor=user, crop=crop, variety=variety, start_year=2025, evaluation="Incomplete result"
    )
    create_planting(
        actor=user,
        crop=crop,
        variety=variety,
        start_year=2025,
        evaluation=" ",
        status="completed",
        completed_on=date(2025, 9, 1),
    )
    archive_area(actor=user, pk=area.pk)
    assert [result.pk for result in variety_evaluations(variety)] == [
        second.pk,
        first.pk,
        unknown_date.pk,
    ]
    client.force_login(user)
    for headers in ({}, {"HTTP_HX_REQUEST": "true"}):
        page = client.get(variety.get_absolute_url(), **headers)
        assert (
            "Manual notes" in page.text
            and "Old result" in page.text
            and "Unknown date" in page.text
        )
        assert "&lt;script&gt;Escaped result&lt;/script&gt;" in page.text
        assert "Different variety" not in page.text and "Incomplete result" not in page.text
        assert "2024–2025" in page.text and area.name in page.text
    cycle = second.cycle
    edit_cycle(
        actor=user,
        pk=cycle.pk,
        status="completed",
        completed_on=cycle.completed_on,
        expected_updated_at=cycle.updated_at,
        evaluations={second.pk: "Corrected"},
        expected_member_versions={second.pk: second.updated_at},
    )
    assert "Corrected" in client.get(variety.get_absolute_url()).text
    second.refresh_from_db()
    edit_cycle(
        actor=user, pk=cycle.pk, status="planned", expected_updated_at=second.cycle.updated_at
    )
    assert second.pk not in [result.pk for result in variety_evaluations(variety)]
    second.refresh_from_db()
    assert second.evaluation == "Corrected"
    create_entry(
        author=user,
        date=date(2025, 10, 1),
        body="Completed through diary",
        uploads=[],
        submission_key=uuid4(),
        action=CycleStatusAction(
            planting=second.pk,
            expected_updated_at=second.cycle.updated_at,
            status="completed",
            completed_on=date(2025, 10, 1),
            evaluations={second.pk: "Diary result"},
            expected_member_versions={second.pk: second.updated_at},
        ),
    )
    assert "Diary result" in client.get(variety.get_absolute_url()).text
    variety.refresh_from_db()
    edit_variety(
        actor=user,
        pk=variety.pk,
        name=variety.name,
        crop=crop,
        experiences="",
        expected_updated_at=variety.updated_at,
    )
    assert "Diary result" in client.get(variety.get_absolute_url()).text
    second.refresh_from_db()
    edit_cycle(
        actor=user,
        pk=second.cycle_id,
        status="completed",
        completed_on=second.cycle.completed_on,
        expected_updated_at=second.cycle.updated_at,
        evaluations={second.pk: ""},
        expected_member_versions={second.pk: second.updated_at},
    )
    assert second.pk not in [result.pk for result in variety_evaluations(variety)]
