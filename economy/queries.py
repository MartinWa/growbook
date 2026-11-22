"""Landing summaries, filtered records and the cost-per-plant reads.

A selected year is the page context; ``year=None`` aggregates every year and cuts
scheduled equipment shares at ``through_year`` so totals never contain costs that
have not happened yet.
"""

from django.urls import reverse

from garden.queries import cycle_years

from .calculations import cost_per_plant, in_year
from .money import money


def year_query(year):
    return {"year": year} if year is not None else {"all_years": 1}


def records_url(year, **values):
    """The records route, with ``all=1`` so the year and filters stay in view."""
    return reverse("economy:records", query={**year_query(year), "all": 1, **values})


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
        values = {"variety" if varieties else "crop": item["source"].pk} if item["source"] else {}
        item["url"] = records_url(year, **values)
    return sorted(
        rows.values(),
        key=lambda item: (item["source"] is None, str(item["source"] or "").casefold()),
    )


def available_balance(estimate, year, through_year=None):
    """Reserved amounts reduce the balance; future scheduled shares are excluded."""
    if year is not None:
        return estimate.available(year)
    cutoff = 9999 if through_year is None else through_year
    years = estimate.schedule or {estimate.expense.purchase_year: estimate.expense.total_cents}
    return sum(estimate.available(item) for item in years if item <= cutoff)


def _in_scope(estimate, year, through_year):
    if year is not None:
        return estimate.expense.purchase_year == year or any(
            part.year == year for part in estimate.parts
        )
    cutoff = 9999 if through_year is None else through_year
    return estimate.expense.purchase_year <= cutoff or any(
        part.year <= cutoff for part in estimate.parts
    )


def overview(state, year, *, through_year=None, **filters):
    """Totals and rows for one year, or for every year when ``year`` is None."""
    cutoff = 9999 if through_year is None else through_year
    all_years = year is None
    parts = [
        part
        for estimate in state.estimates
        for part in estimate.parts
        if part_matches(part, filters)
    ]
    sales = [
        sale for sale in state.sales if matches(sale.crop, sale.variety, sale.cycle_id, filters)
    ]
    if all_years:
        spent = [part for part in parts if part.expense.purchase_year <= cutoff]
        growing = [part for part in parts if part.year <= cutoff]
        cash_sales = [sale for sale in sales if sale.sold_on.year <= cutoff]
        growing_sales = [sale for sale in sales if sale.growing_year <= cutoff]
    else:
        spent = [part for part in parts if part.expense.purchase_year == year]
        growing = [part for part in parts if part.year == year]
        cash_sales = [sale for sale in sales if sale.sold_on.year == year]
        growing_sales = [sale for sale in sales if sale.growing_year == year]
    pending = [
        allocation
        for estimate in state.estimates
        for allocation in estimate.allocations
        if allocation.pending
        and (all_years or allocation.allocation.growing_year == year)
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
        if not (purchase_parts or used_parts or unresolved):
            continue
        # The purchase page reopens the share being browsed; All years needs one year.
        detail_year = year if year is not None else estimate.expense.purchase_year
        if year is None and any(part.year == cutoff for part in estimate.parts):
            detail_year = cutoff
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
                "available": money(available_balance(estimate, year, cutoff)),
                "detail_year": detail_year,
                "pending": bool(unresolved),
            }
        )
    cycles = {}
    referenced = {part.planting.pk for part in growing if part.planting is not None}
    sale_cycles = {sale.cycle_id for sale in growing_sales}
    selected = set()
    for planting in state.plantings:
        if year is None:
            relevant = planting.start_year <= cutoff
        else:
            relevant = (
                in_year(planting, year)
                or planting.pk in referenced
                or planting.cycle_id in sale_cycles
            )
        if relevant and matches(planting.crop, planting.variety, planting.cycle_id, filters):
            selected.add(planting.cycle_id)
    for planting in sorted(state.plantings, key=lambda member: member.pk):
        # A cycle's plant count covers every member, matching the split that shares its cost.
        if planting.cycle_id not in selected:
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
    pending_cycles = {
        group.cycle.pk
        for estimate in pending
        if not all_years or estimate.allocation.growing_year <= cutoff
        for group in estimate.groups
    }
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
        item["years"] = cycle_years(item["members"])
        item["url"] = reverse("economy:cost-per-plant-cycle", args=[key], query=year_query(year))
    return {
        "totals": totals,
        "expense_rows": expense_rows,
        "sales": cash_sales,
        "pending_allocations": pending,
        "crop_rows": _source_rows(spent, growing, cash_sales, growing_sales, year),
        "variety_rows": _source_rows(
            spent, growing, cash_sales, growing_sales, year, varieties=True
        ),
        "cycle_rows": list(cycles.values()),
    }


def _expense_order(row):
    return (row["expense"].purchased_on, row["expense"].created_at)


def _sale_order(sale):
    return (sale.sold_on, sale.created_at)


def landing(state, year, *, through_year=None):
    """Pending reservations and unallocated balances for the landing page."""
    data = overview(state, year, through_year=through_year)
    by_expense = {estimate.expense.pk: estimate for estimate in state.estimates}
    expenses = data["expense_rows"]
    sales = data["sales"]
    groups = {}
    for estimate in sorted(
        data["pending_allocations"],
        key=lambda item: (item.allocation.growing_year, item.allocation.pk),
    ):
        groups.setdefault(estimate.allocation.growing_year, []).append(estimate)
    balances = [
        row
        for row in expenses
        if row["available"] > 0
        and _in_scope(
            by_expense[row["expense"].pk],
            year,
            through_year,
        )
    ]
    return {
        **data,
        "pending_groups": [
            {"year": growing_year, "estimates": estimates}
            for growing_year, estimates in groups.items()
        ],
        "unallocated_purchases": balances,
        "has_records": bool(expenses or sales),
    }


def cycle_index(state, year, *, through_year=None):
    """Cycles that carry growing costs or plant-sale income, with cost per plant."""
    data = overview(state, year, through_year=through_year)
    return {
        "cycle_rows": [
            row
            for row in data["cycle_rows"]
            if row["cost_cents"] or row["income_cents"] or row["pending"]
        ],
        "totals": data["totals"],
        "crop_rows": data["crop_rows"],
        "variety_rows": data["variety_rows"],
    }


def cycle_breakdown(state, year, cycle, *, through_year=None):
    """Each allocation that reaches the cycle, using the current split estimate."""
    cutoff = 9999 if through_year is None else through_year
    rows = []
    for estimate in state.estimates:
        for allocation in estimate.allocations:
            if allocation.allocation.growing_year > cutoff or (
                year is not None and allocation.allocation.growing_year != year
            ):
                continue
            for group in allocation.groups:
                if group.cycle.pk != cycle.pk:
                    continue
                if allocation.pending:
                    rows.append(
                        {
                            "expense": estimate.expense,
                            "allocation": allocation.allocation,
                            "amount": None,
                            "reserved": money(allocation.allocation.amount_cents),
                            "quantity": group.quantity,
                            "pending": True,
                            "reason": allocation.reason,
                            "per_plant": None,
                        }
                    )
                    continue
                cents = sum(share.cents for share in group.member_shares)
                rows.append(
                    {
                        "expense": estimate.expense,
                        "allocation": allocation.allocation,
                        "amount": money(cents),
                        "reserved": money(0),
                        "quantity": group.quantity,
                        "pending": False,
                        "reason": "",
                        "per_plant": cost_per_plant(cents, group.quantity),
                    }
                )
    return rows


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
