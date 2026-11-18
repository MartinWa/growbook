"""Current estimates computed from saved choices, without mutating or consuming balances."""

from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Prefetch

from garden.models import Planting

from .models import Allocation, Expense, Sale
from .money import money, split_cents


def in_year(planting, year):
    if planting.lifespan == Planting.Lifespan.ANNUAL:
        return planting.start_year == year
    return planting.start_year <= year <= (planting.end_year or 9999)


def equipment_schedule(expense):
    if expense.useful_life_years is None:
        return {}
    shares = split_cents(expense.total_cents, [1] * expense.useful_life_years)
    return {expense.purchase_year + offset: cents for offset, cents in enumerate(shares)}


def allocation_budget(expense, year):
    if expense.useful_life_years is None:
        return expense.total_cents
    return equipment_schedule(expense).get(year, 0)


def cost_per_plant(cents, quantity):
    return money(cents) / quantity if quantity is not None and quantity > 0 else None


@dataclass
class MemberShare:
    planting: Planting
    cents: int


@dataclass
class CycleShare:
    cycle: object
    members: list
    cents: int | None = None
    member_shares: list = field(default_factory=list)

    @property
    def label(self):
        return self.members[0].label or str(self.members[0].crop)

    @property
    def quantity(self):
        return (
            None
            if any(member.quantity is None for member in self.members)
            else sum(member.quantity for member in self.members)
        )

    @property
    def amount(self):
        return money(self.cents) if self.cents is not None else None

    @property
    def per_plant(self):
        return cost_per_plant(self.cents, self.quantity) if self.cents is not None else None


@dataclass
class AllocationEstimate:
    allocation: Allocation
    groups: list
    pending: bool = False
    reason: str = ""


def estimate_allocation(allocation, plantings):
    if allocation.scope == Allocation.Scope.YEAR:
        selected = {
            planting.cycle_id
            for planting in plantings
            if in_year(planting, allocation.growing_year)
        }
    else:
        selected = {link.cycle_id for link in allocation.cycle_links.all()}
    cycles = {}
    for planting in sorted(plantings, key=lambda item: item.pk):
        if planting.cycle_id in selected:
            group = cycles.setdefault(planting.cycle_id, CycleShare(planting.cycle, []))
            group.members.append(planting)
    groups = sorted(cycles.values(), key=lambda group: str(group.cycle.pk))
    estimate = AllocationEstimate(allocation, groups)
    if not groups or len(cycles) != len(selected):
        estimate.pending, estimate.reason = True, "empty"
        return estimate
    if allocation.method == Allocation.Method.PLANTS:
        quantities = [group.quantity for group in groups]
        if any(quantity is None for quantity in quantities):
            estimate.pending, estimate.reason = True, "unknown"
            return estimate
        if sum(quantities) == 0:
            estimate.pending, estimate.reason = True, "zero"
            return estimate
        weights = quantities
    else:
        weights = [1] * len(groups)
    for group, cents in zip(groups, split_cents(allocation.amount_cents, weights), strict=True):
        group.cents = cents
        member_weights = (
            [member.quantity for member in group.members]
            if allocation.method == Allocation.Method.PLANTS
            else [1] * len(group.members)
        )
        shares = (
            split_cents(cents, member_weights) if sum(member_weights) else [0] * len(group.members)
        )
        group.member_shares = [
            MemberShare(member, share) for member, share in zip(group.members, shares, strict=True)
        ]
    return estimate


@dataclass
class CostPart:
    expense: Expense
    year: int
    cents: int
    crop: object = None
    variety: object = None
    planting: Planting | None = None
    allocation: Allocation | None = None

    @property
    def cycle_id(self):
        return self.planting.cycle_id if self.planting else None


@dataclass
class ExpenseEstimate:
    expense: Expense
    parts: list
    allocations: list
    schedule: dict

    def available(self, year):
        selected = [
            estimate.allocation
            for estimate in self.allocations
            if self.expense.useful_life_years is None or estimate.allocation.growing_year == year
        ]
        return allocation_budget(self.expense, year) - sum(
            allocation.amount_cents for allocation in selected
        )


def estimate_expense(expense, plantings):
    allocations = [
        estimate_allocation(allocation, plantings) for allocation in expense.allocations.all()
    ]
    schedule = equipment_schedule(expense)
    baseline = (
        dict(schedule)
        if expense.useful_life_years
        else {expense.purchase_year: expense.total_cents}
    )
    parts = []
    for estimate in allocations:
        allocation = estimate.allocation
        base_year = allocation.growing_year if expense.useful_life_years else expense.purchase_year
        baseline[base_year] -= allocation.amount_cents
        if estimate.pending:
            parts.append(
                CostPart(
                    expense,
                    allocation.growing_year,
                    allocation.amount_cents,
                    expense.crop,
                    expense.variety,
                    allocation=allocation,
                )
            )
        else:
            for group in estimate.groups:
                for share in group.member_shares:
                    parts.append(
                        CostPart(
                            expense,
                            allocation.growing_year,
                            share.cents,
                            share.planting.crop,
                            share.planting.variety,
                            share.planting,
                            allocation,
                        )
                    )
    for year, cents in baseline.items():
        parts.append(CostPart(expense, year, cents, expense.crop, expense.variety))
    return ExpenseEstimate(expense, parts, allocations, schedule)


@dataclass
class EconomyState:
    expenses: list
    plantings: list
    sales: list
    estimates: list


def load_economy():
    # Materialize one short, consistent snapshot; calculate after releasing the writer.
    with transaction.atomic():
        expenses = list(
            Expense.objects.select_related("crop", "variety__crop").prefetch_related(
                "allocations__cycle_links"
            )
        )
        plantings = list(Planting.objects.select_related("crop", "variety__crop", "cycle"))
        sales = list(
            Sale.objects.select_related("variety__crop", "cycle").prefetch_related(
                Prefetch(
                    "cycle__members",
                    queryset=Planting.objects.select_related("crop", "variety__crop"),
                )
            )
        )
    return EconomyState(
        expenses, plantings, sales, [estimate_expense(expense, plantings) for expense in expenses]
    )
