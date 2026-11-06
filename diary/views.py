import logging
from uuid import uuid4

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import OperationalError
from django.http import HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_http_methods

from core.forms import DeleteForm, form_error, valid_post
from core.media import MediaBusy
from core.models import Photo

from .forms import CaptureForm, DiaryFilterForm, EntryEditForm, EntryPhotoForm
from .models import DiaryEntry
from .permissions import can_edit_entry
from .queries import diary_years, filter_entries
from .services import (
    EntryConflict,
    SubmissionConflict,
    SubmissionDeleted,
    create_entry,
    delete_entry,
    delete_entry_photo,
    update_entry,
    update_entry_photo,
)

logger = logging.getLogger(__name__)

ENTRY_RELATIONS = (
    "photos",
    "links__area",
    "links__crop",
    "links__variety__crop",
    "links__planting__crop",
)


def _editable_entry(request, pk):
    entry = get_object_or_404(
        DiaryEntry.objects.select_related("author").prefetch_related(*ENTRY_RELATIONS), pk=pk
    )
    if not can_edit_entry(request.user, entry):
        raise PermissionDenied
    return entry


def _filter_query(value):
    data = QueryDict(value).copy()
    data.pop("page", None)
    return data.urlencode()


def _list_context(request, *, data=None, capture_form=None):
    data = request.GET.copy() if data is None else data.copy()
    form = DiaryFilterForm(data, years=diary_years())
    valid = form.is_valid()
    page_obj = None
    if valid:
        entries = (
            filter_entries(**form.cleaned_data)
            .select_related("author")
            .prefetch_related(*ENTRY_RELATIONS)
        )
        page_obj = Paginator(entries, 20).get_page(data.get("page"))
    else:
        page_obj = Paginator(DiaryEntry.objects.none(), 20).get_page(1)
    query = data.copy()
    query.pop("page", None)
    for entry in page_obj.object_list:
        entry.can_edit = can_edit_entry(request.user, entry)
    active_filters = []
    for name, field in form.fields.items():
        value = data.get(name)
        if not value:
            continue
        selected = form.cleaned_data.get(name, value)
        display = field.label_from_instance(selected) if hasattr(selected, "pk") else selected
        remaining = query.copy()
        remaining.pop(name, None)
        active_filters.append(
            {
                "label": field.label,
                "value": display,
                "url": reverse("diary:list") + ("?" + remaining.urlencode() if remaining else ""),
            }
        )
    return {
        "form": form,
        "capture_form": capture_form or CaptureForm(quick=True),
        "page_obj": page_obj,
        "querystring": query.urlencode(),
        "filter_query": query.urlencode(),
        "valid_filter": valid,
        "active_filters": active_filters,
        "diary_has_entries": DiaryEntry.objects.exists(),
    }


@require_GET
def entry_list(request):
    context = _list_context(request)
    partial = (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-Target") == "diary-results"
    )
    return render(
        request,
        "diary/_results.html" if partial else "diary/list.html",
        context,
        status=200 if context["valid_filter"] else 422,
    )


@require_GET
def entry_detail(request, pk):
    entry = get_object_or_404(
        DiaryEntry.objects.select_related("author").prefetch_related(*ENTRY_RELATIONS), pk=pk
    )
    return render(
        request,
        "diary/detail.html",
        {"entry": entry, "can_edit": can_edit_entry(request.user, entry)},
    )


@require_http_methods(["GET", "POST"])
def entry_edit(request, pk):
    entry = _editable_entry(request, pk)
    links = entry.links.all()
    initial = {
        "expected_updated_at": entry.updated_at,
        "date": entry.date,
        "body": entry.body,
        "link_areas": [link.area_id for link in links if link.area_id],
        "link_crops": [link.crop_id for link in links if link.crop_id],
        "link_varieties": [link.variety_id for link in links if link.variety_id],
        "link_plantings": [link.planting_id for link in links if link.planting_id],
    }
    form = EntryEditForm(
        request.POST if request.method == "POST" else None,
        request.FILES if request.method == "POST" else None,
        entry=entry,
        initial=initial,
    )
    context = {
        "entry": entry,
        "form": form,
        "title": _("Edit observation"),
        "action": request.path,
    }
    if request.method == "POST":
        valid = valid_post(request, form, photo_fields={"photos"})
        status = 422
        if valid:
            values = form.cleaned_data
            try:
                update_entry(
                    actor=request.user,
                    pk=entry.pk,
                    date=values["date"],
                    body=values["body"],
                    links=form.selected_links,
                    uploads=values["photos"],
                    remove_photo_ids=values["remove_photos"],
                    expected_updated_at=values["expected_updated_at"],
                    submission_key=values["submission_key"],
                )
            except EntryConflict as exc:
                form_error(form, exc)
                status = 409
            except ValidationError as exc:
                form_error(form, exc)
            except MediaBusy, OperationalError, OSError:
                logger.exception("Entry edit failed; keep the form open for a retry.")
                form.add_error(
                    None,
                    _(
                        "The save could not be completed. Keep this form open and retry; "
                        "your note and selections are preserved."
                    ),
                )
                status = 503
            else:
                messages.success(request, _("Observation saved."))
                if request.headers.get("HX-Request") == "true":
                    response = HttpResponse(status=204)
                    response["HX-Redirect"] = entry.get_absolute_url()
                    return response
                return redirect(entry)
        if request.headers.get("HX-Request") == "true":
            return render(request, "diary/form_errors.html", {"form": form}, status=status)
        context["failed"] = True
        return render(request, "diary/edit.html", context, status=status)
    return render(request, "diary/edit.html", context)


@require_http_methods(["GET", "POST"])
def entry_delete(request, pk):
    entry = _editable_entry(request, pk)
    form = DeleteForm(
        request.POST if request.method == "POST" else None,
        initial={"expected_updated_at": entry.updated_at},
    )
    context = {"entry": entry, "form": form}
    if request.method == "POST":
        if form.is_valid():
            try:
                delete_entry(
                    actor=request.user,
                    pk=entry.pk,
                    expected_updated_at=form.cleaned_data["expected_updated_at"],
                )
            except EntryConflict as exc:
                form_error(form, exc)
            except ValidationError as exc:
                form_error(form, exc)
            except MediaBusy, OperationalError, OSError:
                form.add_error(
                    None, _("Deletion could not be confirmed. Reload the entry before retrying.")
                )
                return render(request, "diary/delete.html", context, status=503)
            else:
                messages.success(request, _("Observation deleted."))
                return redirect("diary:list")
        return render(request, "diary/delete.html", context, status=422)
    return render(request, "diary/delete.html", context)


def _editable_photo(request, pk):
    photo = get_object_or_404(Photo.objects.select_related("entry__author"), pk=pk)
    if photo.entry_id is None or not can_edit_entry(request.user, photo.entry):
        raise PermissionDenied
    return photo


@require_http_methods(["GET", "POST"])
def photo_edit(request, pk):
    photo = _editable_photo(request, pk)
    form = EntryPhotoForm(
        request.POST if request.method == "POST" else None,
        initial={
            "expected_updated_at": photo.entry.updated_at,
            "caption": photo.caption,
            "position": photo.position,
        },
    )
    context = {"photo": photo, "entry": photo.entry, "form": form}
    if request.method == "POST":
        if form.is_valid():
            try:
                update_entry_photo(
                    actor=request.user,
                    pk=photo.pk,
                    caption=form.cleaned_data["caption"],
                    position=form.cleaned_data["position"],
                    expected_updated_at=form.cleaned_data["expected_updated_at"],
                )
            except EntryConflict as exc:
                form_error(form, exc)
            except ValidationError as exc:
                form_error(form, exc)
            except OperationalError:
                form.add_error(
                    None, _("The action could not be confirmed. Reload the entry before retrying.")
                )
                return render(request, "diary/photo_edit.html", context, status=503)
            else:
                messages.success(request, _("Photo details saved."))
                return redirect(photo.entry)
        return render(request, "diary/photo_edit.html", context, status=422)
    return render(request, "diary/photo_edit.html", context)


@require_http_methods(["GET", "POST"])
def photo_delete(request, pk):
    photo = _editable_photo(request, pk)
    form = DeleteForm(
        request.POST if request.method == "POST" else None,
        initial={"expected_updated_at": photo.entry.updated_at},
    )
    context = {"photo": photo, "entry": photo.entry, "form": form}
    if request.method == "POST":
        if form.is_valid():
            try:
                delete_entry_photo(
                    actor=request.user,
                    pk=photo.pk,
                    expected_updated_at=form.cleaned_data["expected_updated_at"],
                )
            except EntryConflict as exc:
                form_error(form, exc)
            except ValidationError as exc:
                form_error(form, exc)
            except MediaBusy, OperationalError, OSError:
                form.add_error(
                    None, _("Deletion could not be confirmed. Reload the entry before retrying.")
                )
                return render(request, "diary/photo_delete.html", context, status=503)
            else:
                messages.success(request, _("Photo deleted."))
                return redirect(photo.entry)
        return render(request, "diary/photo_delete.html", context, status=422)
    return render(request, "diary/photo_delete.html", context)


@require_http_methods(["GET", "POST"])
def capture(request):
    if request.method == "GET":
        return redirect(reverse("diary:list") + "#id_body")
    quick = request.method == "POST" and request.POST.get("capture_mode") == "quick"
    form = CaptureForm(
        request.POST if request.method == "POST" else None,
        request.FILES if request.method == "POST" else None,
        quick=quick,
    )
    status = 422
    if request.method == "POST":
        valid = valid_post(
            request,
            form,
            photo_fields={"camera", "library"},
            unexpected_photo_message=_(
                "An unexpected photo field was submitted. No entry was saved."
            ),
        )
        if valid:
            try:
                result = create_entry(
                    author=request.user,
                    date=form.cleaned_data["date"],
                    body=form.cleaned_data["body"],
                    uploads=form.cleaned_data["camera"] + form.cleaned_data["library"],
                    submission_key=form.cleaned_data["submission_key"],
                    links=form.selected_links,
                )
            except (SubmissionConflict, SubmissionDeleted) as exc:
                form.add_error(None, exc)
                status = 409
            except ValidationError as exc:
                form_error(form, exc)
            except MediaBusy, OperationalError, OSError:
                logger.exception("Capture failed; a stable submission key makes retries safe.")
                form.add_error(
                    None,
                    _(
                        "The save could not be completed. Keep this form open and retry; "
                        "the same submission will not create a duplicate."
                    ),
                )
                status = 503
            else:
                if quick and request.headers.get("HX-Request") == "true":
                    response = render(
                        request,
                        "diary/_quick_saved.html",
                        {"entry": result.entry, "created": result.created, "next_key": uuid4()},
                    )
                    response["HX-Retarget"] = "#quick-receipt"
                    response["HX-Reswap"] = "innerHTML"
                    return response
                messages.success(
                    request,
                    _("Observation saved.") if result.created else _("Observation already saved."),
                )
                if request.headers.get("HX-Request") == "true":
                    response = HttpResponse(status=204)
                    response["HX-Redirect"] = result.entry.get_absolute_url()
                    return response
                if quick:
                    query = _filter_query(request.POST.get("filter_query", ""))
                    destination = reverse("diary:list") + ("?" + query if query else "")
                    return redirect(f"{destination}#entry-{result.entry.pk}")
                return redirect(result.entry)
        if request.headers.get("HX-Request") == "true":
            return render(request, "diary/capture_errors.html", {"form": form}, status=status)
        context = _list_context(
            request,
            data=QueryDict(request.POST.get("filter_query", "")),
            capture_form=form,
        )
        context["failed"] = True
        return render(request, "diary/list.html", context, status=status)
