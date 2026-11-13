"""Category maintenance and shared planting-cycle details and lifecycle forms."""

import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import OperationalError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_http_methods

from core.forms import DeleteForm, form_error, valid_post

from ..forms import CycleEditForm, CycleTransitionForm, PlantingCategoryForm
from ..models import PlantingCategory, PlantingCycle
from ..queries import cycle_years, planting_rows
from ..services import RegisterConflict
from ..services import cycles as services
from ._shared import return_url

logger = logging.getLogger(__name__)


def cycle_context(pk):
    cycle = get_object_or_404(PlantingCycle.objects.prefetch_related("categories"), pk=pk)
    members = planting_rows(
        cycle.members.select_related("crop", "variety", "area").prefetch_related("photos")
    )
    return {
        "cycle": cycle,
        "members": members,
        "years": cycle_years(members),
        "heading": min(members, key=lambda member: member.pk).display_label
        if members
        else _("Planting cycle"),
    }


@require_GET
def cycle_detail(request, pk):
    context = cycle_context(pk)
    if context["members"]:
        try:
            selected = int(request.GET.get("member", context["members"][0].pk))
        except (ValueError, TypeError) as exc:
            raise Http404 from exc
        if not any(member.pk == selected for member in context["members"]):
            raise Http404
        from .plantings import _planting_context

        context.update(_planting_context(selected))
    context["destination"] = return_url(request)
    context["inline"] = request.headers.get("HX-Request") == "true"
    try:
        year = int(request.GET.get("year", ""))
    except ValueError:
        year = None
    context["year"] = year if year is not None and 1 <= year <= 9999 else None
    return render(
        request,
        "garden/cycle_details.html" if context["inline"] else "garden/cycle_detail.html",
        context,
    )


def _saved(request, result, destination):
    messages.success(
        request,
        format_html(
            '{} <a href="{}">{}</a>',
            _("Record already saved.") if result.replayed else _("Record saved."),
            result.record.get_absolute_url(),
            _("Open record"),
        ),
    )
    destination = destination or result.record.get_absolute_url()
    if request.headers.get("HX-Request") == "true":
        response = HttpResponse(status=204)
        response["HX-Redirect"] = destination
        return response
    return redirect(destination)


@require_http_methods(["GET", "POST"])
def cycle_form(request, pk, action=None):
    context = cycle_context(pk)
    cycle = context["cycle"]
    destination = return_url(request)
    initial = {"expected_updated_at": cycle.updated_at}
    if action:
        form = CycleTransitionForm(
            request.POST if request.method == "POST" else None,
            initial=initial,
            cycle=cycle if action == "complete" else None,
        )
        form.fields["date"].label = (
            _("Actual start date") if action == "start" else _("Completion date")
        )
        title = _("Start cycle") if action == "start" else _("Complete cycle")
    else:
        initial.update(
            status=cycle.status,
            started_on=cycle.started_on,
            completed_on=cycle.completed_on,
            category_ids=list(cycle.categories.values_list("pk", flat=True)),
        )
        form = CycleEditForm(
            request.POST if request.method == "POST" else None, cycle=cycle, initial=initial
        )
        title = _("Edit cycle")
    context.update(
        kind="cycle-transition" if action else "cycle",
        form=form,
        record=cycle,
        title=title,
        action=request.path,
        return_url=destination,
        cancel_url=destination or cycle.get_absolute_url(),
        list_url=reverse("garden:planting-list"),
    )
    if request.method == "POST":
        status = 422
        if valid_post(request, form):
            evaluation_values = form.evaluation_values()
            values = {**form.cleaned_data, **evaluation_values, "actor": request.user, "pk": pk}
            try:
                result = (
                    services.transition_cycle(**values, action=action)
                    if action
                    else services.edit_cycle(**values)
                )
            except RegisterConflict as exc:
                form_error(form, exc)
                status = 409
            except ValidationError as exc:
                form_error(form, exc)
            except OperationalError:
                logger.exception("Cycle save failed; preserve the original form and save key.")
                form.add_error(
                    None,
                    _(
                        "The save could not be completed. "
                        "Keep this form open and retry the same submission."
                    ),
                )
                status = 503
            else:
                return _saved(request, result, destination)
        if request.headers.get("HX-Request") == "true":
            return render(request, "garden/form_errors.html", {"form": form}, status=status)
        return render(request, "garden/form.html", context, status=status)
    return render(request, "garden/form.html", context)


@require_http_methods(["GET", "POST"])
def category_form(request, pk=None):
    record = get_object_or_404(PlantingCategory, pk=pk) if pk else None
    destination = return_url(request)
    initial = {"name": record.name, "expected_updated_at": record.updated_at} if record else {}
    form = PlantingCategoryForm(request.POST if request.method == "POST" else None, initial=initial)
    form.fields["expected_updated_at"].required = record is not None
    context = {
        "kind": "planting-category",
        "form": form,
        "record": record,
        "title": _("Edit category") if record else _("New category"),
        "action": request.path,
        "return_url": destination,
        "cancel_url": destination or reverse("garden:planting-list"),
        "list_url": reverse("garden:planting-list"),
    }
    if request.method == "POST":
        status = 422
        if valid_post(request, form):
            values = {**form.cleaned_data, "actor": request.user}
            if record is not None:
                values["pk"] = pk
            else:
                values.pop("expected_updated_at")
            writer = (
                services.edit_planting_category if record else services.create_planting_category
            )
            try:
                result = writer(**values)
            except RegisterConflict as exc:
                form_error(form, exc)
                status = 409
            except ValidationError as exc:
                form_error(form, exc)
            except OperationalError:
                logger.exception("Category save failed; preserve the original form and save key.")
                form.add_error(
                    None,
                    _(
                        "The save could not be completed. "
                        "Keep this form open and retry the same submission."
                    ),
                )
                status = 503
            else:
                return _saved(request, result, destination)
        if request.headers.get("HX-Request") == "true":
            return render(request, "garden/form_errors.html", {"form": form}, status=status)
        return render(request, "garden/form.html", context, status=status)
    return render(request, "garden/form.html", context)


@require_http_methods(["GET", "POST"])
def category_delete(request, pk):
    record = get_object_or_404(PlantingCategory, pk=pk)
    destination = return_url(request) or reverse("garden:planting-list")
    form = DeleteForm(
        request.POST if request.method == "POST" else None,
        initial={"expected_updated_at": record.updated_at},
    )
    context = {"record": record, "form": form, "destination": destination}
    if request.method == "POST":
        status = 422
        if valid_post(request, form):
            try:
                services.delete_planting_category(
                    actor=request.user,
                    pk=pk,
                    expected_updated_at=form.cleaned_data["expected_updated_at"],
                )
            except RegisterConflict as exc:
                form_error(form, exc)
                status = 409
            except ValidationError as exc:
                form_error(form, exc)
            except OperationalError:
                form.add_error(
                    None, _("Deletion could not be confirmed. Reload the register before retrying.")
                )
                status = 503
            else:
                messages.success(request, _("Record deleted."))
                return redirect(destination)
        return render(request, "garden/planting_category_delete.html", context, status=status)
    return render(request, "garden/planting_category_delete.html", context)
