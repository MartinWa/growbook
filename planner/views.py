"""Read-only month views: the calendar never writes in this phase."""

from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from . import queries


@require_GET
def current_month(request):
    today = timezone.localdate()
    return redirect("planner:month", year=today.year, month=today.month)


@require_GET
def month_view(request, year, month):
    today = timezone.localdate()
    if not queries.year_is_valid(year, today) or not queries.month_is_valid(month):
        raise Http404
    return render(request, "planner/month.html", queries.month_context(year, month, today=today))
