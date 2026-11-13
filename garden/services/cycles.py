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
    evaluations=None,
    expected_member_versions=None,
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
    evaluations, versions = normalize_evaluations(evaluations, expected_member_versions)
    values.update(evaluation_payload(evaluations, versions))
    fingerprint = _planting_fingerprint("cycle", str(pk), expected_updated_at, values)
    with transaction.atomic(), validated_write():
        _active_actor(actor)
        if retry := _receipt(key, actor, "cycle", fingerprint):
            return retry
        cycle = write_cycle_edit(
            pk,
            expected_updated_at,
            status,
            started_on,
            completed_on,
            ids,
            evaluations=evaluations,
            versions=versions,
        )
        _remember(key, actor, "cycle", fingerprint, cycle)
        return CycleResult(cycle)


def transition_cycle(
    *,
    actor,
    pk,
    action,
    date,
    expected_updated_at,
    submission_key=None,
    evaluations=None,
    expected_member_versions=None,
):
    _outside_transaction(actor)
    _version(expected_updated_at)
    pk = cycle_id(pk)
    if action not in ("start", "complete") or type(date) is not Date:
        raise ValidationError(_("Choose a valid action and date."))
    key = _key(submission_key)
    evaluations, versions = normalize_evaluations(evaluations, expected_member_versions)
    fingerprint = _planting_fingerprint(
        "cycle",
        str(pk),
        expected_updated_at,
        {"action": action, "date": date, **evaluation_payload(evaluations, versions)},
    )
    with transaction.atomic(), validated_write():
        _active_actor(actor)
        if retry := _receipt(key, actor, "cycle", fingerprint):
            return retry
        cycle = write_cycle_transition(
            pk, expected_updated_at, action, date, evaluations=evaluations, versions=versions
        )
        _remember(key, actor, "cycle", fingerprint, cycle)
        return CycleResult(cycle)


def normalize_evaluations(evaluations, expected_member_versions):
    """Normalize submitted text and original version tokens without reading mutable state."""
    if evaluations is None:
        if expected_member_versions is not None:
            raise ValidationError(_("Reload the cycle evaluations before saving."))
        return None, None
    if not isinstance(evaluations, dict) or not isinstance(expected_member_versions, dict):
        raise ValidationError(_("Reload the cycle evaluations before saving."))
    text, versions = {}, {}
    for pk, value in evaluations.items():
        pk = _identifier(pk, "evaluations")
        if not isinstance(value, str):
            raise ValidationError(_("Enter text, or leave this field blank."))
        text[pk] = value.strip()
    for pk, version in expected_member_versions.items():
        pk = _identifier(pk, "evaluations")
        _version(version)
        versions[pk] = version
    if not text.keys() <= versions.keys():
        raise ValidationError(_("Reload the cycle evaluations before saving."))
    return text, versions


def evaluation_payload(evaluations, versions):
    # Omitted evaluations keep pre-existing immutable receipts compatible.
    if evaluations is None:
        return {}
    return {
        "evaluations": {str(pk): value for pk, value in sorted(evaluations.items())},
        "member_versions": {str(pk): value.isoformat() for pk, value in sorted(versions.items())},
    }


def _write_members(cycle, previous, evaluations, versions):
    members = list(cycle.members.all())
    if versions is not None:
        if {member.pk for member in members} != set(versions):
            raise RegisterConflict(_("The cycle membership changed. Reload the form."))
        for member in members:
            if member.updated_at != versions[member.pk]:
                raise RegisterConflict(_("A cycle member changed. Reload the evaluations."))
    if evaluations and cycle.status != PlantingCycle.Status.COMPLETED:
        raise ValidationError(_("Evaluation is offered when completing a cycle."))
    for member in members:
        before = member.end_year, member.evaluation
        if member.lifespan == Planting.Lifespan.PERSISTENT:
            if cycle.status == PlantingCycle.Status.COMPLETED and cycle.completed_on:
                member.end_year = cycle.completed_on.year
            elif previous == PlantingCycle.Status.COMPLETED and cycle.status != previous:
                member.end_year = None
        if evaluations is not None and member.pk in evaluations:
            member.evaluation = evaluations[member.pk]
        member.full_clean()
        if before != (member.end_year, member.evaluation) or (
            evaluations is not None and member.pk in evaluations
        ):
            member.save()


def write_cycle_edit(
    pk,
    version,
    status,
    started_on,
    completed_on,
    category_ids=None,
    *,
    evaluations=None,
    versions=None,
):
    """Apply validated changes inside the caller's serialized transaction."""
    cycle = _current(PlantingCycle, pk, version)
    previous = cycle.status
    cycle.status, cycle.started_on, cycle.completed_on = status, started_on, completed_on
    validate_lifecycle(cycle, require_dates=status != previous)
    _write_members(cycle, previous, evaluations, versions)
    if category_ids is not None:
        set_categories(cycle, category_ids)
    cycle.save()
    return cycle


def write_cycle_transition(pk, version, action, date, *, evaluations=None, versions=None):
    cycle = _current(PlantingCycle, pk, version)
    expected = PlantingCycle.Status.PLANNED if action == "start" else PlantingCycle.Status.ONGOING
    if action not in ("start", "complete") or type(date) is not Date:
        raise ValidationError(_("Choose a valid action and date."))
    if cycle.status != expected:
        raise RegisterConflict(_("This action is no longer available. Reload the cycle."))
    status = PlantingCycle.Status.ONGOING if action == "start" else PlantingCycle.Status.COMPLETED
    return write_cycle_edit(
        pk,
        version,
        status,
        date if action == "start" else cycle.started_on,
        date if action == "complete" else None,
        evaluations=evaluations,
        versions=versions,
    )


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
