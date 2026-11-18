"""Explicit economy writes with serialized budget validation and immutable receipts."""

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID, uuid4

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import User
from core.writes import validated_write
from garden.models import Crop, PlantingCycle, Variety
from garden.queries import plantings_in_year

from .calculations import allocation_budget, equipment_schedule
from .models import Allocation, AllocationCycle, EconomySubmission, Expense, Sale
from .money import MAX_CENTS, decimal_value, rounded_cents, to_cents


class EconomyConflict(ValidationError):
    pass


@dataclass(frozen=True)
class EconomyResult:
    record: Expense | Allocation | Sale
    replayed: bool = False


def _outside_transaction(actor):
    if connection.in_atomic_block:
        raise RuntimeError("Call economy operations outside any existing transaction.")
    if not actor.is_authenticated or not actor.is_active or not actor.pk:
        raise PermissionDenied


def _version(value):
    if not isinstance(value, datetime) or timezone.is_naive(value):
        raise ValidationError(_("Reload the edit form before saving."))


def _id(value, field, *, optional=False, cycle=False):
    value = getattr(value, "pk", value)
    if value is None and optional:
        return None
    if cycle:
        try:
            return UUID(str(value))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValidationError({field: _("Choose a valid record.")}) from exc
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        raise ValidationError({field: _("Choose a valid record.")})
    return value


def _text(value, field):
    if not isinstance(value, str):
        raise ValidationError({field: _("Enter text, or leave this field blank.")})
    return value.strip()


def _date(value, field):
    if value is not None and (not isinstance(value, date) or isinstance(value, datetime)):
        raise ValidationError({field: _("Enter a valid date.")})
    return value


def _year(value):
    if type(value) is not int or not 1 <= value <= 9999:
        raise ValidationError({"growing_year": _("Enter a growing year from 1 to 9999.")})
    return value


def _canonical(value):
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (date, UUID)):
        return str(value)
    from decimal import Decimal

    if isinstance(value, Decimal):
        return str(value.normalize())
    return value


def _current(model, pk, expected_updated_at):
    try:
        record = model.objects.get(pk=pk)
    except model.DoesNotExist as exc:
        raise EconomyConflict(_("This record no longer exists. Reload Economy.")) from exc
    if record.updated_at != expected_updated_at:
        raise EconomyConflict(
            _("This record changed while the form was open. Reload it before saving again.")
        )
    return record


def _save(actor, kind, pk, values, submission_key, expected_updated_at, writer):
    _outside_transaction(actor)
    if pk is not None:
        pk = _id(pk, "id")
        _version(expected_updated_at)
    try:
        key = UUID(str(submission_key)) if submission_key is not None else uuid4()
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError(_("The save key is invalid. Reload the form.")) from exc
    fingerprint = hashlib.sha256(
        json.dumps(
            _canonical({"kind": kind, "pk": pk, "version": expected_updated_at, "values": values}),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    with transaction.atomic(), validated_write():
        if not User.objects.filter(pk=actor.pk, is_active=True).exists():
            raise PermissionDenied
        receipt = EconomySubmission.objects.select_related(kind).filter(pk=key).first()
        if receipt is not None:
            if (
                receipt.author_id != actor.pk
                or receipt.kind != kind
                or receipt.payload_sha256 != fingerprint
            ):
                raise EconomyConflict(
                    _(
                        "This save key was already used with different content. "
                        "Nothing was overwritten."
                    )
                )
            record = getattr(receipt, kind)
            if record is None:
                raise EconomyConflict(_("This record was deleted. It was not recreated."))
            return EconomyResult(record, True)
        record = writer(pk, values, expected_updated_at)
        receipt = EconomySubmission(
            key=key, author=actor, kind=kind, payload_sha256=fingerprint, **{kind: record}
        )
        receipt.full_clean()
        receipt.save()
        return EconomyResult(record)


def _taxonomy(crop_id, variety_id, previous):
    if variety_id is not None:
        try:
            variety = Variety.objects.select_related("crop").get(pk=variety_id)
        except Variety.DoesNotExist as exc:
            raise ValidationError({"variety": _("Choose a valid record.")}) from exc
        if crop_id is not None and variety.crop_id != crop_id:
            raise ValidationError(
                {"variety": _("Choose a variety of the selected crop, or leave it blank.")}
            )
        crop_id = variety.crop_id
        if variety_id != previous.get("variety_id") and (
            variety.is_archived or variety.crop.is_archived
        ):
            raise ValidationError({"variety": _("Choose an active variety for a new association.")})
    if crop_id is not None:
        try:
            crop = Crop.objects.get(pk=crop_id)
        except Crop.DoesNotExist as exc:
            raise ValidationError({"crop": _("Choose a valid record.")}) from exc
        if crop.is_archived and crop_id != previous.get("crop_id"):
            raise ValidationError({"crop": _("Choose an active crop for a new association.")})
    return crop_id


def _validate_budgets(expense):
    allocations = list(expense.allocations.all()) if expense.pk else []
    if expense.useful_life_years is None:
        if sum(allocation.amount_cents for allocation in allocations) > expense.total_cents:
            raise ValidationError(
                {
                    "total_price": _(
                        "Reduce or remove allocations before lowering the purchase cost "
                        "below the reserved amount."
                    )
                }
            )
        return
    schedule = equipment_schedule(expense)
    for allocation in allocations:
        if allocation.growing_year not in schedule:
            raise ValidationError(
                {
                    "useful_life_years": _(
                        "Remove allocations outside the new equipment period before changing it."
                    )
                }
            )
    for year, budget in schedule.items():
        if (
            sum(
                allocation.amount_cents
                for allocation in allocations
                if allocation.growing_year == year
            )
            > budget
        ):
            raise ValidationError(
                _(
                    "Reduce or remove allocations before lowering an annual equipment share "
                    "below the reserved amount."
                )
            )


def _write_expense(pk, values, expected_updated_at):
    record = _current(Expense, pk, expected_updated_at) if pk is not None else None
    if record is None:
        purchased_on = values.get("purchased_on") or timezone.localdate()
        record = Expense(purchase_year=purchased_on.year, purchased_on=purchased_on)
    previous = {"crop_id": record.crop_id, "variety_id": record.variety_id}
    for field, value in values.items():
        if field == "purchased_on" and value is None:
            continue
        setattr(record, field, value)
    record.crop_id = _taxonomy(record.crop_id, record.variety_id, previous)
    record.full_clean()
    _validate_budgets(record)
    record.save()
    return record


def _expense_values(
    description,
    purchased_on,
    total_price,
    category,
    purchased_quantity,
    unit,
    useful_life_years,
    crop,
    variety,
):
    if useful_life_years is not None and (
        type(useful_life_years) is not int or not 1 <= useful_life_years <= 9999
    ):
        raise ValidationError(
            {"useful_life_years": _("Enter a useful life in whole years from 1 to 9999.")}
        )
    return {
        "description": _text(description, "description"),
        "purchased_on": _date(purchased_on, "purchased_on"),
        "total_cents": to_cents(total_price, "total_price"),
        "category": _text(category, "category"),
        "purchased_quantity": decimal_value(purchased_quantity, "purchased_quantity", places=3)
        if purchased_quantity is not None
        else None,
        "unit": _text(unit, "unit"),
        "useful_life_years": useful_life_years,
        "crop_id": _id(crop, "crop", optional=True),
        "variety_id": _id(variety, "variety", optional=True),
    }


def create_expense(
    *,
    actor,
    description,
    total_price,
    purchased_on=None,
    category=Expense.Category.OTHER,
    purchased_quantity=None,
    unit="",
    useful_life_years=None,
    crop=None,
    variety=None,
    submission_key=None,
):
    values = _expense_values(
        description,
        purchased_on,
        total_price,
        category,
        purchased_quantity,
        unit,
        useful_life_years,
        crop,
        variety,
    )
    return _save(actor, "expense", None, values, submission_key, None, _write_expense)


def edit_expense(
    *,
    actor,
    pk,
    expected_updated_at,
    description,
    total_price,
    purchased_on=None,
    category=Expense.Category.OTHER,
    purchased_quantity=None,
    unit="",
    useful_life_years=None,
    crop=None,
    variety=None,
    submission_key=None,
):
    values = _expense_values(
        description,
        purchased_on,
        total_price,
        category,
        purchased_quantity,
        unit,
        useful_life_years,
        crop,
        variety,
    )
    return _save(actor, "expense", pk, values, submission_key, expected_updated_at, _write_expense)


def _allocation_amount(expense, values):
    entries = [field for field in ("amount", "share", "quantity_used") if values[field] is not None]
    if len(entries) != 1:
        raise ValidationError(
            _("Enter exactly one allocation amount, percentage or quantity used.")
        )
    field = entries[0]
    value = values[field]
    budget = allocation_budget(expense, values["growing_year"])
    if field == "amount":
        cents = to_cents(value, field)
    elif field == "share":
        if not 0 < value <= 100:
            raise ValidationError(
                {field: _("Enter a percentage greater than 0 and no more than 100.")}
            )
        cents = rounded_cents(budget * value / 100)
    else:
        if expense.purchased_quantity is None:
            raise ValidationError(
                {
                    field: _(
                        "Record the purchased quantity and unit before allocating a quantity used."
                    )
                }
            )
        if not 0 < value <= expense.purchased_quantity:
            raise ValidationError(
                {field: _("Use a positive quantity no greater than the purchased quantity.")}
            )
        if expense.unit == Expense.Unit.ITEMS and value != value.to_integral_value():
            raise ValidationError({field: _("Items must be a whole number.")})
        cents = rounded_cents(budget * value / expense.purchased_quantity)
    if cents <= 0:
        raise ValidationError({field: _("The chosen allocation must be at least 0.01.")})
    return cents


def _write_allocation(pk, values, expected_updated_at):
    expense = _current(Expense, values["expense_id"], values["expected_expense_updated_at"])
    record = (
        _current(Allocation, pk, expected_updated_at)
        if pk is not None
        else Allocation(expense=expense)
    )
    if record.expense_id != expense.pk:
        raise ValidationError(_("An allocation must belong to this expense."))
    record.growing_year, record.scope, record.method = (
        values["growing_year"],
        values["scope"],
        values["method"],
    )
    if expense.useful_life_years is not None and record.growing_year not in equipment_schedule(
        expense
    ):
        raise ValidationError(
            {"growing_year": _("Choose a growing year within the equipment period.")}
        )
    ids = values["cycle_ids"] if record.scope == Allocation.Scope.SELECTED else []
    if record.scope == Allocation.Scope.SELECTED:
        if not ids or set(
            PlantingCycle.objects.filter(pk__in=ids).values_list("pk", flat=True)
        ) != set(ids):
            raise ValidationError({"cycle_ids": _("Choose at least one existing planting cycle.")})
        eligible = set(
            plantings_in_year(record.growing_year)
            .filter(cycle_id__in=ids)
            .values_list("cycle_id", flat=True)
        )
        if set(ids) != eligible:
            raise ValidationError(
                {"cycle_ids": _("Every selected cycle must belong to the chosen growing year.")}
            )
    record.amount_cents = _allocation_amount(expense, values)
    reserved = expense.allocations.exclude(pk=pk)
    if expense.useful_life_years is not None:
        reserved = reserved.filter(growing_year=record.growing_year)
    available = allocation_budget(expense, record.growing_year) - sum(
        reserved.values_list("amount_cents", flat=True)
    )
    if record.amount_cents > available:
        raise ValidationError(
            _(
                "This amount exceeds the available balance, "
                "including amounts reserved for pending allocations."
            )
        )
    record.full_clean()
    record.save()
    record.cycle_links.exclude(cycle_id__in=ids).delete()
    existing = set(record.cycle_links.values_list("cycle_id", flat=True))
    for cycle_id in ids:
        if cycle_id not in existing:
            link = AllocationCycle(allocation=record, cycle_id=cycle_id)
            link.full_clean()
            link.save()
    expense.save(update_fields=["updated_at"])
    return record


def _allocation_values(
    expense,
    expected_expense_updated_at,
    growing_year,
    amount,
    share,
    quantity_used,
    scope,
    method,
    cycle_ids,
):
    _version(expected_expense_updated_at)
    ids = sorted({_id(value, "cycle_ids", cycle=True) for value in cycle_ids}, key=str)
    return {
        "expense_id": _id(expense, "expense"),
        "expected_expense_updated_at": expected_expense_updated_at,
        "growing_year": _year(growing_year),
        "amount": decimal_value(amount, "amount") if amount is not None else None,
        "share": decimal_value(share, "share", places=3) if share is not None else None,
        "quantity_used": decimal_value(quantity_used, "quantity_used", places=3)
        if quantity_used is not None
        else None,
        "scope": _text(scope, "scope"),
        "method": _text(method, "method"),
        "cycle_ids": ids if scope != Allocation.Scope.YEAR else [],
    }


def create_allocation(
    *,
    actor,
    expense,
    expected_expense_updated_at,
    growing_year,
    amount=None,
    share=None,
    quantity_used=None,
    scope=Allocation.Scope.SELECTED,
    method=Allocation.Method.PLANTS,
    cycle_ids=(),
    submission_key=None,
):
    values = _allocation_values(
        expense,
        expected_expense_updated_at,
        growing_year,
        amount,
        share,
        quantity_used,
        scope,
        method,
        cycle_ids,
    )
    return _save(actor, "allocation", None, values, submission_key, None, _write_allocation)


def edit_allocation(
    *,
    actor,
    pk,
    expected_updated_at,
    expense,
    expected_expense_updated_at,
    growing_year,
    amount=None,
    share=None,
    quantity_used=None,
    scope=Allocation.Scope.SELECTED,
    method=Allocation.Method.PLANTS,
    cycle_ids=(),
    submission_key=None,
):
    values = _allocation_values(
        expense,
        expected_expense_updated_at,
        growing_year,
        amount,
        share,
        quantity_used,
        scope,
        method,
        cycle_ids,
    )
    return _save(
        actor, "allocation", pk, values, submission_key, expected_updated_at, _write_allocation
    )


def _write_sale(pk, values, expected_updated_at):
    record = (
        _current(Sale, pk, expected_updated_at)
        if pk is not None
        else Sale(sold_on=timezone.localdate())
    )
    previous_variety = record.variety_id
    for field, value in values.items():
        if field == "sold_on" and value is None:
            continue
        setattr(record, field, value)
    if record.cycle_id is not None:
        try:
            cycle = PlantingCycle.objects.prefetch_related("members").get(pk=record.cycle_id)
        except PlantingCycle.DoesNotExist as exc:
            raise ValidationError({"cycle": _("Choose a valid record.")}) from exc
        members = list(cycle.members.all())
        if not members:
            raise ValidationError({"cycle": _("Choose a cycle with a planting.")})
        if record.variety_id is None and len(members) == 1:
            record.variety_id = members[0].variety_id
        if record.variety_id is not None and record.variety_id not in {
            member.variety_id for member in members
        }:
            raise ValidationError(
                {"variety": _("Choose a variety in the selected planting cycle.")}
            )
    elif record.variety_id is not None:
        _taxonomy(
            None,
            record.variety_id,
            {
                "variety_id": previous_variety,
                "crop_id": Variety.objects.filter(pk=previous_variety)
                .values_list("crop_id", flat=True)
                .first(),
            },
        )
    record.full_clean()
    record.save()
    return record


def _sale_values(description, sold_on, quantity, unit_price, total_price, cycle, variety):
    if type(quantity) is not int or not 1 <= quantity <= 2**31 - 1:
        raise ValidationError({"quantity": _("Enter a positive whole number of plants sold.")})
    if (unit_price is None) == (total_price is None):
        raise ValidationError(_("Enter either a unit price or a total sale amount."))
    unit_cents = to_cents(unit_price, "unit_price") if unit_price is not None else None
    cents = (
        unit_cents * quantity if unit_cents is not None else to_cents(total_price, "total_price")
    )
    if cents > MAX_CENTS:
        raise ValidationError({"unit_price": _("The total sale amount is too large.")})
    return {
        "description": _text(description, "description"),
        "sold_on": _date(sold_on, "sold_on"),
        "quantity": quantity,
        "total_cents": cents,
        "unit_price_cents": unit_cents,
        "cycle_id": _id(cycle, "cycle", optional=True, cycle=True),
        "variety_id": _id(variety, "variety", optional=True),
    }


def create_sale(
    *,
    actor,
    description,
    quantity,
    sold_on=None,
    unit_price=None,
    total_price=None,
    cycle=None,
    variety=None,
    submission_key=None,
):
    values = _sale_values(description, sold_on, quantity, unit_price, total_price, cycle, variety)
    return _save(actor, "sale", None, values, submission_key, None, _write_sale)


def edit_sale(
    *,
    actor,
    pk,
    expected_updated_at,
    description,
    quantity,
    sold_on=None,
    unit_price=None,
    total_price=None,
    cycle=None,
    variety=None,
    submission_key=None,
):
    values = _sale_values(description, sold_on, quantity, unit_price, total_price, cycle, variety)
    return _save(actor, "sale", pk, values, submission_key, expected_updated_at, _write_sale)


def _delete(actor, model, pk, expected_updated_at, expected_expense_updated_at=None):
    _outside_transaction(actor)
    _version(expected_updated_at)
    if model is Allocation:
        _version(expected_expense_updated_at)
    with transaction.atomic(), validated_write():
        if not User.objects.filter(pk=actor.pk, is_active=True).exists():
            raise PermissionDenied
        record = _current(model, _id(pk, "id"), expected_updated_at)
        expense = (
            _current(Expense, record.expense_id, expected_expense_updated_at)
            if model is Allocation
            else None
        )
        record.delete()
        if expense is not None:
            expense.save(update_fields=["updated_at"])


def delete_expense(*, actor, pk, expected_updated_at):
    _delete(actor, Expense, pk, expected_updated_at)


def delete_sale(*, actor, pk, expected_updated_at):
    _delete(actor, Sale, pk, expected_updated_at)


def delete_allocation(*, actor, pk, expected_updated_at, expected_expense_updated_at):
    _delete(actor, Allocation, pk, expected_updated_at, expected_expense_updated_at)
