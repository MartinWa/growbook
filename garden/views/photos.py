"""Maintenance forms for variety, growing-area and planting photos."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from core.forms import DeleteForm, form_error, valid_post
from core.media import MediaBusy
from core.models import Photo

from ..forms import RegisterPhotoForm
from ..services import photos as services
from ._shared import management_url, return_url


def _register_photo(pk):
    photo = get_object_or_404(
        Photo.objects.select_related(
            "area", "variety", "planting__crop", "category", "planting_category"
        ),
        pk=pk,
    )
    if photo.entry_id is not None:
        raise PermissionDenied
    return photo


def _photo_destination(request, photo):
    if photo.area_id is not None:
        return management_url(request, "garden:area-manage", selected=photo.area_id)
    if photo.category_id is not None:
        return management_url(request, "garden:category-manage")
    if photo.planting_category_id is not None:
        return management_url(request, "garden:planting-category-manage")
    return return_url(request) or photo.owner.get_absolute_url()


@require_http_methods(["GET", "POST"])
def photo_edit(request, pk):
    photo = _register_photo(pk)
    destination = _photo_destination(request, photo)
    form = RegisterPhotoForm(
        request.POST if request.method == "POST" else None,
        initial={
            "caption": photo.caption,
            "position": photo.position,
            "expected_updated_at": photo.owner.updated_at,
        },
    )
    context = {"photo": photo, "form": form, "destination": destination}
    if request.method == "POST":
        if valid_post(request, form):
            try:
                services.edit_register_photo(actor=request.user, pk=pk, **form.cleaned_data)
            except ValidationError as exc:
                form_error(form, exc)
            except OperationalError:
                form.add_error(
                    None,
                    _("The action could not be confirmed. Reload the register before retrying."),
                )
                return render(request, "garden/photo_edit.html", context, status=503)
            else:
                messages.success(request, _("Photo details saved."))
                return redirect(destination)
        return render(request, "garden/photo_edit.html", context, status=422)
    return render(request, "garden/photo_edit.html", context)


@require_http_methods(["GET", "POST"])
def photo_delete(request, pk):
    photo = _register_photo(pk)
    destination = _photo_destination(request, photo)
    form = DeleteForm(
        request.POST if request.method == "POST" else None,
        initial={"expected_updated_at": photo.owner.updated_at},
    )
    context = {"photo": photo, "form": form, "destination": destination}
    if request.method == "POST":
        if valid_post(request, form):
            try:
                services.delete_register_photo(
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
                return render(request, "garden/photo_delete.html", context, status=503)
            else:
                messages.success(request, _("Photo deleted."))
                return redirect(destination)
        return render(request, "garden/photo_delete.html", context, status=422)
    return render(request, "garden/photo_delete.html", context)
