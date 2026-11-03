import logging
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError
from django.db.models import Count
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.forms import DeleteForm
from core.media import MediaBusy
from core.models import Photo
from diary.queries import observation_count, observations_for

from . import services
from .forms import (
    ArchiveForm,
    AreaForm,
    CropForm,
    PlantingEventForm,
    PlantingFilterForm,
    PlantingForm,
    RegisterPhotoForm,
    VarietyForm,
    clean_record_id,
)
from .models import Crop, GrowingArea, Planting, PlantingEvent, Variety
from .queries import area_paths, planting_history, planting_rows

logger = logging.getLogger(__name__)

# Routing metadata only. Models/forms/domain writers remain explicit rather than a register engine.
REGISTERS = {
    "crop": (Crop, CropForm, _("Crops"), _("New crop"), _("Edit crop")),
    "variety": (Variety, VarietyForm, _("Varieties"), _("New variety"), _("Edit variety")),
    "area": (
        GrowingArea,
        AreaForm,
        _("Growing areas"),
        _("New growing area"),
        _("Edit growing area"),
    ),
}


def _context(kind):
    return {
        "kind": kind,
        "heading": REGISTERS[kind][2],
        "create_label": REGISTERS[kind][3],
        "list_url": reverse(f"garden:{kind}-list"),
        "create_url": reverse(f"garden:{kind}-create"),
    }


@require_GET
def garden_home(request):
    return render(
        request,
        "garden/home.html",
        {
            "crop_count": Crop.objects.filter(is_archived=False).count(),
            "variety_count": Variety.objects.filter(is_archived=False).count(),
            "area_count": GrowingArea.objects.filter(is_archived=False).count(),
        },
    )


@require_GET
def register_list(request, kind):
    model = REGISTERS[kind][0]
    context = _context(kind)
    records = model.objects.all()
    if request.GET.get("archived") != "1":
        records = records.filter(is_archived=False)
    if kind == "crop":
        records = records.annotate(variety_count=Count("varieties"))
    elif kind == "variety":
        records = records.select_related("crop")
    else:
        records = records.select_related("parent")
    records = list(records)
    if kind == "area":
        paths = area_paths(list(GrowingArea.objects.all()))
        for record in records:
            record.parent_path = paths.get(record.parent_id, "")
    context.update(records=records, show_archived=request.GET.get("archived") == "1")
    if not records:
        context.update(
            empty_heading={
                "crop": _("No crops yet"),
                "variety": _("No varieties yet"),
                "area": _("No growing areas yet"),
            }[kind],
            empty_description={
                "crop": _("Add a crop to organize varieties and start planning plantings."),
                "variety": _("Add a variety to a crop when you want to record more detail."),
                "area": _("Add a growing area to record where you grow and link observations."),
            }[kind],
            empty_action_url=context["create_url"],
            empty_action_label=context["create_label"],
        )
        if not context["show_archived"] and model.objects.filter(is_archived=True).exists():
            context.update(
                empty_heading=_("No active records"),
                empty_description=_("Your archived records are available in the archive view."),
                empty_action_url=context["list_url"] + "?archived=1",
                empty_action_label=_("Show archived records"),
            )
        elif kind == "variety" and not Crop.objects.filter(is_archived=False).exists():
            context.update(
                empty_description=_("Add a crop first, then record its varieties."),
                empty_action_url=reverse("garden:crop-create"),
                empty_action_label=_("New crop"),
            )
    return render(request, "garden/list.html", context)


def _detail_context(kind, pk):
    model = REGISTERS[kind][0]
    queryset = model.objects.all()
    if kind == "crop":
        queryset = queryset.prefetch_related("varieties")
    elif kind == "variety":
        queryset = queryset.select_related("crop").prefetch_related("photos")
    else:
        queryset = queryset.select_related("parent").prefetch_related("photos", "children")
    record = get_object_or_404(queryset, pk=pk)
    context = _context(kind)
    context.update(
        record=record,
        edit_url=reverse(f"garden:{kind}-edit", args=[pk]),
        archive_url=reverse(f"garden:{kind}-archive", args=[pk]),
        delete_url=reverse(f"garden:{kind}-delete", args=[pk]),
        history_url=reverse(f"garden:{kind}-history", args=[pk]),
        observations=observations_for(kind, record),
        observation_count=observation_count(kind, record),
        observations_url=reverse("diary:list") + "?" + urlencode({kind: record.pk}),
    )
    return context


@require_GET
def register_detail(request, kind, pk):
    context = _detail_context(kind, pk)
    template = (
        "garden/details.html"
        if request.headers.get("HX-Request") == "true"
        else "garden/detail.html"
    )
    return render(request, template, context)


def _form_error(form, error):
    if hasattr(error, "error_dict"):
        for field, errors in error.error_dict.items():
            form.add_error(field if field in form.fields else None, errors)
    else:
        form.add_error(None, error)


def _valid_post(request, form, photo_fields=()):
    valid = form.is_valid()
    if getattr(request, "photo_upload_error", None):
        form.add_error(None, request.photo_upload_error)
        valid = False
    if set(request.FILES) - set(photo_fields):
        form.add_error(None, _("An unexpected photo field was submitted. Nothing was saved."))
        valid = False
    return valid


def _save(request, kind, pk, data):
    common = {"actor": request.user, "submission_key": data["submission_key"], "name": data["name"]}
    if pk is not None:
        common.update(pk=pk, expected_updated_at=data["expected_updated_at"])
    if kind == "crop":
        writer = services.edit_crop if pk is not None else services.create_crop
        return writer(**common, notes=data["notes"])
    if kind == "variety":
        writer = services.edit_variety if pk is not None else services.create_variety
        return writer(
            **common,
            crop=data["crop"],
            category=data["category"],
            description=data["description"],
            source=data["source"],
            experiences=data["experiences"],
            uploads=data["photos"],
        )
    writer = services.edit_area if pk is not None else services.create_area
    return writer(
        **common,
        parent=data["parent"],
        area_type=data["area_type"],
        length_cm=data["length_cm"],
        width_cm=data["width_cm"],
        description=data["description"],
        notes=data["notes"],
        uploads=data["photos"],
    )


@require_http_methods(["GET", "POST"])
def register_form(request, kind, pk=None):
    model, form_class, heading, create_label, edit_label = REGISTERS[kind]
    record = get_object_or_404(model, pk=pk) if pk is not None else None
    initial = {}
    if record is not None:
        model_fields = {field.name for field in model._meta.fields}
        initial = {
            field: getattr(record, field)
            for field in form_class.base_fields
            if field in model_fields
        }
        initial["expected_updated_at"] = record.updated_at
    if kind == "variety" and record is None:
        initial["crop"] = request.GET.get("crop")
    kwargs = {"record": record} if kind != "crop" else {}
    form = form_class(
        request.POST if request.method == "POST" else None,
        request.FILES if request.method == "POST" else None,
        initial=initial,
        **kwargs,
    )
    context = _context(kind)
    context.update(
        form=form,
        record=record,
        title=edit_label if record else create_label,
        has_photos=kind != "crop",
        action=request.path,
    )
    if request.method == "POST":
        valid = _valid_post(request, form, photo_fields={"photos"} if kind != "crop" else ())
        if record is not None and not form.cleaned_data.get("expected_updated_at"):
            form.add_error(None, _("Reload the edit form before saving."))
            valid = False
        status = 422
        if valid:
            try:
                result = _save(request, kind, pk, form.cleaned_data)
            except services.RegisterConflict as exc:
                _form_error(form, exc)
                status = 409
            except ValidationError as exc:
                _form_error(form, exc)
            except MediaBusy, OperationalError, OSError:
                logger.exception("Register save failed; keep the original form/key for retries.")
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


@require_POST
def register_archive(request, kind, pk):
    get_object_or_404(REGISTERS[kind][0], pk=pk)
    form = ArchiveForm(request.POST)
    if not _valid_post(request, form):
        return render(request, "garden/action_error.html", {"form": form}, status=422)
    writer = {
        "crop": services.archive_crop,
        "variety": services.archive_variety,
        "area": services.archive_area,
    }[kind]
    try:
        record = writer(actor=request.user, pk=pk, archived=form.cleaned_data["archived"])
    except ValidationError as exc:
        _form_error(form, exc)
        return render(request, "garden/action_error.html", {"form": form}, status=409)
    except MediaBusy, OperationalError, OSError:
        form.add_error(
            None, _("The action could not be confirmed. Reload the register before retrying.")
        )
        return render(request, "garden/action_error.html", {"form": form}, status=503)
    messages.success(
        request,
        _("Record archived.") if record.is_archived else _("Record restored to active selections."),
    )
    return redirect(record)


@require_http_methods(["GET", "POST"])
def register_delete(request, kind, pk):
    context = _detail_context(kind, pk)
    record = context["record"]
    form = DeleteForm(
        request.POST if request.method == "POST" else None,
        initial={"expected_updated_at": record.updated_at},
    )
    context["form"] = form
    if request.method == "POST":
        if _valid_post(request, form):
            writer = {
                "crop": services.delete_crop,
                "variety": services.delete_variety,
                "area": services.delete_area,
            }[kind]
            try:
                writer(
                    actor=request.user,
                    pk=pk,
                    expected_updated_at=form.cleaned_data["expected_updated_at"],
                )
            except ValidationError as exc:
                _form_error(form, exc)
            except MediaBusy, OperationalError, OSError:
                form.add_error(
                    None, _("Deletion could not be confirmed. Reload the register before retrying.")
                )
                return render(request, "garden/delete.html", context, status=503)
            else:
                messages.success(request, _("Record deleted."))
                return redirect(context["list_url"])
        return render(request, "garden/delete.html", context, status=422)
    return render(request, "garden/delete.html", context)


def _register_photo(pk):
    photo = get_object_or_404(
        Photo.objects.select_related("area", "variety", "planting__crop"), pk=pk
    )
    if photo.entry_id is not None:
        raise PermissionDenied
    return photo


@require_http_methods(["GET", "POST"])
def photo_edit(request, pk):
    photo = _register_photo(pk)
    form = RegisterPhotoForm(
        request.POST if request.method == "POST" else None,
        initial={
            "caption": photo.caption,
            "position": photo.position,
            "expected_updated_at": photo.owner.updated_at,
        },
    )
    if request.method == "POST":
        if _valid_post(request, form):
            try:
                services.edit_register_photo(actor=request.user, pk=pk, **form.cleaned_data)
            except ValidationError as exc:
                _form_error(form, exc)
            except OperationalError:
                form.add_error(
                    None,
                    _("The action could not be confirmed. Reload the register before retrying."),
                )
                return render(
                    request, "garden/photo_edit.html", {"photo": photo, "form": form}, status=503
                )
            else:
                messages.success(request, _("Photo details saved."))
                return redirect(photo.owner)
        return render(request, "garden/photo_edit.html", {"photo": photo, "form": form}, status=422)
    return render(request, "garden/photo_edit.html", {"photo": photo, "form": form})


@require_http_methods(["GET", "POST"])
def photo_delete(request, pk):
    photo = _register_photo(pk)
    form = DeleteForm(
        request.POST if request.method == "POST" else None,
        initial={"expected_updated_at": photo.owner.updated_at},
    )
    if request.method == "POST":
        if _valid_post(request, form):
            try:
                services.delete_register_photo(
                    actor=request.user,
                    pk=pk,
                    expected_updated_at=form.cleaned_data["expected_updated_at"],
                )
            except ValidationError as exc:
                _form_error(form, exc)
            except MediaBusy, OperationalError, OSError:
                form.add_error(
                    None, _("Deletion could not be confirmed. Reload the register before retrying.")
                )
                return render(
                    request, "garden/photo_delete.html", {"photo": photo, "form": form}, status=503
                )
            else:
                messages.success(request, _("Photo deleted."))
                return redirect(photo.owner)
        return render(
            request, "garden/photo_delete.html", {"photo": photo, "form": form}, status=422
        )
    return render(request, "garden/photo_delete.html", {"photo": photo, "form": form})


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
    valid = form.is_valid()
    if valid:
        filters = form.cleaned_data
        year = filters["year"]
        records = planting_rows(planting_history(**filters))
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
            "year": year,
            "scope": scope,
            "kind": kind or "planting",
            "locked_filters": locked,
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
    template = (
        "garden/planting_details.html"
        if request.headers.get("HX-Request") == "true"
        else "garden/planting_detail.html"
    )
    return render(request, template, _planting_context(pk))


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
        valid = _valid_post(request, form, photo_fields={"photos"})
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
            except services.RegisterConflict as exc:
                _form_error(form, exc)
                status = 409
            except ValidationError as exc:
                _form_error(form, exc)
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
        if _valid_post(request, form):
            try:
                services.delete_planting(
                    actor=request.user,
                    pk=pk,
                    expected_updated_at=form.cleaned_data["expected_updated_at"],
                )
            except ValidationError as exc:
                _form_error(form, exc)
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
        valid = _valid_post(request, form)
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
            except services.RegisterConflict as exc:
                _form_error(form, exc)
                status = 409
            except ValidationError as exc:
                _form_error(form, exc)
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
        if _valid_post(request, form):
            try:
                services.delete_planting_event(
                    actor=request.user,
                    pk=pk,
                    expected_updated_at=form.cleaned_data["expected_updated_at"],
                )
            except ValidationError as exc:
                _form_error(form, exc)
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
