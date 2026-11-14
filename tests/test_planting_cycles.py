"""Batch-wide lifecycle, category counts, protected history and serialized retries."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from uuid import uuid4

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.urls import reverse
from django.utils import timezone

from core.writes import validated_write
from garden.models import CycleSubmission, Planting, PlantingCategory, PlantingCycle
from garden.queries import plantings_in_year
from garden.services import (
    RegisterConflict,
    create_crop,
    create_planting,
    create_planting_event,
    delete_planting,
    edit_planting,
)
from garden.services.cycles import (
    create_planting_category,
    delete_planting_category,
    edit_cycle,
    edit_planting_category,
    transition_cycle,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def batch(user, planting_catalog):
    crop, first, second, area = planting_catalog
    one = create_planting(
        actor=user,
        crop=crop,
        variety=first,
        area=area,
        start_year=2025,
        lifespan="persistent",
        label="Trial",
    ).record
    two = create_planting(
        actor=user,
        crop=crop,
        variety=second,
        start_year=2025,
        lifespan="persistent",
        batch_planting=one,
        quantity=0,
    ).record
    one.refresh_from_db()
    two.refresh_from_db()
    return one, two


def change(user, cycle, **values):
    return edit_cycle(
        actor=user,
        pk=cycle.pk,
        expected_updated_at=cycle.updated_at,
        status=values.pop("status", cycle.status),
        started_on=values.pop("started_on", cycle.started_on),
        completed_on=values.pop("completed_on", cycle.completed_on),
        category_ids=values.pop(
            "category_ids", list(cycle.categories.values_list("pk", flat=True))
        ),
        **values,
    )


def test_shared_status_categories_distinct_filtered_counts_and_zero_cards(client, user, batch):
    one, two = batch
    sale = create_planting_category(actor=user, name="For Sale").record
    create_planting_category(actor=user, name="Empty")
    cycle = change(
        user,
        one.cycle,
        status="ongoing",
        started_on=date(2025, 3, 1),
        category_ids=[sale.pk, sale.pk],
    ).record
    assert one.cycle_id == two.cycle_id == one.batch_key
    client.force_login(user)
    response = client.get(reverse("garden:planting-list"), {"year": 2026})
    assert len(response.context["cycle_groups"]) == 1
    assert [card["count"] for card in response.context["category_cards"]] == [0, 1, 0, 0, 1]
    assert 'class="category-grid"' in response.text
    assert "data-area-id=" not in response.text
    assert response.context["planting_illustration"] == "images/registers/plantings.webp"
    for status in PlantingCycle.Status.values:
        illustration = f"images/registers/{status}.webp"
        assert f'<img src="/static/{illustration}" alt=""' in response.text
        result = client.get(reverse("garden:planting-status", args=[status]), {"year": 2026})
        assert result.context["planting_illustration"] == illustration
        assert f'class="register-illustration" src="/static/{illustration}"' in result.text
    for path in (reverse("garden:planting-status", args=["ongoing"]), sale.get_absolute_url()):
        result = client.get(path, {"year": 2026, "variety": two.variety_id})
        assert len(result.context["cycle_groups"]) == 1
        assert [member.pk for member in result.context["records"]] == [two.pk]
        assert str(cycle.pk) in result.text
    assert result.context["planting_illustration"] == "images/registers/plantings.webp"
    # Area and variety must match the same member, not different members in the batch.
    response = client.get(
        sale.get_absolute_url(), {"year": 2026, "area": one.area_id, "variety": two.variety_id}
    )
    assert not response.context["records"]
    assert response.context["empty_action_url"] == sale.get_absolute_url() + "?all_years=1"


@pytest.mark.parametrize("scope", ["planting", "crop", "variety", "area"])
@pytest.mark.parametrize("year", [None, 2025])
def test_history_category_navigation_preserves_year_and_scope(
    client, user, planting_catalog, monkeypatch, scope, year
):
    monkeypatch.setattr(timezone, "localdate", lambda: date(2026, 5, 9))
    crop, variety, _other_variety, area = planting_catalog
    sale = create_planting_category(actor=user, name="For Sale").record
    members = [
        create_planting(
            actor=user,
            crop=crop,
            variety=variety,
            area=area,
            start_year=2025,
            status=status,
            started_on=date(2025, 3, 1) if status == "ongoing" else None,
            completed_on=date(2025, 9, 1) if status == "completed" else None,
            category_ids=[sale.pk],
        ).record
        for status in PlantingCycle.Status.values
    ]
    if scope != "planting":
        create_planting(
            actor=user,
            crop=create_crop(actor=user, name="Bean").record,
            start_year=2025,
        )
    client.force_login(user)
    pk = {"crop": crop.pk, "variety": variety.pk, "area": area.pk}.get(scope)
    source = client.get(
        reverse(f"garden:{scope}-history", args=[pk] if pk else []),
        {"year": year} if year else {},
    )
    overview = client.get(source.context["category_overview_url"])
    assert overview.context["category_overview"]
    assert overview.context["year"] == year
    assert set(overview.context["records"]) == set(members)
    for card in overview.context["category_cards"]:
        result = client.get(card["url"])
        assert result.context["year"] == year
        assert len(result.context["cycle_groups"]) == card["count"]
        assert card["count"] == (3 if card.get("category") else 1)
        assert set(result.context["records"]) <= set(members)
        categories = client.get(result.context["category_overview_url"])
        assert categories.context["year"] == year
        assert set(categories.context["records"]) == set(members)


def test_start_complete_and_corrections_preserve_ids_years_events_and_members(user, batch):
    one, two = batch
    event = create_planting_event(
        actor=user, planting=two, kind="harvest_end", actual_date=date(2025, 9, 1)
    ).record
    cycle = PlantingCycle.objects.get(pk=one.cycle_id)
    assert cycle.status == "planned"
    started = transition_cycle(
        actor=user,
        pk=cycle.pk,
        action="start",
        date=date(2025, 3, 1),
        expected_updated_at=cycle.updated_at,
    ).record
    assert started.status == "ongoing"
    completed = transition_cycle(
        actor=user,
        pk=cycle.pk,
        action="complete",
        date=date(2027, 9, 1),
        expected_updated_at=started.updated_at,
    ).record
    one.refresh_from_db()
    two.refresh_from_db()
    assert one.end_year == two.end_year == 2027
    assert list(plantings_in_year(2026)) == [one, two]
    assert not plantings_in_year(2028).exists()
    assert two.events.get(pk=event.pk).actual_date == date(2025, 9, 1)
    reopened = change(user, completed, status="ongoing", completed_on=None).record
    assert reopened.status == "ongoing"
    assert all(member.end_year is None for member in reopened.members.all())
    planned = change(user, reopened, status="planned", started_on=None).record
    assert planned.status == "planned" and planned.started_on is None
    assert planned.members.count() == 2


def test_annual_completion_keeps_original_growing_year(user, planting_catalog):
    member = create_planting(
        actor=user,
        crop=planting_catalog[0],
        start_year=2025,
        status="ongoing",
        started_on=date(2025, 3, 1),
    ).record
    cycle = transition_cycle(
        actor=user,
        pk=member.cycle_id,
        action="complete",
        date=date(2026, 1, 3),
        expected_updated_at=member.cycle.updated_at,
    ).record
    member.refresh_from_db()
    assert cycle.status == "completed" and member.start_year == 2025 and member.end_year is None
    assert list(plantings_in_year(2025)) == [member]
    assert not plantings_in_year(2026).exists()


def test_completion_rejects_incompatible_member_years_atomically(user, batch):
    one, two = batch
    with validated_write():
        Planting.objects.filter(pk=two.pk).update(start_year=2028)
    cycle = change(user, one.cycle, status="ongoing", started_on=date(2025, 3, 1)).record
    with pytest.raises(ValidationError):
        transition_cycle(
            actor=user,
            pk=cycle.pk,
            action="complete",
            date=date(2027, 9, 1),
            expected_updated_at=cycle.updated_at,
        )
    cycle.refresh_from_db()
    assert cycle.status == "ongoing" and cycle.completed_on is None
    assert all(member.end_year is None for member in cycle.members.all())


def test_transition_receipt_replays_original_submission_after_later_edit(user, batch):
    cycle = batch[0].cycle
    key = uuid4()
    values = dict(
        actor=user,
        pk=cycle.pk,
        action="start",
        date=date(2025, 3, 1),
        expected_updated_at=cycle.updated_at,
        submission_key=key,
    )
    first = transition_cycle(**values).record
    change(user, first, status="completed", completed_on=date(2027, 9, 1))
    retry = transition_cycle(**values)
    assert retry.replayed and retry.record.status == "completed"
    with pytest.raises(RegisterConflict):
        transition_cycle(**{**values, "date": date(2025, 3, 2)})
    assert CycleSubmission.objects.filter(pk=key).count() == 1


def test_category_deletion_preserves_members_and_matching_edit_retry(user, batch):
    sale = create_planting_category(actor=user, name="For Sale").record
    cycle = batch[0].cycle
    values = dict(
        actor=user,
        pk=cycle.pk,
        status="planned",
        category_ids=[sale.pk],
        expected_updated_at=cycle.updated_at,
        submission_key=uuid4(),
    )
    edit_cycle(**values)
    delete_planting_category(actor=user, pk=sale.pk, expected_updated_at=sale.updated_at)
    assert Planting.objects.count() == 2 and PlantingCycle.objects.count() == 1
    assert not PlantingCategory.objects.exists()
    retry = edit_cycle(**values)
    assert retry.replayed and not retry.record.categories.exists()


def test_category_uniqueness_stale_edit_and_deleted_receipt(user):
    key = uuid4()
    category = create_planting_category(actor=user, name="Åsa  Grön", submission_key=key).record
    with pytest.raises(ValidationError):
        create_planting_category(actor=user, name="A\u030asa\tGRO\u0308N")
    edited = edit_planting_category(
        actor=user, pk=category.pk, name="Sale", expected_updated_at=category.updated_at
    ).record
    with pytest.raises(RegisterConflict):
        edit_planting_category(
            actor=user, pk=category.pk, name="Old", expected_updated_at=category.updated_at
        )
    delete_planting_category(actor=user, pk=edited.pk, expected_updated_at=edited.updated_at)
    with pytest.raises(RegisterConflict):
        create_planting_category(actor=user, name="Åsa  Grön", submission_key=key)


def test_member_changes_make_shared_cycle_forms_stale(user, batch):
    one, two = batch
    old = one.cycle.updated_at
    one.refresh_from_db()
    edit_planting(
        actor=user,
        pk=one.pk,
        crop=one.crop_id,
        variety=one.variety_id,
        area=one.area_id,
        start_year=2026,
        lifespan="persistent",
        expected_updated_at=one.updated_at,
    )
    with pytest.raises(RegisterConflict):
        transition_cycle(
            actor=user,
            pk=one.cycle_id,
            action="start",
            date=date(2025, 3, 1),
            expected_updated_at=old,
        )
    current = PlantingCycle.objects.get(pk=one.cycle_id)
    delete_planting(actor=user, pk=two.pk, expected_updated_at=two.updated_at)
    with pytest.raises(RegisterConflict):
        change(user, current)


def test_simultaneous_transitions_accept_only_one_version(user, batch):
    cycle = batch[0].cycle
    barrier = Barrier(2)

    def write():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return transition_cycle(
                actor=user,
                pk=cycle.pk,
                action="start",
                date=date(2025, 3, 1),
                expected_updated_at=cycle.updated_at,
            )
        except RegisterConflict:
            return None
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _value: write(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert CycleSubmission.objects.count() == 1


@pytest.mark.parametrize(
    "values",
    [
        {"status": "other"},
        {"status": "planned", "started_on": date(2025, 1, 1)},
        {"status": "ongoing", "completed_on": date(2025, 1, 1)},
        {"status": "completed", "started_on": date(2026, 1, 1), "completed_on": date(2025, 1, 1)},
    ],
)
def test_sqlite_enforces_cycle_status_and_dates(values):
    with pytest.raises(IntegrityError), transaction.atomic(), validated_write():
        PlantingCycle.objects.create(**values)


def test_sqlite_enforces_member_cycle_identity(user, planting_catalog):
    member = create_planting(actor=user, crop=planting_catalog[0], start_year=2026).record
    with pytest.raises(IntegrityError), transaction.atomic(), validated_write():
        Planting.objects.filter(pk=member.pk).update(batch_key=uuid4())


def test_cycle_and_category_writers_reject_inactive_actor(user, batch):
    user.is_active = False
    with pytest.raises(PermissionDenied):
        change(user, batch[0].cycle)
    with pytest.raises(PermissionDenied):
        create_planting_category(actor=user, name="Sale")


def test_forms_current_status_in_past_year_and_safe_returns(client, user, batch):
    one, two = batch
    completed = change(user, one.cycle, status="completed", completed_on=date(2027, 9, 1)).record
    client.force_login(user)
    path = reverse("garden:planting-status", args=["completed"])
    response = client.get(path, {"year": 2026})
    assert len(response.context["cycle_groups"]) == 1
    assert str(completed.pk) in response.text
    detail = client.get(
        completed.get_absolute_url(), {"next": path + "?year=2026"}, HTTP_HX_REQUEST="true"
    )
    assert detail.status_code == 200 and "2025–2027" in detail.text
    assert len(detail.context["members"]) == 2 and "<html" not in detail.text
    edit = client.get(
        reverse("garden:cycle-edit", args=[completed.pk]), {"next": "https://example.invalid/"}
    )
    assert edit.context["return_url"] == ""
    client.logout()
    for target in (
        completed.get_absolute_url(),
        path,
        reverse("garden:cycle-edit", args=[completed.pk]),
        reverse("garden:planting-category-create"),
    ):
        assert client.get(target).status_code == 302


def test_cycle_edit_accepts_checkbox_ids_and_preserves_return_scope(client, user, batch):
    sale = create_planting_category(actor=user, name="For Sale").record
    client.force_login(user)
    path = reverse("garden:cycle-edit", args=[batch[0].cycle_id])
    form = client.get(path).context["form"]
    destination = reverse("garden:planting-status", args=["planned"]) + "?year=2026"
    data = {
        "submission_key": str(form["submission_key"].value()),
        "expected_updated_at": form["expected_updated_at"].value(),
        "status": "planned",
        "category_ids": [str(sale.pk)],
        "next": destination,
    }
    response = client.post(path, data)
    assert response.status_code == 302 and response["Location"] == destination
    assert batch[0].cycle.categories.get().pk == sale.pk
    assert client.post(path, data).status_code == 302


@pytest.mark.parametrize("status", ["ongoing", "completed"])
@pytest.mark.parametrize("hx", [False, True])
def test_unknown_date_category_edit_retries_after_later_status_change(
    client, user, batch, status, hx
):
    cycle = batch[0].cycle
    # Migrated cycles retain unknown actual dates.
    with validated_write():
        PlantingCycle.objects.filter(pk=cycle.pk).update(status=status)
    sale = create_planting_category(actor=user, name="For Sale").record
    client.force_login(user)
    path = reverse("garden:cycle-edit", args=[cycle.pk])
    form = client.get(path).context["form"]
    data = {
        "submission_key": str(form["submission_key"].value()),
        "expected_updated_at": form["expected_updated_at"].value(),
        "status": status,
        "started_on": "",
        "completed_on": "",
        "category_ids": [str(sale.pk)],
    }
    headers = {"HTTP_HX_REQUEST": "true"} if hx else {}
    success = 204 if hx else 302
    assert client.post(path, data, **headers).status_code == success
    cycle.refresh_from_db()
    later = change(
        user,
        cycle,
        status="completed" if status == "ongoing" else "ongoing",
        completed_on=date(2027, 9, 1) if status == "ongoing" else None,
        started_on=date(2025, 3, 1) if status == "completed" else None,
    ).record
    receipts = CycleSubmission.objects.count()
    retry = client.post(path, data, **headers)
    assert retry.status_code == success
    assert retry["HX-Redirect" if hx else "Location"] == cycle.get_absolute_url()
    assert CycleSubmission.objects.count() == receipts
    cycle.refresh_from_db()
    assert (cycle.status, cycle.started_on, cycle.completed_on, cycle.updated_at) == (
        later.status,
        later.started_on,
        later.completed_on,
        later.updated_at,
    )
    field = "started_on" if status == "ongoing" else "completed_on"
    assert client.post(path, {**data, field: "2025-09-01"}, **headers).status_code == 409
    fresh = {**data, "submission_key": str(uuid4()), "expected_updated_at": later.updated_at}
    rejected = client.post(path, fresh, **headers)
    assert rejected.status_code == 422 and field in rejected.context["form"].errors
    assert CycleSubmission.objects.count() == receipts


def test_category_assignment_preserves_member_versions(user, batch):
    sale = create_planting_category(actor=user, name="For Sale").record
    versions = {member.pk: member.updated_at for member in batch}
    changed = change(user, batch[0].cycle, category_ids=[sale.pk]).record
    assert changed.updated_at != batch[0].cycle.updated_at
    assert {member.pk: member.updated_at for member in changed.members.all()} == versions
