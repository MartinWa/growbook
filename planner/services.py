"""Validated calendar writes: planned tasks, links and the shared month note."""

from dataclasses import dataclass
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from core import receipts
from core.models import User
from core.writes import validated_write
from garden.models import Crop, GrowingArea, Planting, Variety

from .models import MonthNote, PlannedTask, PlannerSubmission, TaskLink

TASK_KINDS = ("task", "note")


class PlannerConflict(ValidationError):
    """A task or note changed, disappeared, or was deleted while the form was open."""


@dataclass(frozen=True)
class TaskResult:
    task: PlannedTask
    replayed: bool = False


@dataclass(frozen=True)
class NoteResult:
    note: MonthNote
    replayed: bool = False


LINK_FIELDS = {
    GrowingArea: "area",
    Crop: "crop",
    Variety: "variety",
    Planting: "planting",
}
LINK_MODELS = {field: model for model, field in LINK_FIELDS.items()}


def _outside_transaction(actor):
    if connection.in_atomic_block:
        raise RuntimeError("Call calendar operations outside any existing transaction.")
    if not getattr(actor, "is_authenticated", False) or not getattr(actor, "is_active", False):
        raise PermissionDenied


def _active_actor(actor):
    if not User.objects.filter(pk=actor.pk, is_active=True).exists():
        raise PermissionDenied


def _positive_id(value, message=None):
    if type(value) is not int or value <= 0:
        raise ValidationError(message or _("Choose a valid record."))
    return value


def _date_value(value, field):
    if not isinstance(value, date) or type(value) is not date:
        raise ValidationError({field: _("Enter a valid date.")})
    return value


def _current(model, pk, expected_updated_at, *, missing, changed):
    try:
        record = model.objects.get(pk=pk)
    except model.DoesNotExist as exc:
        raise PlannerConflict(missing) from exc
    if expected_updated_at is not None and record.updated_at != expected_updated_at:
        raise PlannerConflict(changed)
    return record


def _fingerprint(kind, action, pk, expected_updated_at, values):
    """This app's receipt payload; the digest itself comes from ``core.receipts``."""
    return receipts.fingerprint(
        {
            "version": 1,
            "kind": kind,
            "action": action,
            "pk": pk,
            "expected_updated_at": expected_updated_at.isoformat() if expected_updated_at else None,
            "values": receipts.canonical(values),
        }
    )


def _receipt(key, actor, kind, fingerprint):
    receipt = PlannerSubmission.objects.filter(key=key).first()
    target = receipts.accepted_target(
        receipt,
        actor.pk,
        digest=fingerprint,
        kind=kind,
        conflict=PlannerConflict(
            _("This save key was already used with different content. Nothing was overwritten.")
        ),
        deleted=PlannerConflict(_("This record was deleted. It was not recreated.")),
    )
    return target


def _normalize_links(links):
    normalized = {}
    for target in links:
        if isinstance(target, tuple) and len(target) == 2:
            field, target_pk = target
            if not isinstance(field, str) or field not in LINK_MODELS:
                raise ValidationError(_("Choose a valid record to link."))
        else:
            field = next(
                (name for model, name in LINK_FIELDS.items() if isinstance(target, model)), None
            )
            target_pk = getattr(target, "pk", None)
        if field is None:
            raise ValidationError(_("Choose a valid record to link."))
        key = (field, _positive_id(target_pk, _("Choose a valid record to link.")))
        if key in normalized:
            raise ValidationError(_("A record can only be linked once."))
        normalized[key] = target
    return normalized


def _resolve_links(links, previous_links=()):
    resolved = {}
    for field, target_pk in links:
        model = LINK_MODELS[field]
        try:
            target = model.objects.get(pk=target_pk)
        except model.DoesNotExist as exc:
            raise ValidationError({"link": _("Choose a valid record.")}) from exc
        if field != "planting" and target.is_archived and (field, target_pk) not in previous_links:
            raise ValidationError({"link": _("Choose an active record for a new link.")})
        resolved[(field, target_pk)] = target
    return resolved


def _link_key(link):
    for field in LINK_MODELS:
        value = getattr(link, f"{field}_id")
        if value is not None:
            return field, value
    return None


def _write_links(task, resolved):
    task.links.all().delete()
    for (field, _target_pk), target in resolved.items():
        link = TaskLink(task=task, **{field: target})
        link.full_clean()
        link.save()


def _task_values(planned_date, title, note):
    if not isinstance(title, str) or not isinstance(note, str):
        raise ValidationError(_("Enter text, or leave this field blank."))
    title = title.strip()
    note = note.strip()
    if not title and not note:
        raise ValidationError(_("Enter a title or a note for this task."))
    try:
        title = PlannedTask._meta.get_field("title").clean(title, None)
    except ValidationError as exc:
        raise ValidationError({"title": exc}) from exc
    return {
        "planned_date": _date_value(planned_date, "planned_date"),
        "title": title,
        "note": note,
    }


def _save_task(*, actor, action, pk, values, links, submission_key, expected_updated_at):
    """One receipted writer for create, edit, complete and reopen."""
    _outside_transaction(actor)
    if pk is not None:
        pk = _positive_id(pk)
        receipts.version_token(expected_updated_at)
    normalized_links = _normalize_links(links) if links is not None else None
    key = receipts.submission_key(submission_key)
    fingerprint = _fingerprint("task", action, pk, expected_updated_at, values)
    with transaction.atomic(), validated_write():
        _active_actor(actor)
        retry = _receipt(key, actor, "task", fingerprint)
        if retry is not None:
            return TaskResult(retry, replayed=True)
        task = (
            _current(
                PlannedTask,
                pk,
                expected_updated_at,
                missing=_("This task no longer exists. Reload the calendar."),
                changed=_(
                    "This task changed while the form was open. Reload it before saving again."
                ),
            )
            if pk is not None
            else PlannedTask(submission_key=key)
        )
        previous_links = {_link_key(link) for link in task.links.all()} if pk is not None else set()
        for field, value in values.items():
            setattr(task, field, value)
        task.full_clean()
        task.save()
        if normalized_links is not None:
            _write_links(task, _resolve_links(normalized_links, previous_links))
        receipt = PlannerSubmission(
            key=key,
            author=actor,
            kind="task",
            payload_sha256=fingerprint,
            task=task,
        )
        receipt.full_clean()
        receipt.save()
        return TaskResult(task)


def create_task(*, actor, planned_date, title="", note="", links=(), submission_key=None):
    values = _task_values(planned_date, title, note)
    return _save_task(
        actor=actor,
        action="create",
        pk=None,
        values=values,
        links=links,
        submission_key=submission_key,
        expected_updated_at=None,
    )


def edit_task(
    *,
    actor,
    pk,
    planned_date,
    title="",
    note="",
    links=(),
    submission_key=None,
    expected_updated_at,
):
    values = _task_values(planned_date, title, note)
    return _save_task(
        actor=actor,
        action="edit",
        pk=pk,
        values=values,
        links=links,
        submission_key=submission_key,
        expected_updated_at=expected_updated_at,
    )


def complete_task(*, actor, pk, completed_on=None, submission_key=None, expected_updated_at):
    """Tick a task off; the completion day is the date it then appears on."""
    values = {"completed_on": _date_value(completed_on or timezone.localdate(), "completed_on")}
    return _save_task(
        actor=actor,
        action="complete",
        pk=pk,
        values=values,
        links=None,
        submission_key=submission_key,
        expected_updated_at=expected_updated_at,
    )


def reopen_task(*, actor, pk, submission_key=None, expected_updated_at):
    """Undo a completion, returning the task to its planned day."""
    values = {"completed_on": None}
    return _save_task(
        actor=actor,
        action="reopen",
        pk=pk,
        values=values,
        links=None,
        submission_key=submission_key,
        expected_updated_at=expected_updated_at,
    )


def delete_task(*, actor, pk, expected_updated_at):
    _outside_transaction(actor)
    version = receipts.version_token(expected_updated_at)
    with transaction.atomic(), validated_write():
        _active_actor(actor)
        task = _current(
            PlannedTask,
            _positive_id(pk, _("Choose a valid task.")),
            version,
            missing=_("This task no longer exists. Reload the calendar."),
            changed=_("This task changed while the form was open. Reload it before saving again."),
        )
        task.delete()


def save_month_note(*, actor, year, month, body, submission_key=None, expected_updated_at=None):
    """Create or replace the one note for a month, with a stale-edit guard."""
    _outside_transaction(actor)
    if type(year) is not int or not 1 <= year <= 9999:
        raise ValidationError({"year": _("Choose a valid month.")})
    if type(month) is not int or not 1 <= month <= 12:
        raise ValidationError({"month": _("Choose a valid month.")})
    if not isinstance(body, str):
        raise ValidationError(_("Enter text, or leave this field blank."))
    body = body.strip()
    if expected_updated_at is not None:
        receipts.version_token(expected_updated_at)
    values = {"year": year, "month": month, "body": body}
    action = "edit" if expected_updated_at is not None else "create"
    key = receipts.submission_key(submission_key)
    fingerprint = _fingerprint("note", action, None, expected_updated_at, values)
    with transaction.atomic(), validated_write():
        _active_actor(actor)
        retry = _receipt(key, actor, "note", fingerprint)
        if retry is not None:
            return NoteResult(retry, replayed=True)
        note = MonthNote.objects.filter(year=year, month=month).first()
        if expected_updated_at is None:
            # A first save has no version; a note that appeared meanwhile still wins.
            if note is not None:
                raise PlannerConflict(
                    _("This note changed while the form was open. Reload it before saving again.")
                )
        elif note is None:
            raise PlannerConflict(_("This note no longer exists. Reload the calendar."))
        elif note.updated_at != expected_updated_at:
            raise PlannerConflict(
                _("This note changed while the form was open. Reload it before saving again.")
            )
        if note is None:
            note = MonthNote(year=year, month=month)
        note.body = body
        note.full_clean()
        note.save()
        receipt = PlannerSubmission(
            key=key,
            author=actor,
            kind="note",
            payload_sha256=fingerprint,
            note=note,
        )
        receipt.full_clean()
        receipt.save()
        return NoteResult(note)
