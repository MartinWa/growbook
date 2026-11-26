from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import ValidationError
from django.http import (
    FileResponse,
    Http404,
    HttpResponseBadRequest,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import (
    require_GET,
    require_http_methods,
    require_POST,
    require_safe,
)
from django.views.decorators.vary import vary_on_headers

from diary.permissions import can_edit_entry
from garden.views._shared import return_url

from .api_tokens import delete_token, issue_token, revoke_token, tokens_for
from .appearance import SEASON_COOKIE, SEASON_COOKIE_AGE, SEASON_MODES
from .forms import ApiTokenForm, ConfirmForm, form_error
from .images import FORMATS
from .media import private_path
from .models import ApiToken, Photo
from .releases import update_available


@login_not_required
@require_safe
def favicon(request):
    return redirect(static("icons/favicon.svg"), permanent=True)


@login_not_required
@require_POST
def set_season(request):
    requested_mode = request.POST.get("season")
    mode = next((allowed for allowed in SEASON_MODES if allowed == requested_mode), None)
    if mode is None:
        return HttpResponseBadRequest(_("Choose a valid season."))
    destination = request.POST.get("next", "/")
    if destination.startswith("/") and url_has_allowed_host_and_scheme(
        destination, allowed_hosts=None
    ):
        response = HttpResponseRedirect(destination)
    else:
        response = HttpResponseRedirect("/")
    response.set_cookie(
        SEASON_COOKIE,
        mode,
        max_age=SEASON_COOKIE_AGE,
        secure=request.is_secure(),
        samesite="Lax",
    )
    return response


@require_GET
@never_cache
def release_status(request):
    return JsonResponse({"update_available": update_available()})


@require_GET
@never_cache
def photo_detail(request, pk):
    photo = get_object_or_404(
        Photo.objects.select_related(
            "entry", "area", "variety__crop", "planting__crop", "category", "planting_category"
        ),
        pk=pk,
    )
    return render(
        request,
        "core/photo.html",
        {
            "photo": photo,
            "can_edit": bool(photo.entry_id and can_edit_entry(request.user, photo.entry)),
            "destination": return_url(request, planting_edit=photo.planting_id)
            if photo.entry_id is None
            else "",
        },
    )


@require_GET
@never_cache
@vary_on_headers("Sec-Fetch-Dest")
def photo_file(request, pk, variant):
    if variant not in {"original", "display", "thumbnail"}:
        raise Http404
    photo = get_object_or_404(Photo, pk=pk)
    # Browser image documents inject inline styles. Navigate to our HTML viewer
    # without relaxing CSP; embedded images and original downloads remain files.
    if variant != "original" and request.headers.get("Sec-Fetch-Dest") == "document":
        return redirect(photo)
    field = getattr(photo, variant)
    try:
        stream = private_path(field.name).open("rb")
    except (OSError, ValueError) as exc:
        raise Http404 from exc
    original = variant == "original"
    response = FileResponse(
        stream,
        as_attachment=original,
        filename=f"photo-{photo.pk}{Path(field.name).suffix}",
        content_type=FORMATS[photo.original_format][1] if original else "image/webp",
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response


def csrf_failure(request, reason=""):
    return render(request, "errors/csrf.html", status=403)


@require_http_methods(["GET", "POST"])
def api_tokens(request):
    """Create a named credential and show its secret once; list the account's tokens."""
    form = ApiTokenForm(request.POST if request.method == "POST" else None, user=request.user)
    secret = None
    created_name = ""
    status = 200
    if request.method == "POST":
        status = 422
        if form.is_valid():
            try:
                record, secret = issue_token(
                    actor=request.user,
                    name=form.cleaned_data["name"],
                    expires_at=form.cleaned_data["expires_at"],
                )
            except ValidationError as error:
                form_error(form, error)
                status = 409
            else:
                created_name = record.name
                form = ApiTokenForm(user=request.user)
                status = 200
    return render(
        request,
        "core/api_tokens.html",
        {
            "form": form,
            "tokens": tokens_for(request.user),
            "secret": secret,
            "created_name": created_name,
        },
        status=status,
    )


@require_POST
def api_token_revoke(request, pk):
    token = get_object_or_404(ApiToken.objects.filter(user=request.user), pk=pk)
    try:
        revoke_token(actor=request.user, token=token)
    except ValidationError as error:
        messages.error(request, error.messages[0])
    else:
        messages.success(
            request,
            _("Revoked %(name)s. Clients using it stop working now.") % {"name": token.name},
        )
    return redirect("api-tokens")


@require_http_methods(["GET", "POST"])
def api_token_delete(request, pk):
    """Confirm before removing a credential, like every other destructive action."""
    token = get_object_or_404(ApiToken.objects.filter(user=request.user), pk=pk)
    form = ConfirmForm(request.POST if request.method == "POST" else None)
    status = 200
    if request.method == "POST":
        status = 422
        if form.is_valid():
            name = token.name
            try:
                delete_token(actor=request.user, token=token)
            except ValidationError as error:
                messages.error(request, error.messages[0])
            else:
                messages.success(
                    request,
                    _("Deleted %(name)s. That name can be used again.") % {"name": name},
                )
            return redirect("api-tokens")
    return render(
        request,
        "core/api_token_delete.html",
        {"token": token, "form": form},
        status=status,
    )
