from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import OperationalError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.forms import DeleteForm, form_error, valid_post
from garden.models import PlantingCycle

from . import queries, services
from .calculations import (
    allocation_budget,
    estimate_allocation,
    in_year,
    load_economy,
    load_plantings,
)
from .forms import (
    AllocationDeleteForm,
    AllocationForm,
    AllocationPreviewForm,
    EconomyFilterForm,
    ExpenseForm,
    SaleForm,
)
from .models import RECORDS_PAGE_SIZE, Allocation, Expense, Sale
from .money import money
from .queries import cycle_breakdown, cycle_index, expense_detail, landing, overview


def _year(request, default=None):
    value = request.POST.get("year", request.GET.get("year", default or timezone.localdate().year))
    try:
        return forms.IntegerField(min_value=1, max_value=9999).clean(value)
    except ValidationError as exc:
        raise Http404 from exc


def _back_url(request, year, *, list_only=False):
    value = request.POST.get("next", request.GET.get("next", ""))
    if value.startswith("/") and url_has_allowed_host_and_scheme(value, allowed_hosts=None):
        try:
            match = resolve(urlsplit(value).path)
        except Resolver404:
            pass
        else:
            if match.view_name in {"economy:list", "economy:records"} or (
                not list_only and match.view_name == "economy:expense-detail"
            ):
                return value
    return reverse("economy:list", query={"year": year})


def _scope(request):
    """The selected growing year, or ``None`` for every year."""
    if request.GET.get("all_years") == "1":
        return None, True
    value = request.GET.get("year") or timezone.localdate().year
    try:
        year = forms.IntegerField(min_value=1, max_value=9999).clean(value)
    except ValidationError as exc:
        raise Http404 from exc
    return year, False


def _navigation(request, route, year, args=()):
    """Previous, next and *All years* links that keep the current filters."""
    data = request.GET.copy()
    for key in ("all_years", "page_expenses", "page_income"):
        data.pop(key, None)
    if year is not None:
        data["year"] = year
    else:
        data.pop("year", None)
    every_year = data.copy()
    every_year.pop("year", None)
    every_year["all_years"] = 1
    previous_url = next_url = None
    if year is not None:
        if year > 1:
            previous = data.copy()
            previous["year"] = year - 1
            previous_url = reverse(route, args=args, query=previous)
        if year < 9999:
            following = data.copy()
            following["year"] = year + 1
            next_url = reverse(route, args=args, query=following)
    return {
        "all_years_url": reverse(route, args=args, query=every_year),
        "previous_url": previous_url,
        "next_url": next_url,
    }


def _through_year(year):
    """Future scheduled shares never enter aggregated totals."""
    return timezone.localdate().year if year is None else None


@require_GET
def economy_list(request):
    year, all_years = _scope(request)
    context = {
        "title": _("Economy"),
        "year": year,
        "all_years": all_years,
        "records_url": queries.records_url(year),
        **landing(load_economy(), year, through_year=_through_year(year)),
        **_navigation(request, "economy:list", year),
    }
    return render(request, "economy/landing.html", context)


def _page_url(request, name, number):
    data = request.GET.copy()
    data[name] = number
    return f"{request.path}?{data.urlencode()}"


@require_GET
def economy_records(request):
    data = request.GET.copy()
    if data.get("all_years") != "1" and not data.get("year"):
        data["year"] = timezone.localdate().year
    form = EconomyFilterForm(data)
    context = {
        "title": _("Economy records"),
        "form": form,
        "year": None,
        "all_years": False,
        "show_all": bool(data.get("all")),
    }
    if form.is_valid():
        year = form.cleaned_data["year"]
        filters = {
            name: getattr(form.cleaned_data[name], "pk", form.cleaned_data[name])
            for name in ("crop", "variety", "cycle")
        }
        result = overview(load_economy(), year, through_year=_through_year(year), **filters)
        expenses = sorted(result["expense_rows"], key=queries._expense_order, reverse=True)
        sales = sorted(result["sales"], key=queries._sale_order, reverse=True)
        context.update(result)
        context.update(
            {
                "ready": True,
                "year": year,
                "all_years": year is None,
                "show_varieties": form.cleaned_data["show_varieties"],
                "expense_page": Paginator(expenses, RECORDS_PAGE_SIZE).get_page(
                    request.GET.get("page_expenses")
                ),
                "income_page": Paginator(sales, RECORDS_PAGE_SIZE).get_page(
                    request.GET.get("page_income")
                ),
                **_navigation(request, "economy:records", year),
            }
        )
        context["expense_previous_url"] = (
            _page_url(request, "page_expenses", context["expense_page"].previous_page_number())
            if context["expense_page"].has_previous()
            else None
        )
        context["expense_next_url"] = (
            _page_url(request, "page_expenses", context["expense_page"].next_page_number())
            if context["expense_page"].has_next()
            else None
        )
        context["income_previous_url"] = (
            _page_url(request, "page_income", context["income_page"].previous_page_number())
            if context["income_page"].has_previous()
            else None
        )
        context["income_next_url"] = (
            _page_url(request, "page_income", context["income_page"].next_page_number())
            if context["income_page"].has_next()
            else None
        )
    return render(request, "economy/records.html", context)


@require_GET
def cost_per_plant(request):
    year, all_years = _scope(request)
    context = {
        "title": _("Cost per plant"),
        "year": year,
        "all_years": all_years,
        **cycle_index(load_economy(), year, through_year=_through_year(year)),
        **_navigation(request, "economy:cost-per-plant", year),
    }
    return render(request, "economy/cost_per_plant.html", context)


@require_GET
def cost_per_plant_cycle(request, pk):
    cycle = get_object_or_404(PlantingCycle, pk=pk)
    year, all_years = _scope(request)
    state = load_economy()
    through = _through_year(year)
    result = overview(state, year, through_year=through, cycle=cycle.pk)
    row = next((item for item in result["cycle_rows"] if item["cycle"].pk == cycle.pk), None)
    context = {
        "title": _("Cost per plant"),
        "cycle": cycle,
        "year": year,
        "all_years": all_years,
        "row": row,
        "totals": result["totals"],
        "allocations": cycle_breakdown(state, year, cycle, through_year=through),
        **_navigation(request, "economy:cost-per-plant-cycle", year, [cycle.pk]),
    }
    return render(request, "economy/cost_per_plant_cycle.html", context)


def _edited_allocation(request, expense):
    """The allocation being edited, when the request names one for this expense."""
    value = request.GET.get("allocation") or request.POST.get("allocation")
    try:
        pk = int(value)
    except TypeError, ValueError:
        return None
    return Allocation.objects.filter(pk=pk, expense=expense).first()


@require_GET
def allocation_cycles(request, expense_pk):
    """The year-filtered cycle picker, refreshed when the growing year changes."""
    expense = get_object_or_404(Expense, pk=expense_pk)
    value = request.GET.get("growing_year") or request.GET.get("year")
    try:
        year = forms.IntegerField(min_value=1, max_value=9999).clean(value)
    except ValidationError:
        year = timezone.localdate().year
    record = _edited_allocation(request, expense)
    form = AllocationForm(
        initial={"growing_year": year}, year=year, minimum_year=expense.purchase_year
    )
    return render(
        request,
        "economy/cycle_choices.html",
        {
            "field": form["cycle_ids"],
            "year": year,
            "expense": expense,
            "preview_url": reverse(
                "economy:allocation-preview",
                args=[expense.pk],
                query={"allocation": record.pk} if record else None,
            ),
        },
    )


def _form_available(expense, year, record=None):
    reserved = expense.allocations.exclude(pk=record.pk if record else None)
    if expense.useful_life_years is not None:
        reserved = reserved.filter(growing_year=year)
    return allocation_budget(expense, year) - sum(reserved.values_list("amount_cents", flat=True))


@require_POST
def allocation_preview(request, expense_pk):
    """Server-side split preview; the post-save display stays the fallback."""
    expense = get_object_or_404(Expense, pk=expense_pk)
    editing = _edited_allocation(request, expense)
    form = AllocationPreviewForm(request.POST, minimum_year=expense.purchase_year)
    context = {"expense": expense, "state": "incomplete"}
    if form.is_valid():
        values = form.cleaned_data
        try:
            cents = services.allocation_amount(
                expense,
                values["growing_year"],
                amount=values.get("amount"),
                share=values.get("share"),
                quantity_used=values.get("quantity_used"),
            )
        except ValidationError as exc:
            context.update({"state": "unavailable", "message": exc.messages[0]})
        else:
            record = Allocation(
                expense=expense,
                growing_year=values["growing_year"],
                amount_cents=cents,
                scope=values["scope"],
                method=values["method"],
            )
            plantings = load_plantings()
            selected = [
                planting.cycle_id
                for planting in plantings
                if planting.cycle_id in set(values["cycle_ids"])
                and in_year(planting, values["growing_year"])
            ]
            available = _form_available(expense, values["growing_year"], editing)
            context.update(
                {
                    "state": "ready",
                    "estimate": estimate_allocation(record, plantings, cycle_ids=selected),
                    "amount": money(cents),
                    "available": money(available),
                    "remaining": money(available - cents),
                    "over": cents > available,
                }
            )
    return render(request, "economy/allocation_preview.html", context)


@require_GET
def expense_view(request, pk):
    state = load_economy()
    estimate = next((item for item in state.estimates if item.expense.pk == pk), None)
    if estimate is None:
        raise Http404
    year = _year(request)
    context = expense_detail(estimate, year)
    context["list_url"] = _back_url(request, year, list_only=True)
    context["can_allocate"] = not estimate.expense.useful_life_years or year in estimate.schedule
    return render(request, "economy/expense.html", context)


def _form_context(request, form, kind, title, record=None, expense=None):
    year = _year(request)
    return {
        "form": form,
        "kind": kind,
        "title": title,
        "record": record,
        "expense": expense,
        "year": year,
        "list_url": _back_url(request, year),
        "action": request.path,
    }


def _submit(request, form, context, writer, values):
    if request.method != "POST":
        return render(request, "economy/form.html", context)
    valid = valid_post(request, form)
    if context["record"] is not None and not form.cleaned_data.get("expected_updated_at"):
        form.add_error(None, _("Reload the edit form before saving."))
        valid = False
    status = 422
    if valid:
        arguments = {**form.cleaned_data, **values, "actor": request.user}
        if context["record"] is None:
            arguments.pop("expected_updated_at", None)
        else:
            arguments["pk"] = context["record"].pk
        try:
            result = writer(**arguments)
        except services.EconomyConflict as exc:
            form_error(form, exc)
            status = 409
        except ValidationError as exc:
            form_error(form, exc)
        except OperationalError:
            form.add_error(
                None,
                _(
                    "The save could not be completed. "
                    "Keep this form open and retry the same submission."
                ),
            )
            status = 503
        else:
            messages.success(
                request, _("Record already saved.") if result.replayed else _("Record saved.")
            )
            destination = result.record.get_absolute_url()
            if isinstance(result.record, Expense):
                destination = reverse(
                    "economy:expense-detail",
                    args=[result.record.pk],
                    query={"year": result.record.purchase_year, "next": context["list_url"]},
                )
            if request.headers.get("HX-Request") == "true":
                response = HttpResponse(status=204)
                response["HX-Redirect"] = destination
                response["X-Growbook-Saved-Key"] = str(form.cleaned_data["submission_key"])
                return response
            return redirect(destination)
    if request.headers.get("HX-Request") == "true":
        return render(request, "economy/form_errors.html", {"form": form}, status=status)
    return render(request, "economy/form.html", context, status=status)


@require_http_methods(["GET", "POST"])
def expense_form(request, pk=None):
    record = get_object_or_404(Expense, pk=pk) if pk is not None else None
    initial = (
        {
            field: getattr(record, field)
            for field in (
                "description",
                "purchased_on",
                "total_price",
                "category",
                "purchased_quantity",
                "unit",
                "useful_life_years",
            )
        }
        if record
        else {}
    )
    if record:
        initial["expected_updated_at"] = record.updated_at
    form = ExpenseForm(
        request.POST if request.method == "POST" else None, initial=initial, record=record
    )
    context = _form_context(
        request, form, "expense", _("Edit expense") if record else _("Add expense"), record
    )
    if record:
        context["purchase_year"] = record.purchase_year
    return _submit(
        request, form, context, services.edit_expense if record else services.create_expense, {}
    )


@require_http_methods(["GET", "POST"])
def allocation_form(request, expense_pk, pk=None):
    expense = get_object_or_404(Expense, pk=expense_pk)
    record = get_object_or_404(Allocation, pk=pk, expense=expense) if pk is not None else None
    year = _year(request)
    initial = {"expected_expense_updated_at": expense.updated_at, "growing_year": year}
    if record:
        initial.update(
            {field: getattr(record, field) for field in ("growing_year", "scope", "method")}
        )
        initial.update(
            {
                "expected_updated_at": record.updated_at,
                "cycle_ids": list(record.cycles.values_list("pk", flat=True)),
            }
        )
        budget = allocation_budget(expense, record.growing_year)
        if budget:
            share = (Decimal(record.amount_cents) * 100 / Decimal(budget)).quantize(
                Decimal("0.001")
            )
            initial["share"] = min(max(share, Decimal("0")), Decimal("100"))
    else:
        initial["share"] = Decimal("100")
    if request.GET.get("cycle"):
        initial["cycle_ids"] = [request.GET["cycle"]]
    form = AllocationForm(
        request.POST if request.method == "POST" else None,
        initial=initial,
        minimum_year=expense.purchase_year,
    )
    query = {"allocation": record.pk} if record else None
    form.fields["growing_year"].widget.attrs.update(
        {
            "hx-get": reverse("economy:allocation-cycles", args=[expense.pk], query=query),
            "hx-trigger": "change",
            "hx-target": "#id_cycle_ids_group",
            "hx-swap": "outerHTML",
            "hx-include": "this",
        }
    )
    context = _form_context(
        request,
        form,
        "allocation",
        _("Edit allocation") if record else _("Allocate expense"),
        record,
        expense,
    )
    percent = form["share"].value()
    try:
        percent = Decimal(str(percent)) if percent not in (None, "") else Decimal("100")
    except InvalidOperation:
        percent = Decimal("100")
    context.update(
        {
            "purchase_year": expense.purchase_year,
            "preview_url": reverse("economy:allocation-preview", args=[expense.pk], query=query),
        }
    )
    if expense.purchased_quantity is not None:
        context["share_quantity"] = (expense.purchased_quantity * percent / 100).quantize(
            Decimal("0.001")
        )
    context["list_url"] = (
        _back_url(request, year)
        if request.GET.get("next") or request.POST.get("next")
        else reverse("economy:expense-detail", args=[expense.pk], query={"year": year})
    )
    return _submit(
        request,
        form,
        context,
        services.edit_allocation if record else services.create_allocation,
        {"expense": expense.pk},
    )


@require_http_methods(["GET", "POST"])
def sale_form(request, pk=None):
    record = get_object_or_404(Sale, pk=pk) if pk is not None else None
    initial = (
        {
            field: getattr(record, field)
            for field in ("description", "sold_on", "quantity", "unit_price")
        }
        if record
        else {}
    )
    if record:
        initial.update(
            {
                "cycle": record.cycle_id,
                "variety": record.variety_id,
                "expected_updated_at": record.updated_at,
            }
        )
        if record.unit_price is None:
            initial["total_price"] = record.total_price
    elif request.GET.get("cycle"):
        initial["cycle"] = request.GET["cycle"]
    elif request.GET.get("variety"):
        initial["variety"] = request.GET["variety"]
    form = SaleForm(
        request.POST if request.method == "POST" else None, initial=initial, record=record
    )
    context = _form_context(
        request, form, "sale", _("Edit plant sale") if record else _("Add plant sale"), record
    )
    context["cycle_sources"] = form.sources
    return _submit(
        request, form, context, services.edit_sale if record else services.create_sale, {}
    )


@require_http_methods(["GET", "POST"])
def delete_record(request, kind, pk):
    model, writer = {
        "expense": (Expense, services.delete_expense),
        "allocation": (Allocation, services.delete_allocation),
        "sale": (Sale, services.delete_sale),
    }[kind]
    record = get_object_or_404(model, pk=pk)
    year = _year(request)
    initial = {"expected_updated_at": record.updated_at}
    if kind == "allocation":
        initial["expected_expense_updated_at"] = record.expense.updated_at
    form = (AllocationDeleteForm if kind == "allocation" else DeleteForm)(
        request.POST if request.method == "POST" else None, initial=initial
    )
    destination = (
        reverse("economy:expense-detail", args=[record.expense_id], query={"year": year})
        if kind == "allocation"
        else _back_url(request, year, list_only=True)
    )
    context = {"kind": kind, "record": record, "form": form, "list_url": destination, "year": year}
    if request.method == "POST":
        status = 422
        if valid_post(request, form):
            values = {key: value for key, value in form.cleaned_data.items() if key != "confirm"}
            try:
                writer(actor=request.user, pk=pk, **values)
            except services.EconomyConflict as exc:
                form_error(form, exc)
                status = 409
            except ValidationError as exc:
                form_error(form, exc)
            except OperationalError:
                form.add_error(
                    None, _("Deletion could not be confirmed. Reload Economy before retrying.")
                )
                status = 503
            else:
                messages.success(request, _("Record deleted."))
                return redirect(destination)
        return render(request, "economy/delete.html", context, status=status)
    return render(request, "economy/delete.html", context)
