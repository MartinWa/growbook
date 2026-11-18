"""Financial reference checks called inside serialized garden maintenance writes."""

from garden.queries import plantings_in_year

from .models import Allocation, AllocationCycle, Expense, Sale


def planting_referenced(planting):
    if (
        AllocationCycle.objects.filter(cycle_id=planting.cycle_id).exists()
        or Sale.objects.filter(cycle_id=planting.cycle_id).exists()
    ):
        return True
    years = (
        Allocation.objects.filter(scope=Allocation.Scope.YEAR)
        .values_list("growing_year", flat=True)
        .distinct()
    )
    return any(
        plantings_in_year(year).filter(cycle_id=planting.cycle_id).exists() for year in years
    )


def variety_referenced(variety):
    return (
        Expense.objects.filter(variety=variety).exists()
        or Sale.objects.filter(variety=variety).exists()
    )


def sold_variety_referenced(planting, previous_variety_id):
    return (
        previous_variety_id is not None
        and Sale.objects.filter(cycle_id=planting.cycle_id, variety_id=previous_variety_id).exists()
    )
