from urllib.parse import urlsplit

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import OperationalError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_http_methods

from core.forms import DeleteForm, form_error, valid_post

from . import services
from .calculations import load_economy
from .forms import AllocationDeleteForm, AllocationForm, EconomyFilterForm, ExpenseForm, SaleForm
from .models import Allocation, Expense, Sale
from .queries import expense_detail, overview


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
            if (
                match.view_name == "economy:list"
                or not list_only
                and match.view_name == "economy:expense-detail"
            ):
                return value
    return reverse("economy:list", query={"year": year})


@require_GET
def economy_list(request):
    data = request.GET.copy()
    if not data.get("year"):
        data["year"] = timezone.localdate().year
    form = EconomyFilterForm(data)
    context = {"form": form, "title": _("Economy"), "year": None}
    if form.is_valid():
        year = form.cleaned_data["year"]
        filters = {
            name: getattr(form.cleaned_data[name], "pk", form.cleaned_data[name])
            for name in ("crop", "variety", "cycle")
        }
        context.update(overview(load_economy(), year, **filters))
        previous, following = data.copy(), data.copy()
        previous["year"], following["year"] = year - 1, year + 1
        context.update(
            {
                "year": year,
                "show_varieties": form.cleaned_data["show_varieties"],
                "previous_url": reverse("economy:list", query=previous) if year > 1 else None,
                "next_url": reverse("economy:list", query=following) if year < 9999 else None,
            }
        )
    return render(request, "economy/list.html", context)


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
        initial.update(
            {
                "crop": record.crop_id,
                "variety": record.variety_id,
                "expected_updated_at": record.updated_at,
            }
        )
    else:
        for field in ("crop", "variety"):
            if request.GET.get(field):
                initial[field] = request.GET[field]
    form = ExpenseForm(
        request.POST if request.method == "POST" else None, initial=initial, record=record
    )
    context = _form_context(
        request, form, "expense", _("Edit expense") if record else _("New expense"), record
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
            {
                field: getattr(record, field)
                for field in ("growing_year", "scope", "method", "amount")
            }
        )
        initial.update(
            {
                "expected_updated_at": record.updated_at,
                "cycle_ids": list(record.cycles.values_list("pk", flat=True)),
            }
        )
    elif request.GET.get("cycle"):
        initial["cycle_ids"] = [request.GET["cycle"]]
    form = AllocationForm(request.POST if request.method == "POST" else None, initial=initial)
    context = _form_context(
        request,
        form,
        "allocation",
        _("Edit allocation") if record else _("Allocate expense"),
        record,
        expense,
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
        request, form, "sale", _("Edit plant sale") if record else _("Record plant sale"), record
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
