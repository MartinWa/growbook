"""Exact spending, current allocations, equipment periods, sales and reference integrity."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone, translation

from core.writes import validated_write
from economy.calculations import equipment_schedule, load_economy
from economy.models import Allocation, EconomySubmission, Expense, Sale
from economy.money import split_cents
from economy.queries import expense_detail, overview
from economy.services import (
    EconomyConflict,
    create_allocation,
    create_expense,
    create_sale,
    delete_allocation,
    delete_expense,
    delete_sale,
    edit_expense,
    edit_sale,
)
from garden.models import Planting
from garden.services import (
    archive_crop,
    create_crop,
    create_planting,
    create_variety,
    delete_crop,
    delete_planting,
    delete_variety,
    edit_planting,
    edit_variety,
)

pytestmark = pytest.mark.django_db(transaction=True)
LOCALDATE = timezone.localdate


@pytest.fixture(autouse=True)
def purchase_calendar(monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: date(2026, 5, 9))
    with translation.override("en"):
        yield


@pytest.fixture
def catalog(user):
    tomato = create_crop(actor=user, name="Tomato").record
    bean = create_crop(actor=user, name="Bean").record
    golden = create_variety(actor=user, crop=tomato, name="Golden").record
    red = create_variety(actor=user, crop=tomato, name="Red").record
    first = create_planting(
        actor=user, crop=tomato, variety=golden, start_year=2026, quantity=50
    ).record
    second = create_planting(
        actor=user, crop=tomato, variety=red, start_year=2026, quantity=50
    ).record
    other = create_planting(actor=user, crop=bean, start_year=2026, quantity=50).record
    return tomato, bean, golden, red, first, second, other


def allocate(user, expense, cycles=(), **values):
    expense.refresh_from_db()
    return create_allocation(
        actor=user,
        expense=expense,
        expected_expense_updated_at=expense.updated_at,
        growing_year=values.pop("growing_year", 2026),
        cycle_ids=[member.cycle_id for member in cycles],
        **values,
    ).record


def quantity(user, record, value):
    record.refresh_from_db()
    return edit_planting(
        actor=user,
        pk=record.pk,
        expected_updated_at=record.updated_at,
        crop=record.crop_id,
        variety=record.variety_id,
        start_year=record.start_year,
        lifespan=record.lifespan,
        end_year=record.end_year,
        quantity=value,
    ).record


def report(year=2026, **filters):
    return overview(load_economy(), year, **filters)


def test_estimates_do_not_read_shared_state_after_releasing_the_snapshot(
    user, catalog, django_assert_num_queries
):
    golden, first, second = catalog[2], catalog[4], catalog[5]
    expense = create_expense(
        actor=user, description="Seeds", total_price="100", variety=golden
    ).record
    allocate(user, expense, [first, second], amount="60")
    create_sale(actor=user, description="Plants", quantity=1, unit_price="10", cycle=first.cycle_id)
    state = load_economy()
    with django_assert_num_queries(0):
        overview(state, 2026)
        expense_detail(state.estimates[0], 2026)


def test_purchase_needs_only_description_date_and_price_and_has_a_visible_balance(user):
    expense = create_expense(actor=user, description="Soil", total_price="100.10").record
    assert expense.purchased_on == date(2026, 5, 9) and expense.purchase_year == 2026
    totals = report()["totals"]
    assert totals["spent"] == totals["growing"] == totals["unallocated"] == Decimal("100.10")
    assert totals["allocated"] == 0 and totals["cash_difference"] == Decimal("-100.10")
    assert not expense.allocations.exists()


def test_new_purchase_year_is_garden_local_and_a_saved_purchase_keeps_its_year(
    user, settings, monkeypatch
):
    monkeypatch.setattr(timezone, "localdate", LOCALDATE)
    settings.TIME_ZONE = "Europe/Stockholm"
    monkeypatch.setattr(timezone, "now", lambda: datetime(2026, 12, 31, 23, 30, tzinfo=UTC))
    key = uuid4()
    expense = create_expense(
        actor=user, description="Trays", total_price="90", submission_key=key
    ).record
    assert expense.purchase_year == 2027 and expense.purchased_on == date(2027, 1, 1)
    monkeypatch.setattr(timezone, "now", lambda: datetime(2028, 2, 1, tzinfo=UTC))
    replay = create_expense(actor=user, description="Trays", total_price="90", submission_key=key)
    assert replay.replayed and replay.record.pk == expense.pk
    edit_expense(
        actor=user,
        pk=expense.pk,
        expected_updated_at=expense.updated_at,
        description="Trays corrected",
        total_price="90",
        purchased_on=expense.purchased_on,
    )
    expense.refresh_from_db()
    assert expense.purchase_year == 2027
    with pytest.raises(ValidationError, match="recorded purchase year"):
        create_expense(
            actor=user, description="Old date", purchased_on=date(2027, 12, 31), total_price="10"
        )


@pytest.mark.parametrize("amount", ["0.001", "-1", "NaN", "Infinity", "10000000000.00", 0.1, True])
def test_invalid_or_inexact_money_is_rejected(user, amount):
    with pytest.raises(ValidationError):
        create_expense(actor=user, description="Invalid", total_price=amount)
    assert not Expense.objects.exists()


@pytest.mark.parametrize(
    "unit,value", [("litres", "20.5"), ("kilograms", "0.125"), ("items", "100")]
)
def test_optional_purchased_quantities_have_units_and_do_not_replace_the_price(user, unit, value):
    expense = create_expense(
        actor=user, description="Supplies", total_price="79.90", purchased_quantity=value, unit=unit
    ).record
    expense.refresh_from_db()
    assert expense.purchased_quantity == Decimal(value) and expense.total_price == Decimal("79.90")


@pytest.mark.parametrize(
    "values",
    [
        {"purchased_quantity": "1.5", "unit": "items"},
        {"purchased_quantity": "1", "unit": ""},
        {"unit": "litres"},
        {"purchased_quantity": "0", "unit": "litres"},
        {"useful_life_years": 3, "category": "soil"},
    ],
)
def test_invalid_quantity_pairs_and_equipment_periods_are_rejected(user, values):
    with pytest.raises(ValidationError):
        create_expense(actor=user, description="Invalid", total_price="10", **values)


def test_refining_a_crop_cost_into_shared_cycles_counts_each_portion_once(user, catalog):
    tomato, bean, _, _, first, _, other = catalog
    expense = create_expense(
        actor=user, description="Shared soil", total_price="100", crop=tomato
    ).record
    allocate(user, expense, [first, other], amount="60")
    assert report(crop=tomato.pk)["totals"]["spent"] == Decimal("70")
    assert report(crop=bean.pk)["totals"]["spent"] == Decimal("30")
    totals = report()["totals"]
    assert totals["growing"] == 100 and totals["allocated"] == 60 and totals["unallocated"] == 40
    assert report(cycle=first.cycle_id)["totals"]["growing"] == 30


def test_variety_attribution_without_plantings_is_refined_and_redistributed(user, catalog):
    tomato, _, golden, red, first, second, _ = catalog
    expense = create_expense(
        actor=user, description="Seed packet", total_price="100", variety=golden
    ).record
    assert expense.crop_id == tomato.pk
    assert report(variety=golden.pk)["totals"]["growing"] == 100
    allocate(user, expense, [first, second], amount="40")
    assert report(variety=golden.pk)["totals"]["growing"] == 80
    assert report(variety=red.pk)["totals"]["growing"] == 20
    quantity(user, first, 0)
    assert report(variety=golden.pk)["totals"]["growing"] == 60
    assert report(variety=red.pk)["totals"]["growing"] == 40
    assert report(crop=tomato.pk)["totals"]["growing"] == 100


def test_portion_moves_to_another_year_while_spending_and_unused_balance_stay_in_purchase_year(
    user, catalog
):
    tomato, _, _, _, _, _, _ = catalog
    future = create_planting(actor=user, crop=tomato, start_year=2027, quantity=10).record
    expense = create_expense(
        actor=user,
        description="Fertilizer",
        total_price="100",
        purchased_quantity="5",
        unit="kilograms",
    ).record
    allocate(user, expense, [future], growing_year=2027, quantity_used="2")
    assert report(2026)["totals"]["spent"] == 100
    assert report(2026)["totals"]["growing"] == 60
    assert report(2027)["totals"]["growing"] == report(2027)["totals"]["allocated"] == 40
    assert report(2027)["totals"]["spent"] == 0


def test_full_packet_can_be_charged_even_with_seeds_left_unused(user, catalog):
    _, _, _, _, first, second, _ = catalog
    expense = create_expense(
        actor=user, description="Seeds", total_price="35.99", purchased_quantity="100", unit="items"
    ).record
    allocate(user, expense, [first, second], share="100")
    assert report()["totals"]["allocated"] == Decimal("35.99")
    assert report()["totals"]["unallocated"] == 0


def test_redistribution_never_consumes_the_remaining_purchase_balance(user, catalog):
    _, _, _, _, first, second, _ = catalog
    expense = create_expense(actor=user, description="Soil", total_price="100").record
    allocation = allocate(user, expense, [first, second], amount="80")
    quantity(user, first, 70)
    quantity(user, second, 30)
    assert report(cycle=first.cycle_id)["totals"]["allocated"] == 56
    assert report(cycle=second.cycle_id)["totals"]["allocated"] == 24
    quantity(user, first, 0)
    assert report(cycle=second.cycle_id)["totals"]["allocated"] == 80
    zero = report(cycle=first.cycle_id)["cycle_rows"][0]
    assert zero["quantity"] == 0 and zero["per_plant"] is None
    allocation.refresh_from_db()
    assert allocation.amount_cents == 8000 and report()["totals"]["unallocated"] == 20


@pytest.mark.parametrize("missing", [None, 0])
def test_unknown_or_all_zero_groups_remain_pending_and_reserved(user, catalog, missing):
    _, _, _, _, first, second, other = catalog
    quantity(user, first, missing)
    quantity(user, second, 0)
    expense = create_expense(actor=user, description="Reserved soil", total_price="100").record
    allocation = allocate(user, expense, [first, second], amount="80")
    state = report()
    assert state["totals"]["allocated"] == 0 and state["totals"]["unallocated"] == 100
    assert state["pending_allocations"][0].reason == ("unknown" if missing is None else "zero")
    assert allocation.cycles.count() == 2
    with pytest.raises(ValidationError, match="available balance"):
        allocate(user, expense, [other], amount="20.01")
    allocate(user, expense, [other], amount="20")
    quantity(user, first, 70)
    assert not report()["pending_allocations"] and report()["totals"]["allocated"] == 100


def test_an_unknown_quantity_is_not_treated_as_zero_when_other_plants_are_known(user, catalog):
    _, _, _, _, first, second, _ = catalog
    quantity(user, first, None)
    expense = create_expense(actor=user, description="Seeds", total_price="100").record
    allocate(user, expense, [first, second], amount="100")
    assert report(cycle=second.cycle_id)["totals"]["allocated"] == 0
    assert report(cycle=second.cycle_id)["cycle_rows"][0]["per_plant"] is None
    quantity(user, first, 0)
    assert report(cycle=second.cycle_id)["totals"]["allocated"] == 100


def test_all_year_allocation_includes_new_cycles_and_keeps_its_chosen_amount(user, catalog):
    tomato, _, _, _, _, _, _ = catalog
    expense = create_expense(actor=user, description="General supplies", total_price="100").record
    allocation = allocate(user, expense, amount="60", scope="year")
    new = create_planting(actor=user, crop=tomato, start_year=2026, quantity=150).record
    create_planting(actor=user, crop=tomato, start_year=2027, quantity=300)
    assert report(cycle=new.cycle_id)["totals"]["allocated"] == 30
    allocation.refresh_from_db()
    assert allocation.amount_cents == 6000 and not allocation.cycles.exists()
    assert report()["totals"]["allocated"] == 60 and report()["totals"]["unallocated"] == 40


def test_selected_groups_include_new_varieties_but_not_new_cycles(user, catalog):
    tomato, _, _, red, first, second, _ = catalog
    expense = create_expense(actor=user, description="Soil", total_price="100").record
    allocate(user, expense, [first, second], amount="100")
    new_member = create_planting(
        actor=user, crop=tomato, variety=red, batch_planting=first, start_year=2026, quantity=50
    ).record
    separate = create_planting(actor=user, crop=tomato, start_year=2026, quantity=500).record
    assert report(cycle=first.cycle_id)["totals"]["allocated"] == Decimal("66.67")
    assert report(cycle=second.cycle_id)["totals"]["allocated"] == Decimal("33.33")
    assert report(cycle=separate.cycle_id)["totals"]["allocated"] == 0
    quantity(user, new_member, None)
    assert report()["pending_allocations"]


def test_empty_all_year_groups_reserve_cost_in_the_chosen_growing_year(user, catalog):
    tomato = catalog[0]
    expense = create_expense(actor=user, description="Future soil", total_price="100").record
    allocate(user, expense, amount="40", scope="year", growing_year=2027)
    assert report(2026)["totals"]["growing"] == 60
    assert report(2027)["totals"]["growing"] == report(2027)["totals"]["unallocated"] == 40
    assert report(2027)["pending_allocations"][0].reason == "empty"
    create_planting(actor=user, crop=tomato, start_year=2027, quantity=20)
    assert not report(2027)["pending_allocations"] and report(2027)["totals"]["allocated"] == 40


def test_equipment_has_one_purchase_and_consecutive_annual_costs_including_unused_years(user):
    expense = create_expense(
        actor=user,
        description="Seed trays",
        total_price="900",
        category="equipment",
        useful_life_years=3,
    ).record
    assert equipment_schedule(expense) == {2026: 30000, 2027: 30000, 2028: 30000}
    for year in (2026, 2027, 2028):
        totals = report(year)["totals"]
        assert totals["growing"] == totals["unallocated"] == 300
        assert totals["spent"] == (900 if year == 2026 else 0)
    assert report(2029)["totals"]["growing"] == 0
    estimate = load_economy().estimates[0]
    assert expense_detail(estimate, 2024)["remaining_years"] == 3
    assert expense_detail(estimate, 2027)["remaining_years"] == 1


def test_equipment_rounding_and_changed_periods_preserve_totals_and_existing_choices(user, catalog):
    first = catalog[4]
    expense = create_expense(
        actor=user,
        description="Trays",
        total_price="100",
        category="equipment",
        useful_life_years=3,
    ).record
    assert equipment_schedule(expense) == {2026: 3334, 2027: 3333, 2028: 3333}
    allocate(user, expense, [first], amount="20")
    expense.refresh_from_db()
    edit_expense(
        actor=user,
        pk=expense.pk,
        expected_updated_at=expense.updated_at,
        description="Trays",
        total_price="100",
        category="equipment",
        useful_life_years=4,
    )
    assert report()["totals"]["growing"] == 25 and report()["totals"]["allocated"] == 20
    assert report(2029)["totals"]["growing"] == 25
    expense.refresh_from_db()
    with pytest.raises(ValidationError, match="reserved amount"):
        edit_expense(
            actor=user,
            pk=expense.pk,
            expected_updated_at=expense.updated_at,
            description="Trays",
            total_price="100",
            category="equipment",
            useful_life_years=6,
        )
    expense.refresh_from_db()
    assert expense.useful_life_years == 4


def test_equipment_annual_reservations_cannot_use_other_years_or_survive_an_excluded_year(
    user, catalog
):
    expense = create_expense(
        actor=user,
        description="Equipment",
        total_price="900",
        category="equipment",
        useful_life_years=3,
    ).record
    future = allocate(user, expense, amount="200", scope="year", growing_year=2028)
    with pytest.raises(ValidationError, match="available balance"):
        allocate(user, expense, amount="100.01", scope="year", growing_year=2028)
    with pytest.raises(ValidationError, match="equipment period"):
        allocate(user, expense, amount="10", scope="year", growing_year=2029)
    expense.refresh_from_db()
    with pytest.raises(ValidationError, match="outside the new equipment period"):
        edit_expense(
            actor=user,
            pk=expense.pk,
            expected_updated_at=expense.updated_at,
            description="Equipment",
            total_price="900",
            category="equipment",
            useful_life_years=2,
        )
    delete_allocation(
        actor=user,
        pk=future.pk,
        expected_updated_at=future.updated_at,
        expected_expense_updated_at=expense.updated_at,
    )
    expense.refresh_from_db()
    edit_expense(
        actor=user,
        pk=expense.pk,
        expected_updated_at=expense.updated_at,
        description="Equipment",
        total_price="900",
        category="equipment",
        useful_life_years=2,
    )
    assert report(2026)["totals"]["growing"] == report(2027)["totals"]["growing"] == 450
    assert report(2028)["totals"]["growing"] == 0


def test_rounding_is_stable_total_preserving_and_never_gives_zero_weights_money():
    assert split_cents(100, [1, 1, 1]) == [34, 33, 33]
    assert split_cents(100, [0, 1, 1]) == [0, 50, 50]
    shares = split_cents(999_999_999_999, [1, 2, 7])
    assert sum(shares) == 999_999_999_999 and shares == split_cents(999_999_999_999, [1, 2, 7])


def test_plant_sale_derives_source_and_growing_year_without_reducing_quantity(user, catalog):
    tomato, _, golden, _, first, _, _ = catalog
    sale = create_sale(
        actor=user,
        description="Plants",
        sold_on=date(2027, 1, 4),
        quantity=7,
        unit_price="12.34",
        cycle=first.cycle_id,
    ).record
    assert sale.crop.pk == tomato.pk and sale.variety_id == golden.pk and sale.growing_year == 2026
    assert sale.total_price == Decimal("86.38")
    first.refresh_from_db()
    assert first.quantity == 50
    assert report(2026)["totals"]["income"] == 0 and report(2026)["totals"][
        "growing_income"
    ] == Decimal("86.38")
    assert report(2027)["totals"]["income"] == Decimal("86.38")
    assert report(2026)["totals"]["growing_difference"] == 0
    assert report(2027)["totals"]["growing_difference"] == Decimal("86.38")
    assert report(cycle=first.cycle_id)["cycle_rows"][0]["income"] == Decimal("86.38")


@pytest.mark.parametrize(
    "lifespan,start_year,end_year,expected_year",
    [
        ("annual", 2027, None, 2027),
        ("persistent", 2027, None, 2028),
        ("persistent", 2025, 2027, 2027),
        ("persistent", 2029, None, 2029),
    ],
)
def test_batch_sale_uses_the_selected_variety_growing_year(
    user, catalog, django_assert_num_queries, lifespan, start_year, end_year, expected_year
):
    crop, _, _, red, first, _, _ = catalog
    member = create_planting(
        actor=user,
        crop=crop,
        variety=red,
        batch_planting=first,
        start_year=start_year,
        lifespan=lifespan,
        end_year=end_year,
        quantity=10,
    ).record
    expense = create_expense(actor=user, description="Batch supplies", total_price="100").record
    allocate(user, expense, [first], amount="60", growing_year=expected_year)
    sale = create_sale(
        actor=user,
        description="Selected variety plants",
        sold_on=date(2028, 5, 9),
        quantity=2,
        total_price="20",
        cycle=first.cycle_id,
        variety=red,
    ).record
    assert sale.source_member().pk == member.pk and sale.growing_year == expected_year
    state = load_economy()
    with django_assert_num_queries(0):
        selected_year = overview(state, expected_year, cycle=first.cycle_id)
        first_year = overview(state, first.start_year, cycle=first.cycle_id)
    assert selected_year["totals"]["growing_income"] == 20
    assert selected_year["cycle_rows"][0]["difference"] == -40
    assert first_year["totals"]["growing_income"] == 0


def test_cycle_wide_sale_keeps_the_earliest_member_growing_year(user, catalog):
    crop, _, _, red, first, _, _ = catalog
    create_planting(
        actor=user, crop=crop, variety=red, batch_planting=first, start_year=2027, quantity=10
    )
    sale = create_sale(
        actor=user,
        description="Whole batch plants",
        sold_on=date(2028, 5, 9),
        quantity=2,
        total_price="20",
        cycle=first.cycle_id,
    ).record
    assert sale.variety_id is None
    assert sale.source_member().pk == first.pk and sale.growing_year == first.start_year
    assert report(first.start_year, cycle=first.cycle_id)["cycle_rows"][0]["income"] == 20


def test_yearly_differences_use_received_income_while_cycle_sales_follow_the_growing_year(
    user, catalog
):
    crop, _, _, _, first, _, _ = catalog
    expense = create_expense(
        actor=user,
        description="Seed trays",
        total_price="900",
        category="equipment",
        useful_life_years=3,
        crop=crop,
    ).record
    allocate(user, expense, [first], amount="100")
    create_sale(
        actor=user,
        description="Delayed plant sale",
        sold_on=date(2027, 1, 4),
        quantity=7,
        unit_price="12.34",
        cycle=first.cycle_id,
    )
    for filters in ({}, {"crop": crop.pk}):
        purchase_year = report(2026, **filters)["totals"]
        sale_year = report(2027, **filters)["totals"]
        assert purchase_year["cash_difference"] == -900
        assert purchase_year["growing_difference"] == -300
        assert sale_year["cash_difference"] == Decimal("86.38")
        assert sale_year["growing_difference"] == Decimal("-213.62")
    cycle = report(cycle=first.cycle_id)["cycle_rows"][0]
    assert cycle["income"] == Decimal("86.38") and cycle["difference"] == Decimal("-13.62")


def test_planting_corrections_cannot_orphan_the_sold_variety(user, catalog):
    _, _, _, red, first, _, _ = catalog
    sale = create_sale(
        actor=user, description="Plants", quantity=2, total_price="25", cycle=first.cycle_id
    ).record
    with pytest.raises(ValidationError, match="sales"):
        edit_planting(
            actor=user,
            pk=first.pk,
            expected_updated_at=first.updated_at,
            crop=first.crop_id,
            variety=red,
            start_year=2026,
            quantity=70,
        )
    quantity(user, first, 70)
    assert report()["totals"]["income"] == 25
    delete_sale(actor=user, pk=sale.pk, expected_updated_at=sale.updated_at)
    first.refresh_from_db()
    edited = edit_planting(
        actor=user,
        pk=first.pk,
        expected_updated_at=first.updated_at,
        crop=first.crop_id,
        variety=red,
        start_year=2026,
        quantity=70,
    ).record
    assert edited.variety_id == red.pk


def test_variety_only_sales_and_edit_retries_are_supported_for_every_member(
    user, other_user, catalog
):
    golden = catalog[2]
    sale = create_sale(
        actor=user, description="Plants", quantity=3, total_price="25.50", variety=golden
    ).record
    key = uuid4()
    values = {
        "actor": other_user,
        "pk": sale.pk,
        "expected_updated_at": sale.updated_at,
        "description": "Corrected sale",
        "quantity": 4,
        "total_price": "30",
        "variety": golden,
        "submission_key": key,
    }
    edit_sale(**values)
    assert edit_sale(**values).replayed
    assert report(variety=golden.pk)["totals"]["income"] == 30
    sale.refresh_from_db()
    delete_sale(actor=user, pk=sale.pk, expected_updated_at=sale.updated_at)
    with pytest.raises(EconomyConflict, match="deleted"):
        edit_sale(**values)


def test_sale_requires_a_valid_source_one_price_and_a_positive_whole_quantity(user, catalog):
    _, _, _, red, first, _, _ = catalog
    for values in (
        {"quantity": 0, "unit_price": "10"},
        {"quantity": True, "unit_price": "10"},
        {"quantity": 1},
        {"quantity": 1, "unit_price": "10", "total_price": "10"},
        {"quantity": 1, "unit_price": "10"},
        {"quantity": 1, "unit_price": "10", "cycle": first.cycle_id, "variety": red},
    ):
        with pytest.raises(ValidationError):
            create_sale(actor=user, description="Invalid", **values)
    assert not Sale.objects.exists()


def test_receipts_precede_new_year_archive_and_changed_budget_checks(user, catalog, monkeypatch):
    tomato, _, _, _, first, second, _ = catalog
    purchase_key = uuid4()
    purchase = {
        "actor": user,
        "description": "Soil",
        "total_price": "100",
        "crop": tomato,
        "submission_key": purchase_key,
    }
    expense = create_expense(**purchase).record
    allocation_key = uuid4()
    chosen = {
        "actor": user,
        "expense": expense,
        "expected_expense_updated_at": expense.updated_at,
        "growing_year": 2026,
        "cycle_ids": [first.cycle_id, second.cycle_id],
        "share": "50",
        "submission_key": allocation_key,
    }
    allocation = create_allocation(**chosen).record
    expense.refresh_from_db()
    edited = {
        "actor": user,
        "pk": expense.pk,
        "expected_updated_at": expense.updated_at,
        "description": "Soil",
        "total_price": "200",
        "crop": tomato,
        "submission_key": uuid4(),
    }
    edit_expense(**edited)
    archive_crop(actor=user, pk=tomato.pk)
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: date(2027, 1, 1))
    assert create_expense(**purchase).replayed and edit_expense(**edited).replayed
    assert create_allocation(
        **{**chosen, "cycle_ids": list(reversed(chosen["cycle_ids"]))}
    ).replayed
    allocation.refresh_from_db()
    assert allocation.amount_cents == 5000
    with pytest.raises(EconomyConflict, match="different content"):
        create_expense(**{**purchase, "total_price": "101"})


@pytest.mark.parametrize("reference", ["selected", "pending", "year", "sale"])
def test_financially_referenced_plantings_cannot_be_deleted(user, catalog, reference):
    first = catalog[4]
    if reference == "sale":
        record = create_sale(
            actor=user, description="Plants", quantity=2, unit_price="10", cycle=first.cycle_id
        ).record
    else:
        if reference == "pending":
            first = quantity(user, first, None)
        expense = create_expense(actor=user, description="Soil", total_price="100").record
        allocate(
            user, expense, [first], amount="60", scope="year" if reference == "year" else "selected"
        )
        record = expense
        record.refresh_from_db()
    with pytest.raises(ValidationError, match="economy references"):
        delete_planting(actor=user, pk=first.pk, expected_updated_at=first.updated_at)
    assert Planting.objects.filter(pk=first.pk).exists()
    (delete_sale if reference == "sale" else delete_expense)(
        actor=user, pk=record.pk, expected_updated_at=record.updated_at
    )
    delete_planting(actor=user, pk=first.pk, expected_updated_at=first.updated_at)


def test_direct_financial_references_protect_register_deletion_and_variety_reclassification(user):
    crop = create_crop(actor=user, name="Tomato").record
    other = create_crop(actor=user, name="Bean").record
    variety = create_variety(actor=user, crop=crop, name="Golden").record
    expense = create_expense(
        actor=user, description="Seeds", total_price="10", variety=variety
    ).record
    with pytest.raises(ValidationError):
        delete_variety(actor=user, pk=variety.pk, expected_updated_at=variety.updated_at)
    with pytest.raises(ValidationError, match="economy references"):
        edit_variety(
            actor=user,
            pk=variety.pk,
            expected_updated_at=variety.updated_at,
            crop=other,
            name=variety.name,
        )
    delete_expense(actor=user, pk=expense.pk, expected_updated_at=expense.updated_at)
    sale = create_sale(
        actor=user, description="Plants", quantity=1, total_price="10", variety=variety
    ).record
    with pytest.raises(ValidationError, match="economy references"):
        edit_variety(
            actor=user,
            pk=variety.pk,
            expected_updated_at=variety.updated_at,
            crop=other,
            name=variety.name,
        )
    delete_sale(actor=user, pk=sale.pk, expected_updated_at=sale.updated_at)
    delete_variety(actor=user, pk=variety.pk, expected_updated_at=variety.updated_at)
    expense = create_expense(actor=user, description="Supplies", total_price="10", crop=crop).record
    with pytest.raises(ValidationError):
        delete_crop(actor=user, pk=crop.pk, expected_updated_at=crop.updated_at)
    delete_expense(actor=user, pk=expense.pk, expected_updated_at=expense.updated_at)
    delete_crop(actor=user, pk=crop.pk, expected_updated_at=crop.updated_at)


def test_expense_deletion_removes_allocations_future_shares_and_retains_receipt_tombstones(
    user, catalog
):
    key = uuid4()
    values = {
        "actor": user,
        "description": "Trays",
        "total_price": "900",
        "category": "equipment",
        "useful_life_years": 3,
        "submission_key": key,
    }
    expense = create_expense(**values).record
    allocation = allocate(user, expense, [catalog[4]], amount="100")
    expense.refresh_from_db()
    delete_expense(actor=user, pk=expense.pk, expected_updated_at=expense.updated_at)
    assert not Allocation.objects.exists() and report(2028)["totals"]["growing"] == 0
    assert EconomySubmission.objects.get(pk=key).expense_id is None
    assert EconomySubmission.objects.get(kind="allocation").allocation_id is None
    with pytest.raises(EconomyConflict, match="deleted"):
        create_expense(**values)
    assert allocation.pk is not None and not Expense.objects.exists()


def test_inactive_members_stale_edits_and_accidental_orm_writes_are_rejected(user):
    expense = create_expense(actor=user, description="Soil", total_price="100").record
    old = expense.updated_at
    edit_expense(
        actor=user,
        pk=expense.pk,
        expected_updated_at=old,
        description="Corrected",
        total_price="100",
    )
    with pytest.raises(EconomyConflict):
        delete_expense(actor=user, pk=expense.pk, expected_updated_at=old)
    with pytest.raises(ValidationError):
        edit_expense(
            actor=user,
            pk=expense.pk,
            expected_updated_at=datetime(2026, 1, 1),
            description="Invalid",
            total_price="100",
        )
    with transaction.atomic(), pytest.raises(RuntimeError, match="outside"):
        create_expense(actor=user, description="Invalid", total_price="10")
    with pytest.raises(RuntimeError, match="validated domain operation"):
        Expense.objects.create(description="Invalid")
    user.is_active = False
    user.save(update_fields=["is_active"])
    user.is_active = True
    with pytest.raises(PermissionDenied):
        create_expense(actor=user, description="Invalid", total_price="10")


@pytest.mark.parametrize(
    "values",
    [
        {"total_cents": -1},
        {"total_cents": 1_000_000_000_000},
        {"description": ""},
        {"purchase_year": 2027},
        {"unit": "items", "purchased_quantity": Decimal("1.5")},
        {"category": "soil", "useful_life_years": 3},
        {"category": "equipment", "useful_life_years": 9999},
    ],
)
def test_database_constraints_reject_invalid_purchase_rows_even_when_hooks_are_bypassed(values):
    with validated_write(), transaction.atomic(), pytest.raises(IntegrityError):
        Expense.objects.create(
            **{
                "description": "Synthetic",
                "purchased_on": date(2026, 5, 9),
                "purchase_year": 2026,
                "total_cents": 100,
                **values,
            }
        )
