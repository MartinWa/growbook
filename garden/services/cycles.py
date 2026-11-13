"""Serialized shared-cycle lifecycle and category writes with immutable retries."""

from dataclasses import dataclass
from datetime import date as Date
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from core.writes import validated_write

from ..models import (
    CycleSubmission,
    Planting,
    PlantingCategory,
    PlantingCategoryLink,
    PlantingCycle,
)
from ._shared import (
    RegisterConflict,
    _active_actor,
    _current,
    _identifier,
    _key,
    _outside_transaction,
    _planting_fingerprint,
    _version,
)


@dataclass(frozen=True)
class CycleResult:
    record: PlantingCycle | PlantingCategory
    replayed: bool = False


def normalize_category_ids(values):
    return sorted({_identifier(value, "category_ids", model=PlantingCategory) for value in values})


def cycle_id(value):
    try:
        return UUID(str(getattr(value, "pk", value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError(_("Choose a valid planting cycle.")) from exc


def _receipt(key, actor, kind, fingerprint):
    receipt = CycleSubmission.objects.filter(pk=key).first()
    if receipt is None:
        return None
    if (
        receipt.author_id != actor.pk
        or receipt.kind != kind
        or receipt.payload_sha256 != fingerprint
    ):
        raise RegisterConflict(
            _("This save key was already used with different content. Nothing was overwritten.")
        )
    record = getattr(receipt, kind)
    if record is None:
        raise RegisterConflict(_("This record was deleted. It was not recreated."))
    return CycleResult(record, True)


def _remember(key, actor, kind, fingerprint, record):
    receipt = CycleSubmission(
        key=key, author=actor, kind=kind, payload_sha256=fingerprint, **{kind: record}
    )
    receipt.full_clean()
    receipt.save()


def validate_lifecycle(cycle, *, require_dates=False):
    errors = {}
    for field in ("started_on", "completed_on"):
        value = getattr(cycle, field)
        if value is not None and type(value) is not Date:
            errors[field] = _("Enter a valid date.")
    if errors:
        raise ValidationError(errors)
    if cycle.status == PlantingCycle.Status.PLANNED and (cycle.started_on or cycle.completed_on):
        errors["status"] = _("A planned cycle has no actual start or completion date.")
    if cycle.status != PlantingCycle.Status.COMPLETED and cycle.completed_on:
        errors["completed_on"] = _("Only a completed cycle has a completion date.")
    if cycle.started_on and cycle.completed_on and cycle.completed_on < cycle.started_on:
        errors["completed_on"] = _("The completion date cannot precede the actual start date.")
    if require_dates:
        if cycle.status == PlantingCycle.Status.ONGOING and not cycle.started_on:
            errors["started_on"] = _("Set the actual start date for an ongoing cycle.")
        if cycle.status == PlantingCycle.Status.COMPLETED and not cycle.completed_on:
            errors["completed_on"] = _("Set the completion date for a completed cycle.")
    if errors:
        raise ValidationError(errors)
    cycle.full_clean()


def set_categories(cycle, ids):
    if PlantingCategory.objects.filter(pk__in=ids).count() != len(ids):
        raise ValidationError(
            {"category_ids": _("A selected category no longer exists. Reload the form.")}
        )
    PlantingCategoryLink.objects.filter(cycle=cycle).exclude(category_id__in=ids).delete()
    existing = set(cycle.categories.values_list("pk", flat=True))
    for pk in set(ids) - existing:
        link = PlantingCategoryLink(cycle=cycle, category_id=pk)
        link.full_clean()
        link.save()


def validate_member_years(member, cycle):
    if (
        cycle.status == PlantingCycle.Status.COMPLETED
        and cycle.completed_on
        and member.lifespan == Planting.Lifespan.PERSISTENT
        and member.end_year != cycle.completed_on.year
    ):
        raise ValidationError(
            {
                "end_year": _(
                    "The end year must match this cycle's completion date. "
                    "Correct the cycle through Edit cycle."
                )
            }
        )


def edit_cycle(
    *,
    actor,
    pk,
    status,
    started_on=None,
    completed_on=None,
    category_ids=(),
    expected_updated_at,
    submission_key=None,
):
    _outside_transaction(actor)
    _version(expected_updated_at)
    pk = cycle_id(pk)
    ids = normalize_category_ids(category_ids)
    key = _key(submission_key)
    values = {
        "status": status,
        "started_on": started_on,
        "completed_on": completed_on,
        "category_ids": ids,
    }
    fingerprint = _planting_fingerprint("cycle", str(pk), expected_updated_at, values)
    with transaction.atomic(), validated_write():
        _active_actor(actor)
        if retry := _receipt(key, actor, "cycle", fingerprint):
            return retry
        cycle = _current(PlantingCycle, pk, expected_updated_at)
        previous = cycle.status
        cycle.status, cycle.started_on, cycle.completed_on = status, started_on, completed_on
        validate_lifecycle(cycle, require_dates=status != previous)
        members = list(cycle.members.all())
        for member in members:
            if member.lifespan == Planting.Lifespan.PERSISTENT:
                previous_end_year = member.end_year
                if status == PlantingCycle.Status.COMPLETED and completed_on:
                    member.end_year = completed_on.year
                elif previous == PlantingCycle.Status.COMPLETED and status != previous:
                    member.end_year = None
                member.full_clean()
                if member.end_year != previous_end_year:
                    member.save()
        set_categories(cycle, ids)
        cycle.save()
        _remember(key, actor, "cycle", fingerprint, cycle)
        return CycleResult(cycle)


def transition_cycle(*, actor, pk, action, date, expected_updated_at, submission_key=None):
    _outside_transaction(actor)
    _version(expected_updated_at)
    pk = cycle_id(pk)
    if action not in ("start", "complete") or type(date) is not Date:
        raise ValidationError(_("Choose a valid action and date."))
    key = _key(submission_key)
    fingerprint = _planting_fingerprint(
        "cycle", str(pk), expected_updated_at, {"action": action, "date": date}
    )
    with transaction.atomic(), validated_write():
        _active_actor(actor)
        if retry := _receipt(key, actor, "cycle", fingerprint):
            return retry
        cycle = _current(PlantingCycle, pk, expected_updated_at)
        expected = (
            PlantingCycle.Status.PLANNED if action == "start" else PlantingCycle.Status.ONGOING
        )
        if cycle.status != expected:
            raise RegisterConflict(_("This action is no longer available. Reload the cycle."))
        if action == "start":
            cycle.status, cycle.started_on = PlantingCycle.Status.ONGOING, date
        else:
            cycle.status, cycle.completed_on = PlantingCycle.Status.COMPLETED, date
        validate_lifecycle(cycle, require_dates=True)
        if action == "complete":
            for member in cycle.members.filter(lifespan=Planting.Lifespan.PERSISTENT):
                member.end_year = date.year
                member.full_clean()
                member.save()
        cycle.save()
        _remember(key, actor, "cycle", fingerprint, cycle)
        return CycleResult(cycle)


def create_planting_category(*, actor, name, submission_key=None):
    return _save_planting_category(actor=actor, name=name, submission_key=submission_key)


def edit_planting_category(*, actor, pk, name, expected_updated_at, submission_key=None):
    _outside_transaction(actor)
    pk = _identifier(pk, "id")
    _version(expected_updated_at)
    return _save_planting_category(
        actor=actor,
        pk=pk,
        name=name,
        expected_updated_at=expected_updated_at,
        submission_key=submission_key,
    )


def _save_planting_category(*, actor, name, pk=None, expected_updated_at=None, submission_key=None):
    _outside_transaction(actor)
    if pk is not None:
        pk = _identifier(pk, "id")
        _version(expected_updated_at)
    key = _key(submission_key)
    fingerprint = _planting_fingerprint("category", pk, expected_updated_at, {"name": name})
    try:
        with transaction.atomic(), validated_write():
            _active_actor(actor)
            if retry := _receipt(key, actor, "category", fingerprint):
                return retry
            category = (
                _current(PlantingCategory, pk, expected_updated_at) if pk else PlantingCategory()
            )
            category.name = name
            category.full_clean()
            category.save()
            _remember(key, actor, "category", fingerprint, category)
            return CycleResult(category)
    except IntegrityError as exc:
        if "garden_plantingcategory.name_key" in str(exc):
            raise ValidationError({"name": _("A category with this name already exists.")}) from exc
        raise


def delete_planting_category(*, actor, pk, expected_updated_at):
    _outside_transaction(actor)
    _version(expected_updated_at)
    pk = _identifier(pk, "id")
    with transaction.atomic(), validated_write():
        _active_actor(actor)
        category = _current(PlantingCategory, pk, expected_updated_at)
        for cycle in category.cycles.all():
            cycle.save()
        category.delete()
