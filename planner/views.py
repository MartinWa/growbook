"""Calendar views: the read-only month, planned tasks and the shared month note."""

from datetime import date

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import OperationalError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.forms import DeleteForm, form_error, valid_post

from . import queries, services
from .forms import CalendarFilterForm, MonthNoteForm, TaskActionForm, TaskForm, parse_link
from .models import PlannedTask

RESULTS_ID = "calendar-results"
FILTER_FIELDS = ("type", "area", "crop", "variety", "planting")
CHIP_LIMIT = 40


def _trim(value):
    text = " ".join(str(value).split())
    return text if len(text) <= CHIP_LIMIT else text[: CHIP_LIMIT - 1] + "…"


def _active_filters(request, form, year, month):
    """One removable chip per chosen filter, keeping the other choices."""
    base = reverse("planner:month", args=[year, month])
    chips = []
    for name in FILTER_FIELDS:
        # An empty select still submits its name when a form is sent without JavaScript.
        chosen = [value for value in request.GET.getlist(name) if value]
        if not chosen:
            continue
        field = form.fields[name]
        if name == "type":
            display = ", ".join(str(dict(field.choices).get(value, value)) for value in chosen)
        else:
            selected = form.cleaned_data.get(name)
            display = field.label_from_instance(selected) if selected is not None else chosen[0]
        remaining = request.GET.copy()
        for key in (name, "item", "day"):
            remaining.pop(key, None)
        query = remaining.urlencode()
        chips.append(
            {
                "label": field.label,
                "value": _trim(display),
                "url": f"{base}?{query}" if query else base,
            }
        )
    return chips


def _month_url(day):
    return reverse("planner:month", args=[day.year, day.month])


def _task_url(task):
    """The month the task is shown in, with the task opened."""
    return f"{_month_url(task.display_day)}?item=task-{task.pk}"


def _month_page(request, year, month, *, note_form=None, status=200):
    """The month page and its HTMX partial share this one context."""
    today = timezone.localdate()
    if not queries.year_is_valid(year, today) or not queries.month_is_valid(month):
        raise Http404
    form = CalendarFilterForm(request.GET)
    if not form.is_valid():
        raise Http404
    address = None
    if request.GET.get("item"):
        address = queries.parse_item(request.GET["item"])
        if address is None:
            raise Http404
    day = None
    if request.GET.get("day"):
        day = queries.parse_day(request.GET["day"], year, month)
        if day is None:
            raise Http404
    context = queries.month_context(
        year, month, today=today, filters=form.values, address=address, day=day
    )
    if address is not None and context["panel"] is None:
        raise Http404
    close_query = request.GET.copy()
    for key in ("item", "day"):
        close_query.pop(key, None)
    close_url = reverse("planner:month", args=[year, month])
    if close_query:
        close_url = f"{close_url}?{close_query.urlencode()}"
    if context["panel"] is not None:
        context["panel"] = {**context["panel"], "close_url": close_url, "open": True}
        item = context["panel"].get("item")
        if item is not None and item.task is not None:
            context["panel"]["action_form"] = TaskActionForm(
                initial={
                    "expected_updated_at": item.task.updated_at,
                    "completed_on": today,
                }
            )
    if note_form is None:
        note = context["month_note"]
        # A prefix keeps the note fields' ids unique beside the panel's task form.
        note_form = MonthNoteForm(
            prefix="note",
            initial={
                "body": note.body if note else "",
                "expected_updated_at": note.updated_at if note else None,
            },
        )
    # The note belongs to the month; carrying item/day would reopen a dismissed panel.
    note_parameters = request.GET.copy()
    for key in ("item", "day"):
        note_parameters.pop(key, None)
    note_query = note_parameters.urlencode()
    note_action = reverse("planner:note", args=[year, month])
    context.update(
        {
            "form": form,
            "results_id": RESULTS_ID,
            "clear_filters_url": reverse("planner:month", args=[year, month]),
            "active_filters": _active_filters(request, form, year, month),
            "note_form": note_form,
            "note_action": f"{note_action}?{note_query}" if note_query else note_action,
            "new_task_url": (
                f"{reverse('planner:task-new', args=[year, month])}"
                f"?date={context['new_task_date'].isoformat()}"
            ),
        }
    )
    partial = (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-Target") == RESULTS_ID
    )
    return render(
        request,
        "planner/_month_results.html" if partial else "planner/month.html",
        context,
        status=status,
    )


@require_GET
def current_month(request):
    today = timezone.localdate()
    url = reverse("planner:month", args=[today.year, today.month])
    query = request.GET.copy()
    for key in ("item", "day"):
        query.pop(key, None)
    if query:
        url = f"{url}?{query.urlencode()}"
    return redirect(url)


@require_GET
def month_view(request, year, month):
    return _month_page(request, year, month)


def _valid_month(year, month):
    if not queries.year_is_valid(year) or not queries.month_is_valid(month):
        raise Http404


def _task_initial(record=None, *, planned_date=None, title="", note="", link=()):
    initial = {"planned_date": planned_date, "title": title, "note": note}
    if link:
        initial["link"] = f"{link[0]}-{link[1]}"
    if record is not None:
        initial.update(
            {
                "planned_date": record.planned_date,
                "title": record.title,
                "note": record.note,
                "expected_updated_at": record.updated_at,
            }
        )
    return initial


def _link_address(link):
    """A task or diary link in the task form's address format."""
    if link is None:
        return ()
    return next(
        (field, getattr(link, f"{field}_id"))
        for field in ("area", "crop", "variety", "planting")
        if getattr(link, f"{field}_id") is not None
    )


def _source_task_values(item, year, month):
    """Prefill a new task from a previous-year item, always asking for a date."""
    day = min(item.day.day, queries.month_bounds(year, month)[1].day)
    link = ()
    if item.task is not None:
        title = item.task.title
        note = item.task.note
        first = next(iter(item.task.links.all()), None)
        link = _link_address(first)
    elif item.event is not None:
        planting = item.event.planting
        title = f"{item.event.get_kind_display()}: {planting.label or planting.crop}"
        note = item.event.note
        link = ("planting", planting.pk)
    else:
        title = item.entry.title
        note = item.entry.body.strip()
        # The calendar prefetch contains only planting links for event pairing.
        # A fresh query sees the observation's area, crop and variety links too.
        entry_links = list(item.entry.links.filter())
        first = next((link for link in entry_links if link.planting_id is not None), None)
        if first is None and len(entry_links) == 1:
            first = entry_links[0]
        link = _link_address(first)
    return _task_initial(planned_date=date(year, month, day), title=title, note=note, link=link)


def _requested_date(request, year, month):
    """A ``date`` query value inside the shown month, or its first day."""
    first, last = queries.month_bounds(year, month)
    value = request.GET.get("date")
    if value:
        try:
            chosen = date.fromisoformat(value)
        except ValueError:
            chosen = None
        if chosen is not None and first <= chosen <= last:
            return chosen
    return first


@require_http_methods(["GET", "POST"])
def task_new(request, year, month):
    _valid_month(year, month)
    planned_date = _requested_date(request, year, month)
    initial = {"planned_date": planned_date}
    current_link = ()
    if request.method != "POST" and request.GET.get("from"):
        address = queries.parse_item(request.GET["from"])
        if address is None:
            raise Http404
        item = queries.find_item(queries.previous_year_items(year, month), *address)
        if item is None:
            raise Http404
        initial = _source_task_values(item, year, month)
        if initial.get("link"):
            current_link = parse_link(initial["link"])
    form = TaskForm(
        request.POST if request.method == "POST" else None,
        initial=initial,
        current_link=current_link,
    )
    context = {
        "title": _("New task"),
        "form": form,
        "record": None,
        "action": request.path,
        "cancel_url": _month_url(planned_date),
    }
    return _task_write(request, form, context, record=None)


def _task_write(request, form, context, *, record):
    if request.method != "POST":
        return render(request, "planner/task_form.html", context)
    status = 422
    if valid_post(request, form):
        values = {
            "actor": request.user,
            "planned_date": form.cleaned_data["planned_date"],
            "title": form.cleaned_data["title"],
            "note": form.cleaned_data["note"],
            "links": form.links,
            "submission_key": form.cleaned_data["submission_key"],
        }
        try:
            if record is None:
                result = services.create_task(**values)
            else:
                result = services.edit_task(
                    pk=record.pk,
                    expected_updated_at=form.cleaned_data.get("expected_updated_at"),
                    **values,
                )
        except services.PlannerConflict as exc:
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
            saved = _("Task already saved.") if result.replayed else _("Task saved.")
            messages.success(request, saved)
            if request.headers.get("HX-Request") == "true":
                response = HttpResponse(status=204)
                response["HX-Redirect"] = _task_url(result.task)
                response["X-Growbook-Saved-Key"] = str(values["submission_key"])
                return response
            return redirect(_task_url(result.task))
    if request.headers.get("HX-Request") == "true":
        return render(request, "planner/form_errors.html", {"form": form}, status=status)
    return render(request, "planner/task_form.html", context, status=status)


@require_http_methods(["GET", "POST"])
def task_edit(request, pk):
    record = get_object_or_404(PlannedTask, pk=pk)
    first = next(iter(record.links.all()), None)
    current_link = _link_address(first)
    form = TaskForm(
        request.POST if request.method == "POST" else None,
        initial=_task_initial(record),
        current_link=current_link,
    )
    context = {
        "title": _("Edit task"),
        "form": form,
        "record": record,
        "action": request.path,
        "cancel_url": _month_url(record.display_day),
    }
    return _task_write(request, form, context, record=record)


def _action(request, pk, *, complete):
    """Tick a task off or reopen it, then return to the month it is shown in."""
    record = get_object_or_404(PlannedTask, pk=pk)
    form = TaskActionForm(request.POST)
    if not valid_post(request, form):
        messages.error(request, _("The task was not changed. Reload the calendar and try again."))
        return redirect(_month_url(record.display_day))
    try:
        if complete:
            result = services.complete_task(
                actor=request.user,
                pk=record.pk,
                completed_on=form.cleaned_data.get("completed_on") or timezone.localdate(),
                submission_key=form.cleaned_data["submission_key"],
                expected_updated_at=form.cleaned_data["expected_updated_at"],
            )
            messages.success(
                request, _("Task already done.") if result.replayed else _("Task done.")
            )
        else:
            result = services.reopen_task(
                actor=request.user,
                pk=record.pk,
                submission_key=form.cleaned_data["submission_key"],
                expected_updated_at=form.cleaned_data["expected_updated_at"],
            )
            messages.success(
                request, _("Task already reopened.") if result.replayed else _("Task reopened.")
            )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect(_month_url(record.display_day))
    except OperationalError:
        messages.error(
            request,
            _("The change could not be completed. Reload the calendar and try again."),
        )
        return redirect(_month_url(record.display_day))
    return redirect(_task_url(result.task))


@require_POST
def task_complete(request, pk):
    return _action(request, pk, complete=True)


@require_POST
def task_reopen(request, pk):
    return _action(request, pk, complete=False)


@require_http_methods(["GET", "POST"])
def task_delete(request, pk):
    record = get_object_or_404(PlannedTask, pk=pk)
    form = DeleteForm(
        request.POST if request.method == "POST" else None,
        initial={"expected_updated_at": record.updated_at},
    )
    context = {"task": record, "form": form, "cancel_url": _month_url(record.display_day)}
    if request.method == "POST":
        status = 422
        if valid_post(request, form):
            try:
                services.delete_task(
                    actor=request.user,
                    pk=record.pk,
                    expected_updated_at=form.cleaned_data["expected_updated_at"],
                )
            except ValidationError as exc:
                form_error(form, exc)
                status = 409
            except OperationalError:
                form.add_error(
                    None, _("Deletion could not be confirmed. Reload the calendar before retrying.")
                )
                status = 503
            else:
                messages.success(request, _("Task deleted."))
                return redirect(_month_url(record.display_day))
        return render(request, "planner/task_delete.html", context, status=status)
    return render(request, "planner/task_delete.html", context)


@require_http_methods(["GET", "POST"])
def note_save(request, year, month):
    _valid_month(year, month)
    month_url = reverse("planner:month", args=[year, month], query=request.GET)
    # Failed POSTs render the month here; relative item and language links use GET.
    if request.method == "GET":
        return redirect(month_url)
    form = MonthNoteForm(request.POST, prefix="note")
    status = 422
    if valid_post(request, form):
        try:
            result = services.save_month_note(
                actor=request.user,
                year=year,
                month=month,
                body=form.cleaned_data["body"],
                submission_key=form.cleaned_data["submission_key"],
                expected_updated_at=form.cleaned_data.get("expected_updated_at"),
            )
        except services.PlannerConflict as exc:
            form_error(form, exc)
            status = 409
        except ValidationError as exc:
            form_error(form, exc)
        except OperationalError:
            form.add_error(
                None,
                _(
                    "The note could not be saved. "
                    "Keep this page open and retry the same submission."
                ),
            )
            status = 503
        else:
            saved = _("Note already saved.") if result.replayed else _("Note saved.")
            messages.success(request, saved)
            return redirect(month_url)
    return _month_page(request, year, month, note_form=form, status=status)
