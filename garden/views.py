import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.media import MediaBusy
from core.models import Photo

from . import services
from .forms import (
    ArchiveForm,
    AreaForm,
    CropForm,
    DeleteForm,
    RegisterPhotoForm,
    VarietyForm,
)
from .models import Crop, GrowingArea, Variety
from .queries import area_paths

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
    photo = get_object_or_404(Photo.objects.select_related("area", "variety"), pk=pk)
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
