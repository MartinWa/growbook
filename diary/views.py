import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import OperationalError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_http_methods

from core.media import MediaBusy

from .forms import CaptureForm
from .models import DiaryEntry
from .services import SubmissionConflict, SubmissionDeleted, create_entry

logger = logging.getLogger(__name__)


@require_GET
def entry_list(request):
    entries = DiaryEntry.objects.select_related("author").prefetch_related("photos")
    return render(
        request,
        "diary/list.html",
        {
            "page_obj": Paginator(entries, 20).get_page(request.GET.get("page")),
        },
    )


@require_GET
def entry_detail(request, pk):
    entry = get_object_or_404(
        DiaryEntry.objects.select_related("author").prefetch_related("photos"), pk=pk
    )
    return render(request, "diary/detail.html", {"entry": entry})


@require_http_methods(["GET", "POST"])
def capture(request):
    form = CaptureForm(
        request.POST if request.method == "POST" else None,
        request.FILES if request.method == "POST" else None,
    )
    status = 422
    if request.method == "POST":
        valid = form.is_valid()
        if getattr(request, "photo_upload_error", None):
            form.add_error(None, request.photo_upload_error)
            valid = False
        if set(request.FILES) - {"camera", "library"}:
            form.add_error(None, _("An unexpected photo field was submitted. No entry was saved."))
            valid = False
        if valid:
            try:
                result = create_entry(
                    author=request.user,
                    date=form.cleaned_data["date"],
                    body=form.cleaned_data["body"],
                    uploads=form.cleaned_data["camera"] + form.cleaned_data["library"],
                    submission_key=form.cleaned_data["submission_key"],
                )
            except (SubmissionConflict, SubmissionDeleted) as exc:
                form.add_error(None, exc)
                status = 409
            except ValidationError as exc:
                form.add_error(None, exc)
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
                messages.success(
                    request,
                    _("Observation saved.") if result.created else _("Observation already saved."),
                )
                if request.headers.get("HX-Request") == "true":
                    response = HttpResponse(status=204)
                    response["HX-Redirect"] = result.entry.get_absolute_url()
                    return response
                return redirect(result.entry)
        if request.headers.get("HX-Request") == "true":
            return render(request, "diary/capture_errors.html", {"form": form}, status=status)
        return render(request, "diary/capture.html", {"form": form, "failed": True}, status=status)
    return render(request, "diary/capture.html", {"form": form})
