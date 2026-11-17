"""Separate purchase-year spending, growing-year costs and current source summaries."""

from django.urls import reverse

from .calculations import cost_per_plant, in_year
from .money import money


def matches(crop, variety, cycle_id, filters):
    return (
        (filters.get("crop") is None or crop is not None and crop.pk == filters["crop"])
        and (
            filters.get("variety") is None
            or variety is not None
            and variety.pk == filters["variety"]
        )
        and (filters.get("cycle") is None or cycle_id == filters["cycle"])
    )


def part_matches(part, filters):
    return matches(part.crop, part.variety, part.cycle_id, filters)


def pending_matches(estimate, filters):
    if not any(filters.get(field) is not None for field in ("crop", "variety", "cycle")):
        return True
    return any(
        matches(member.crop, member.variety, member.cycle_id, filters)
        for group in estimate.groups
        for member in group.members
    )


def _amounts(spent, growing, cash_income, growing_income, allocated):
    return {
        "spent": money(spent),
        "growing": money(growing),
        "income": money(cash_income),
        "growing_income": money(growing_income),
        "cash_difference": money(cash_income - spent),
        "growing_difference": money(cash_income - growing),
        "allocated": money(allocated),
        "unallocated": money(growing - allocated),
    }


def _source_rows(spent, growing, cash_sales, growing_sales, year, *, varieties=False):
    rows = {}

    def row(crop, variety):
        source = variety if varieties else crop
        key = source.pk if source else None
        return rows.setdefault(
            key,
            {
                "source": source,
                "crop": crop,
                "spent": 0,
                "growing": 0,
                "income": 0,
                "growing_income": 0,
                "allocated": 0,
            },
        )

    for part in spent:
        row(part.crop, part.variety)["spent"] += part.cents
    for part in growing:
        item = row(part.crop, part.variety)
        item["growing"] += part.cents
        if part.planting is not None:
            item["allocated"] += part.cents
    for sale in cash_sales:
        row(sale.crop, sale.variety)["income"] += sale.total_cents
    for sale in growing_sales:
        row(sale.crop, sale.variety)["growing_income"] += sale.total_cents
    for item in rows.values():
        item.update(
            _amounts(
                item["spent"],
                item["growing"],
                item["income"],
                item["growing_income"],
                item["allocated"],
            )
        )
        query = {"year": year}
        if item["source"] is not None:
            query["variety" if varieties else "crop"] = item["source"].pk
        item["url"] = reverse("economy:list", query=query)
    return sorted(
        rows.values(),
        key=lambda item: (item["source"] is None, str(item["source"] or "").casefold()),
    )


def overview(state, year, **filters):
    parts = [
        part
        for estimate in state.estimates
        for part in estimate.parts
        if part_matches(part, filters)
    ]
    spent = [part for part in parts if part.expense.purchase_year == year]
    growing = [part for part in parts if part.year == year]
    sales = [
        sale for sale in state.sales if matches(sale.crop, sale.variety, sale.cycle_id, filters)
    ]
    cash_sales = [sale for sale in sales if sale.sold_on.year == year]
    growing_sales = [sale for sale in sales if sale.growing_year == year]
    pending = [
        allocation
        for estimate in state.estimates
        for allocation in estimate.allocations
        if allocation.pending
        and allocation.allocation.growing_year == year
        and pending_matches(allocation, filters)
    ]
    totals = _amounts(
        sum(part.cents for part in spent),
        sum(part.cents for part in growing),
        sum(sale.total_cents for sale in cash_sales),
        sum(sale.total_cents for sale in growing_sales),
        sum(part.cents for part in growing if part.planting is not None),
    )
    expense_rows = []
    for estimate in state.estimates:
        purchase_parts = [part for part in spent if part.expense.pk == estimate.expense.pk]
        used_parts = [part for part in growing if part.expense.pk == estimate.expense.pk]
        unresolved = [
            allocation
            for allocation in pending
            if allocation.allocation.expense_id == estimate.expense.pk
        ]
        if purchase_parts or used_parts or unresolved:
            expense_rows.append(
                {
                    "expense": estimate.expense,
                    "spent": money(sum(part.cents for part in purchase_parts)),
                    "growing": money(sum(part.cents for part in used_parts)),
                    "allocated": money(
                        sum(part.cents for part in used_parts if part.planting is not None)
                    ),
                    "unallocated": money(
                        sum(part.cents for part in used_parts if part.planting is None)
                    ),
                    "pending": bool(unresolved),
                }
            )
    cycles = {}
    referenced = {part.planting.pk for part in growing if part.planting is not None}
    sale_cycles = {sale.cycle_id for sale in growing_sales}
    for planting in sorted(state.plantings, key=lambda member: member.pk):
        if not (
            in_year(planting, year) or planting.pk in referenced or planting.cycle_id in sale_cycles
        ):
            continue
        if not matches(planting.crop, planting.variety, planting.cycle_id, filters):
            continue
        item = cycles.setdefault(
            planting.cycle_id,
            {
                "cycle": planting.cycle,
                "label": planting.label or str(planting.crop),
                "members": [],
                "cost_cents": 0,
                "income_cents": 0,
            },
        )
        item["members"].append(planting)
    for part in growing:
        if part.cycle_id in cycles:
            cycles[part.cycle_id]["cost_cents"] += part.cents
    for sale in growing_sales:
        if sale.cycle_id in cycles:
            cycles[sale.cycle_id]["income_cents"] += sale.total_cents
    pending_cycles = {group.cycle.pk for estimate in pending for group in estimate.groups}
    for key, item in cycles.items():
        item["quantity"] = (
            None
            if any(member.quantity is None for member in item["members"])
            else sum(member.quantity for member in item["members"])
        )
        item["pending"] = key in pending_cycles
        item["per_plant"] = (
            None if item["pending"] else cost_per_plant(item["cost_cents"], item["quantity"])
        )
        item["cost"] = money(item["cost_cents"])
        item["income"] = money(item["income_cents"])
        item["difference"] = money(item["income_cents"] - item["cost_cents"])
        item["url"] = reverse("economy:list", query={"year": year, "cycle": key})
    return {
        "totals": totals,
        "expense_rows": expense_rows,
        "sales": [sale for sale in sales if sale.sold_on.year == year or sale.growing_year == year],
        "pending_allocations": pending,
        "crop_rows": _source_rows(spent, growing, cash_sales, growing_sales, year),
        "variety_rows": _source_rows(
            spent, growing, cash_sales, growing_sales, year, varieties=True
        ),
        "cycle_rows": list(cycles.values()),
    }


def expense_detail(estimate, year):
    schedule = []
    for scheduled_year, cents in estimate.schedule.items():
        allocated = sum(
            part.cents
            for part in estimate.parts
            if part.year == scheduled_year and part.planting is not None
        )
        reserved = sum(
            item.allocation.amount_cents
            for item in estimate.allocations
            if item.allocation.growing_year == scheduled_year and item.pending
        )
        schedule.append(
            {
                "year": scheduled_year,
                "cost": money(cents),
                "allocated": money(allocated),
                "unallocated": money(cents - allocated),
                "reserved": money(reserved),
            }
        )
    current = [part for part in estimate.parts if part.year == year]
    return {
        "expense": estimate.expense,
        "year": year,
        "allocations": estimate.allocations,
        "growing_cost": money(sum(part.cents for part in current)),
        "allocated": money(sum(part.cents for part in current if part.planting is not None)),
        "unallocated": money(sum(part.cents for part in current if part.planting is None)),
        "available": money(estimate.available(year)),
        "annual_share": money(estimate.schedule.get(year, 0)),
        "future_shares": money(
            sum(
                cents
                for scheduled_year, cents in estimate.schedule.items()
                if scheduled_year > year
            )
        ),
        "remaining_years": sum(scheduled_year > year for scheduled_year in estimate.schedule),
        "last_year": max(estimate.schedule, default=None),
        "schedule": schedule,
    }
