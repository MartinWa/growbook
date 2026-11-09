"""Planting overviews, scoped histories, details and maintenance forms."""

import logging
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import OperationalError
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_http_methods

from core.forms import DeleteForm, form_error, valid_post
from core.media import MediaBusy
from diary.queries import observation_count, observations_for

from ..forms import PlantingFilterForm, PlantingForm, clean_record_id
from ..models import Crop, GrowingArea, Planting
from ..queries import area_paths, planting_history, planting_tree
from ..services import RegisterConflict
from ..services import plantings as services
from ._shared import REGISTERS

logger = logging.getLogger(__name__)


@require_GET
def planting_list(request, history=False, kind=None, pk=None):
    """The year overview and scoped histories share one explicit, deduplicated query."""
    scope = get_object_or_404(REGISTERS[kind][0], pk=pk) if kind else None
    data = request.GET.copy()
    if not history and not data.get("year"):
        data["year"] = str(timezone.localdate().year)
    locked = set()
    if scope:
        data[kind] = str(scope.pk)
        locked.add(kind)
        if kind == "variety":
            data["crop"] = str(scope.crop_id)
            locked.add("crop")
    form = PlantingFilterForm(data)
    records, year = [], None
    tree = {"events": [], "unassigned": []}
    valid = form.is_valid()
    if valid:
        filters = form.cleaned_data
        year = filters["year"]
        tree = planting_tree(**filters)
        records = tree["records"]
    active_filters = []
    for name, field in form.fields.items():
        value = form.cleaned_data.get(name, data.get(name))
        if not value:
            continue
        display = field.label_from_instance(value) if hasattr(value, "pk") else value
        if name in ("unassigned", "unknown_variety"):
            display = _("Yes")
        remaining = data.copy()
        remaining.pop(name, None)
        # The overview defaults to the current year; its year stays visible while
        # All years provides the explicit route to an unbounded history.
        removable = name not in locked and (name != "year" or history)
        active_filters.append(
            {
                "label": field.label,
                "value": display,
                "url": request.path + ("?" + remaining.urlencode() if remaining else "")
                if removable
                else "",
            }
        )
    previous_url = next_url = None
    if year is not None:
        if year > 1:
            previous = data.copy()
            previous["year"] = str(year - 1)
            previous_url = request.path + "?" + previous.urlencode()
        if year < 9999:
            following = data.copy()
            following["year"] = str(year + 1)
            next_url = request.path + "?" + following.urlencode()
    create = {}
    if year:
        create["year"] = year
    for field in ("crop", "variety", "area"):
        if valid and form.cleaned_data[field] is not None:
            create[field] = form.cleaned_data[field].pk
    create_url = reverse("garden:planting-create") + ("?" + urlencode(create) if create else "")
    empty = {}
    if not records:
        empty = {
            "empty_heading": _("No plantings yet"),
            "empty_description": _(
                "Start a planting with a crop and growing year; add details later."
            ),
            "empty_action_url": create_url,
            "empty_action_label": _("New planting"),
        }
        scope_filters = {kind: scope} if scope else {}
        history_path = request.path if history else reverse("garden:planting-history")
        if not valid:
            empty.update(
                empty_heading=_("Check the filters"),
                empty_description=_("Choose a valid value, or clear the filters and try again."),
                empty_action_url=history_path,
                empty_action_label=_("Clear filters"),
                empty_filter_link=True,
            )
        elif planting_history(**scope_filters).exists():
            adjustable_filters = any(
                filters[field]
                for field in ("crop", "variety", "area", "unassigned", "unknown_variety")
                if field not in locked
            )
            empty.update(
                empty_heading=_("No plantings match these filters.")
                if adjustable_filters
                else _("No plantings in this growing year"),
                empty_description=_("Clear the filters to see the planting history.")
                if adjustable_filters
                else _("See all years to find earlier plans and ongoing plantings."),
                empty_action_url=history_path,
                empty_action_label=_("Clear filters") if adjustable_filters else _("All years"),
                empty_filter_link=True,
            )
        elif not Crop.objects.filter(is_archived=False).exists():
            empty.update(
                empty_description=_("Add a crop first, then plan where and when to grow it."),
                empty_action_url=reverse("garden:crop-create"),
                empty_action_label=_("New crop"),
            )
    all_years_data = data.copy()
    all_years_data.pop("year", None)
    overview_data = data.copy()
    overview_data["year"] = str(year or timezone.localdate().year)
    observations = []
    observation_total = 0
    observations_url = ""
    if scope:
        observations = observations_for(kind, scope)
        observation_total = observation_count(kind, scope)
        observations_url = reverse("diary:list") + "?" + urlencode({kind: scope.pk})
    partial = (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-Target") == "planting-results"
    )
    return render(
        request,
        "garden/planting_results.html" if partial else "garden/planting_list.html",
        {
            "heading": _("Planting history") if history else _("Plantings"),
            "form": form,
            "records": records,
            "area_events": tree["events"],
            "unassigned_records": tree["unassigned"],
            "year": year,
            "scope": scope,
            "kind": kind or "planting",
            "locked_filters": locked,
            "active_filters": active_filters,
            "history": history,
            "observations": observations,
            "observation_count": observation_total,
            "observations_url": observations_url,
            "create_url": create_url,
            "previous_url": previous_url,
            "next_url": next_url,
            "overview_url": reverse("garden:planting-list") + "?" + overview_data.urlencode(),
            "all_years_url": reverse("garden:planting-history")
            + ("?" + all_years_data.urlencode() if all_years_data else ""),
            **empty,
        },
        status=200 if valid else 422,
    )


def _planting_context(pk):
    record = get_object_or_404(
        Planting.objects.select_related("crop", "variety", "area").prefetch_related("photos"), pk=pk
    )
    paths = area_paths(list(GrowingArea.objects.all()))
    record.area_path = paths.get(record.area_id, "")
    record.display_label = record.label or _("Batch %(number)s") % {"number": record.pk}
    events = record.events.annotate(event_date=Coalesce("actual_date", "planned_date")).order_by(
        "event_date", "id"
    )
    return {
        "kind": "planting",
        "record": record,
        "events": events,
        "observations": observations_for("planting", record),
        "observation_count": observation_count("planting", record),
        "observations_url": reverse("diary:list") + "?" + urlencode({"planting": record.pk}),
    }


@require_GET
def planting_detail(request, pk):
    context = _planting_context(pk)
    context["inline"] = request.headers.get("HX-Request") == "true"
    try:
        year = int(request.GET.get("year", ""))
    except ValueError:
        year = None
    context["year"] = year if year is not None and 1 <= year <= 9999 else None
    template = (
        "garden/planting_details.html" if context["inline"] else "garden/planting_detail.html"
    )
    return render(request, template, context)


@require_http_methods(["GET", "POST"])
def planting_form(request, pk=None):
    record = get_object_or_404(Planting, pk=pk) if pk is not None else None
    initial = {}
    if record:
        initial = {
            field: getattr(record, field)
            for field in PlantingForm.base_fields
            if field in {f.name for f in Planting._meta.fields}
        }
        initial["expected_updated_at"] = record.updated_at
    else:
        initial = {field: request.GET.get(field) for field in ("crop", "variety", "area")}
        try:
            year = int(request.GET.get("year", ""))
        except ValueError:
            year = timezone.localdate().year
        initial["start_year"] = year if 1 <= year <= 9999 else timezone.localdate().year
    form = PlantingForm(
        request.POST if request.method == "POST" else None,
        request.FILES if request.method == "POST" else None,
        record=record,
        initial=initial,
    )
    form.fields["crop"].widget.attrs.update(
        {
            "hx-get": reverse("garden:planting-varieties"),
            "hx-trigger": "change",
            "hx-target": "#id_variety",
            "hx-swap": "outerHTML",
            "hx-include": "#id_variety, #planting-context" if record else "#id_variety",
            "hx-sync": "this:replace",
        }
    )
    context = {
        "kind": "planting",
        "form": form,
        "record": record,
        "title": _("Edit planting") if record else _("New planting"),
        "has_photos": True,
        "action": request.path,
        "list_url": reverse("garden:planting-list"),
    }
    if request.method == "POST":
        if request.POST.get("choose_crop") == "1":
            context["failed"] = bool(request.FILES) or bool(
                getattr(request, "photo_upload_error", None)
            )
            return render(request, "garden/form.html", context)
        valid = valid_post(request, form, photo_fields={"photos"})
        if record is not None and not form.cleaned_data.get("expected_updated_at"):
            form.add_error(None, _("Reload the edit form before saving."))
            valid = False
        status = 422
        if valid:
            values = {**form.cleaned_data, "actor": request.user}
            values["uploads"] = values.pop("photos")
            if pk is None:
                values.pop("expected_updated_at")
            else:
                values["pk"] = pk
            writer = services.edit_planting if pk is not None else services.create_planting
            try:
                result = writer(**values)
            except RegisterConflict as exc:
                form_error(form, exc)
                status = 409
            except ValidationError as exc:
                form_error(form, exc)
            except MediaBusy, OperationalError, OSError:
                logger.exception("Planting save failed; keep the original form/key for retries.")
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
        context["failed"] = True
        return render(request, "garden/form.html", context, status=status)
    return render(request, "garden/form.html", context)


@require_GET
def planting_varieties(request):
    try:
        crop_id = clean_record_id(request.GET.get("crop"))
    except ValidationError:
        crop_id = None
    record = None
    if request.GET.get("planting"):
        try:
            planting_id = clean_record_id(request.GET["planting"], required=True)
        except ValidationError:
            return HttpResponse(status=400)
        record = get_object_or_404(Planting, pk=planting_id)
    form = PlantingForm(record=record, initial={"crop": crop_id})
    try:
        current_id = clean_record_id(request.GET.get("variety"))
    except ValidationError:
        current_id = None
    if form.fields["variety"].queryset.filter(pk=current_id).exists():
        form.initial["variety"] = current_id
    return render(request, "garden/variety_options.html", {"field": form["variety"]})


@require_http_methods(["GET", "POST"])
def planting_delete(request, pk):
    context = _planting_context(pk)
    record = context["record"]
    form = DeleteForm(
        request.POST if request.method == "POST" else None,
        initial={"expected_updated_at": record.updated_at},
    )
    context["form"] = form
    if request.method == "POST":
        if valid_post(request, form):
            try:
                services.delete_planting(
                    actor=request.user,
                    pk=pk,
                    expected_updated_at=form.cleaned_data["expected_updated_at"],
                )
            except ValidationError as exc:
                form_error(form, exc)
            except MediaBusy, OperationalError, OSError:
                form.add_error(
                    None, _("Deletion could not be confirmed. Reload the register before retrying.")
                )
                return render(request, "garden/planting_delete.html", context, status=503)
            else:
                messages.success(request, _("Record deleted."))
                return redirect("garden:planting-list")
        return render(request, "garden/planting_delete.html", context, status=422)
    return render(request, "garden/planting_delete.html", context)
