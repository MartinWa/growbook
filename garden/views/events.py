"""Planting-event create, edit and deletion forms."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import OperationalError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from core.forms import DeleteForm, form_error, valid_post

from ..forms import PlantingEventForm
from ..models import Planting, PlantingEvent
from ..services import RegisterConflict
from ..services import events as services


@require_http_methods(["GET", "POST"])
def planting_event_form(request, planting_pk, pk=None):
    planting = get_object_or_404(Planting.objects.select_related("crop"), pk=planting_pk)
    record = get_object_or_404(PlantingEvent, pk=pk, planting=planting) if pk is not None else None
    initial = (
        {field: getattr(record, field) for field in ("kind", "planned_date", "actual_date", "note")}
        if record
        else {}
    )
    if record:
        initial["expected_updated_at"] = record.updated_at
    form = PlantingEventForm(request.POST if request.method == "POST" else None, initial=initial)
    context = {
        "kind": "event",
        "form": form,
        "record": record,
        "planting": planting,
        "title": _("Edit planting event") if record else _("New planting event"),
        "has_photos": False,
        "action": request.path,
        "list_url": planting.get_absolute_url(),
    }
    if request.method == "POST":
        valid = valid_post(request, form)
        if record is not None and not form.cleaned_data.get("expected_updated_at"):
            form.add_error(None, _("Reload the edit form before saving."))
            valid = False
        status = 422
        if valid:
            values = {**form.cleaned_data, "actor": request.user, "planting": planting.pk}
            if pk is None:
                values.pop("expected_updated_at")
            else:
                values["pk"] = pk
            writer = (
                services.edit_planting_event if pk is not None else services.create_planting_event
            )
            try:
                result = writer(**values)
            except RegisterConflict as exc:
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
                if request.headers.get("HX-Request") == "true":
                    response = HttpResponse(status=204)
                    response["HX-Redirect"] = result.record.get_absolute_url()
                    return response
                return redirect(result.record)
        if request.headers.get("HX-Request") == "true":
            return render(request, "garden/form_errors.html", {"form": form}, status=status)
        return render(request, "garden/form.html", context, status=status)
    return render(request, "garden/form.html", context)


@require_http_methods(["GET", "POST"])
def planting_event_delete(request, planting_pk, pk):
    event = get_object_or_404(
        PlantingEvent.objects.select_related("planting__crop"), pk=pk, planting_id=planting_pk
    )
    form = DeleteForm(
        request.POST if request.method == "POST" else None,
        initial={"expected_updated_at": event.updated_at},
    )
    context = {"event": event, "form": form}
    if request.method == "POST":
        if valid_post(request, form):
            try:
                services.delete_planting_event(
                    actor=request.user,
                    pk=pk,
                    expected_updated_at=form.cleaned_data["expected_updated_at"],
                )
            except ValidationError as exc:
                form_error(form, exc)
            except OperationalError:
                form.add_error(
                    None, _("Deletion could not be confirmed. Reload the register before retrying.")
                )
                return render(request, "garden/event_delete.html", context, status=503)
            else:
                messages.success(request, _("Record deleted."))
                return redirect(event.planting)
        return render(request, "garden/event_delete.html", context, status=422)
    return render(request, "garden/event_delete.html", context)
