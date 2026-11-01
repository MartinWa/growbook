from pathlib import Path

from django.contrib.auth.decorators import login_not_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_safe
from django.views.decorators.vary import vary_on_headers

from .images import FORMATS
from .media import private_path
from .models import Photo


@login_not_required
@require_safe
def favicon(request):
    return redirect(static("icons/favicon.svg"), permanent=True)


@require_GET
@never_cache
def photo_detail(request, pk):
    photo = get_object_or_404(Photo.objects.select_related("entry"), pk=pk)
    return render(request, "core/photo.html", {"photo": photo})


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
