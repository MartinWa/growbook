"""Register lists, details and maintenance forms."""

import logging
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import OperationalError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.forms import DeleteForm, form_error, valid_post
from core.media import MediaBusy
from diary.queries import observation_count, observations_for

from ..forms import ArchiveForm, VarietyFilterForm
from ..models import Crop, CropCategory, GrowingArea
from ..names import normalize_name
from ..queries import area_tree, planting_history, planting_rows
from ..services import RegisterConflict
from ..services import registers as services
from ._shared import REGISTERS

logger = logging.getLogger(__name__)


def _context(kind):
    return {
        "kind": kind,
        "heading": _("Crops and varieties") if kind in ("crop", "variety") else REGISTERS[kind][2],
        "create_label": REGISTERS[kind][3],
        "list_url": reverse(
            "garden:variety-list" if kind in ("crop", "category") else f"garden:{kind}-list"
        ),
        "create_url": reverse(f"garden:{kind}-create"),
    }


@require_GET
def garden_home(request):
    return redirect("garden:area-list")


def _area_list(request):
    show_archived = request.GET.get("archived") == "1"
    areas = list(GrowingArea.objects.prefetch_related("photos"))
    tree = area_tree(
        areas=areas,
        visible_ids={area.pk for area in areas if show_archived or not area.is_archived},
    )
    records = [node.area for node in tree["roots"]]
    context = _context("area")
    context.update(
        records=records,
        has_records=bool(records),
        show_archived=show_archived,
        hide_archived_url=context["list_url"],
        empty_heading=_("No growing areas yet"),
        empty_description=_("Add a growing area to record where you grow and link observations."),
        empty_action_url=context["create_url"],
        empty_action_label=context["create_label"],
    )
    if not records and areas and not show_archived:
        context.update(
            empty_heading=_("No active records"),
            empty_description=_("Your archived records are available in the archive view."),
            empty_action_url=context["list_url"] + "?archived=1",
            empty_action_label=_("Show archived records"),
        )
    return render(request, "garden/areas.html", context)


def _archive_redirect(request, view_name, *, args=None):
    # The external submit button preserves even an unapplied search without JavaScript.
    filters = request.GET.copy()
    if filters.pop("archive_toggle")[-1] == "1":
        filters.setlist("archived", ["1"])
    else:
        filters.pop("archived", None)
    return redirect(reverse(view_name, args=args, query=filters))


@require_GET
def register_list(request, kind, category_pk=None, uncategorized=False, all_crops=False):
    if kind == "crop":
        query = request.GET.urlencode()
        return redirect(reverse("garden:all-crops") + ("?" + query if query else ""))
    partial = (
        kind == "variety"
        and request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-Target") == "register-results"
    )
    category = (
        get_object_or_404(CropCategory.objects.prefetch_related("photos"), pk=category_pk)
        if category_pk is not None
        else None
    )
    if (
        kind == "variety"
        and category is None
        and not uncategorized
        and not all_crops
        and not partial
    ):
        if any(key in request.GET for key in ("q", "archived", "archive_toggle")):
            return redirect(reverse("garden:all-crops") + "?" + request.GET.urlencode())
        return render(
            request,
            "garden/categories.html",
            {
                "heading": _("Crops and varieties"),
                "categories": CropCategory.objects.prefetch_related("photos"),
            },
        )
    if "archive_toggle" in request.GET:
        if category:
            return _archive_redirect(request, "garden:category-detail", args=[category.pk])
        if uncategorized:
            return _archive_redirect(request, "garden:uncategorized")
        if all_crops:
            return _archive_redirect(request, "garden:all-crops")
        return _archive_redirect(
            request, "garden:area-list" if kind == "area" else "garden:variety-list"
        )
    if kind == "area":
        return _area_list(request)
    model = REGISTERS[kind][0]
    context = _context(kind)
    context["list_url"] = request.path
    context.update(
        category=category,
        uncategorized=uncategorized,
        category_image=category.photos.first() if category else None,
        crop_create_url=reverse("garden:crop-create")
        + ("?category=" + str(category.pk) if category else ""),
    )
    if category or uncategorized:
        context["heading"] = category.name if category else _("Uncategorized")
    show_archived = request.GET.get("archived") == "1"
    records = model.objects.all()
    if kind == "variety":
        crops = category.crops.all() if category else Crop.objects.all()
        if uncategorized:
            crops = crops.filter(categories__isnull=True)
        records = records.filter(crop__in=crops)
        records = (
            records.select_related("crop")
            .prefetch_related("photos")
            .order_by("crop__name_key", "crop_id", "name_key", "id")
        )
    scoped_records = records
    if not show_archived:
        records = records.filter(is_archived=False)
    records = list(records)
    search = ""
    valid = True
    if kind == "variety":
        form = VarietyFilterForm(request.GET)
        valid = form.is_valid()
        search = form.cleaned_data.get("q", "")
        if not valid:
            records = []
        if search:
            key = normalize_name(search)[1]
            records = [
                record
                for record in records
                if key in record.name_key or key in record.crop.name_key
            ]
        by_crop = {}
        for record in records:
            by_crop.setdefault(record.crop_id, []).append(record)
        groups = []
        if valid:
            for crop in crops.order_by("name_key", "id"):
                varieties = by_crop.get(crop.pk, [])
                if not show_archived and crop.is_archived and not varieties:
                    continue
                if search and key not in crop.name_key and not varieties:
                    continue
                groups.append({"crop": crop, "records": varieties})
        context.update(form=form, variety_groups=groups, search=search)
    filters = {"q": search} if search else {}
    context.update(
        records=records,
        show_archived=show_archived,
        hide_archived_url=context["list_url"] + ("?" + urlencode(filters) if filters else ""),
        clear_search_url=context["list_url"] + ("?archived=1" if show_archived else ""),
        has_records=bool(groups) if kind == "variety" else bool(records),
    )
    if not context["has_records"]:
        context.update(
            empty_heading={
                "crop": _("No crops yet"),
                "variety": _("No crops or varieties yet"),
                "area": _("No growing areas yet"),
            }[kind],
            empty_description={
                "crop": _("Add a crop to organize varieties and start planning plantings."),
                "variety": _("Add a crop first, then record its varieties."),
                "area": _("Add a growing area to record where you grow and link observations."),
            }[kind],
            empty_action_url=context["create_url"],
            empty_action_label=context["create_label"],
        )
        if not valid:
            context.update(
                empty_heading=_("Check the filters"),
                empty_description=_("Choose a valid value, or clear the filters and try again."),
                empty_action_url=context["clear_search_url"],
                empty_action_label=_("Clear search"),
                empty_filter_link=True,
            )
        elif search:
            context.update(
                empty_heading=_("No crops or varieties match this search"),
                empty_description=_("Search variety or crop names, or clear the search to browse."),
                empty_action_url=context["clear_search_url"],
                empty_action_label=_("Clear search"),
                empty_filter_link=True,
            )
        elif not show_archived and (
            scoped_records.filter(is_archived=True).exists()
            or (kind == "variety" and crops.filter(is_archived=True).exists())
        ):
            context.update(
                empty_heading=_("No active records"),
                empty_description=_("Your archived records are available in the archive view."),
                empty_action_url=context["list_url"] + "?archived=1",
                empty_action_label=_("Show archived records"),
            )
        elif kind == "variety" and not crops.filter(is_archived=False).exists():
            context.update(
                empty_description=_("Add a crop first, then record its varieties."),
                empty_action_url=context["crop_create_url"],
                empty_action_label=_("New crop"),
            )
        if category and not category.crops.exists() and not search and valid:
            context.update(
                empty_heading=_("No crops in this category"),
                empty_description=_("Link existing crops or create a crop in this category."),
                empty_action_url=reverse("garden:category-edit", args=[category.pk]),
                empty_action_label=_("Edit category"),
            )
    return render(
        request,
        "garden/register_results.html" if partial else "garden/list.html",
        context,
        status=200 if valid else 422,
    )


def _detail_context(kind, pk, *, show_archived=False):
    model = REGISTERS[kind][0]
    if kind == "category":
        return {**_context(kind), "record": get_object_or_404(model, pk=pk)}
    queryset = model.objects.all()
    if kind == "crop":
        queryset = queryset.prefetch_related("varieties")
    elif kind == "variety":
        queryset = queryset.select_related("crop").prefetch_related("photos")
    else:
        queryset = queryset.select_related("parent").prefetch_related("photos", "children__photos")
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
    if kind == "area":
        areas = list(GrowingArea.objects.all())
        by_id = {area.pk: area for area in areas}
        ancestors, seen = [], {record.pk}
        parent = by_id.get(record.parent_id)
        while parent is not None and parent.pk not in seen:
            seen.add(parent.pk)
            ancestors.append(parent)
            parent = by_id.get(parent.parent_id)
        tree = area_tree(
            areas=areas,
            visible_ids={area.pk for area in areas if show_archived or not area.is_archived},
        )
        visible_ids = {event["node"].area.pk for event in tree["events"] if not event["closing"]}
        year = timezone.localdate().year
        context.update(
            ancestors=list(reversed(ancestors)),
            children=[child for child in record.children.all() if child.pk in visible_ids],
            area_image=record.photos.first(),
            show_archived=show_archived,
            hide_archived_url=record.get_absolute_url(),
            edit_url=reverse("garden:area-edit", args=[pk])
            + ("?archived=1" if show_archived else ""),
            plantings=planting_rows(planting_history(area=record, area_only=True), areas=areas),
            planting_year=year,
            planting_count=planting_history(year=year, area=record, area_only=True).count(),
            planting_url=reverse("garden:planting-list")
            + "?"
            + urlencode({"year": year, "area": record.pk, "area_only": "1"}),
        )
    return context


@require_GET
def register_detail(request, kind, pk):
    if kind == "area" and "archive_toggle" in request.GET:
        return _archive_redirect(request, "garden:area-detail", args=[pk])
    context = _detail_context(kind, pk, show_archived=request.GET.get("archived") == "1")
    context["inline"] = request.headers.get("HX-Request") == "true"
    if kind == "area":
        template = "garden/area_content.html" if context["inline"] else "garden/area_detail.html"
    else:
        template = "garden/details.html" if context["inline"] else "garden/detail.html"
    return render(request, template, context)


def _save(request, kind, pk, data):
    common = {"actor": request.user, "submission_key": data["submission_key"], "name": data["name"]}
    if pk is not None:
        common.update(pk=pk, expected_updated_at=data["expected_updated_at"])
    if kind == "crop":
        writer = services.edit_crop if pk is not None else services.create_crop
        return writer(
            **common, notes=data["notes"], **({"category": data["category"]} if pk is None else {})
        )
    if kind == "category":
        writer = services.edit_category if pk is not None else services.create_category
        return writer(
            **common,
            crops=[int(value) for value in data["crop_ids"]],
            uploads=data["photos"],
            **({"remove_image": data["remove_image"]} if pk is not None else {}),
        )
    if kind == "variety":
        writer = services.edit_variety if pk is not None else services.create_variety
        return writer(
            **common,
            crop=data["crop"],
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
        if kind == "category":
            initial["crop_ids"] = list(record.crops.values_list("pk", flat=True))
    if kind == "variety" and record is None:
        initial["crop"] = request.GET.get("crop")
    if kind == "crop" and record is None:
        initial["category"] = request.GET.get("category")
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
    if kind == "area":
        context["action"] = request.get_full_path()
        if record is not None:
            context["cancel_url"] = record.get_absolute_url() + (
                "?archived=1" if request.GET.get("archived") == "1" else ""
            )
    if kind == "category":
        context.update(photo_max_count=1, replace_image=True)
    if request.method == "POST":
        valid = valid_post(request, form, photo_fields={"photos"} if kind != "crop" else ())
        if record is not None and not form.cleaned_data.get("expected_updated_at"):
            form.add_error(None, _("Reload the edit form before saving."))
            valid = False
        status = 422
        if valid:
            try:
                result = _save(request, kind, pk, form.cleaned_data)
            except RegisterConflict as exc:
                form_error(form, exc)
                status = 409
            except ValidationError as exc:
                form_error(form, exc)
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
                target = result.record.get_absolute_url()
                if kind == "area" and request.GET.get("archived") == "1":
                    target += "?archived=1"
                if request.headers.get("HX-Request") == "true":
                    response = HttpResponse(status=204)
                    response["HX-Redirect"] = target
                    return response
                return redirect(target)
        if request.headers.get("HX-Request") == "true":
            return render(request, "garden/form_errors.html", {"form": form}, status=status)
        context["failed"] = True
        return render(request, "garden/form.html", context, status=status)
    return render(request, "garden/form.html", context)


@require_POST
def register_archive(request, kind, pk):
    get_object_or_404(REGISTERS[kind][0], pk=pk)
    form = ArchiveForm(request.POST)
    context = {**_context(kind), "form": form}
    if not valid_post(request, form):
        return render(request, "garden/action_error.html", context, status=422)
    writer = {
        "crop": services.archive_crop,
        "variety": services.archive_variety,
        "area": services.archive_area,
    }[kind]
    try:
        record = writer(actor=request.user, pk=pk, archived=form.cleaned_data["archived"])
    except ValidationError as exc:
        form_error(form, exc)
        return render(request, "garden/action_error.html", context, status=409)
    except MediaBusy, OperationalError, OSError:
        form.add_error(
            None, _("The action could not be confirmed. Reload the register before retrying.")
        )
        return render(request, "garden/action_error.html", context, status=503)
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
        if valid_post(request, form):
            writer = {
                "category": services.delete_category,
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
                form_error(form, exc)
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
