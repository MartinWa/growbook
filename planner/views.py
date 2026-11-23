"""Read-only month views: the calendar never writes in this phase."""

from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from . import queries
from .forms import CalendarFilterForm

RESULTS_ID = "calendar-results"
FILTER_FIELDS = ("type", "area", "crop", "variety", "planting")
CHIP_LIMIT = 40


def _trim(value):
    text = " ".join(str(value).split())
    return text if len(text) <= CHIP_LIMIT else text[: CHIP_LIMIT - 1] + "…"


def _active_filters(request, form, year, month):
    """One removable chip per chosen filter, keeping the other choices."""
    base = reverse("planner:month", args=[year, month])
    chips = []
    for name in FILTER_FIELDS:
        chosen = request.GET.getlist(name)
        if not chosen:
            continue
        field = form.fields[name]
        if name == "type":
            display = ", ".join(
                str(dict(field.choices).get(value, value)) for value in chosen if value
            )
        else:
            selected = form.cleaned_data.get(name)
            display = field.label_from_instance(selected) if selected is not None else chosen[0]
        remaining = request.GET.copy()
        for key in (name, "item", "day"):
            remaining.pop(key, None)
        chips.append(
            {
                "label": field.label,
                "value": _trim(display),
                "url": f"{base}?{remaining.urlencode()}",
            }
        )
    return chips


@require_GET
def current_month(request):
    today = timezone.localdate()
    url = reverse("planner:month", args=[today.year, today.month])
    query = request.GET.copy()
    for key in ("item", "day"):
        query.pop(key, None)
    if query:
        url = f"{url}?{query.urlencode()}"
    return redirect(url)


@require_GET
def month_view(request, year, month):
    today = timezone.localdate()
    if not queries.year_is_valid(year, today) or not queries.month_is_valid(month):
        raise Http404
    form = CalendarFilterForm(request.GET)
    if not form.is_valid():
        raise Http404
    item = None
    if request.GET.get("item"):
        address = queries.parse_item(request.GET["item"])
        if address is None:
            raise Http404
        item = queries.find_item(year, month, *address)
        if item is None:
            raise Http404
    day = None
    if request.GET.get("day"):
        day = queries.parse_day(request.GET["day"], year, month)
        if day is None:
            raise Http404
    context = queries.month_context(
        year, month, today=today, filters=form.values, item=item, day=day
    )
    close_query = request.GET.copy()
    for key in ("item", "day"):
        close_query.pop(key, None)
    close_url = reverse("planner:month", args=[year, month])
    if close_query:
        close_url = f"{close_url}?{close_query.urlencode()}"
    if context["panel"] is not None:
        context["panel"] = {**context["panel"], "close_url": close_url, "open": True}
    context.update(
        {
            "form": form,
            "results_id": RESULTS_ID,
            "clear_filters_url": reverse("planner:month", args=[year, month]),
            "active_filters": _active_filters(request, form, year, month),
        }
    )
    partial = (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-Target") == RESULTS_ID
    )
    return render(
        request,
        "planner/_month_results.html" if partial else "planner/month.html",
        context,
    )
