"""Typed capture actions, normalized before receipts and executed only on creation."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from garden.models import Planting, PlantingCycle, Variety
from garden.services._shared import _current, _identifier, _version
from garden.services.cycles import (
    evaluation_payload,
    normalize_category_ids,
    normalize_evaluations,
    write_cycle_edit,
)
from garden.services.events import write_event
from garden.services.plantings import _planting_values, write_planting


@dataclass(frozen=True, kw_only=True)
class NewPlantingAction:
    crop: int | None = None
    variety: int | None = None
    area: int | None = None
    start_year: int | None = None
    lifespan: str = Planting.Lifespan.ANNUAL
    end_year: int | None = None
    label: str = ""
    quantity: int | None = None
    notes: str = ""
    evaluation: str = ""
    status: str = PlantingCycle.Status.ONGOING
    started_on: date | None | Literal["diary"] = "diary"
    completed_on: date | None = None
    category_ids: tuple[int, ...] = ()


@dataclass(frozen=True, kw_only=True)
class PlantingEventAction:
    planting: int
    kind: str
    planned_date: date | None = None
    actual_date: date | None | Literal["diary"] = "diary"
    note: str = ""


@dataclass(frozen=True, kw_only=True)
class CycleStatusAction:
    planting: int
    expected_updated_at: datetime
    status: str
    started_on: date | None = None
    completed_on: date | None = None
    category_ids: tuple[int, ...] | None = None
    evaluations: dict[int, str] | None = None
    expected_member_versions: dict[int, datetime] | None = None


CaptureAction = NewPlantingAction | PlantingEventAction | CycleStatusAction


def normalize_action(action, observed):
    """Use submitted IDs and versions, without mutable-state checks before receipt replay."""
    if action is None:
        return None
    if isinstance(action, NewPlantingAction):
        values = _planting_values(
            action.crop if action.crop is not None else 1,
            action.start_year if action.start_year is not None else observed.year,
            action.variety,
            action.area,
            action.lifespan,
            action.end_year,
            action.label,
            action.quantity,
            action.notes,
            action.evaluation,
        )
        if action.crop is None:
            values["crop_id"] = None
        started_on = action.started_on
        if started_on == "diary":
            started_on = observed if action.status != PlantingCycle.Status.PLANNED else None
        values.update(
            status=action.status,
            started_on=started_on,
            completed_on=action.completed_on,
            category_ids=normalize_category_ids(action.category_ids),
        )
        return {"kind": "new", "values": values}
    if isinstance(action, PlantingEventAction):
        if not isinstance(action.kind, str) or not isinstance(action.note, str):
            raise ValidationError(_("Enter text, or leave this field blank."))
        return {
            "kind": "event",
            "values": {
                "planting_id": _identifier(action.planting, "planting", model=Planting),
                "kind": action.kind,
                "planned_date": action.planned_date,
                "actual_date": observed if action.actual_date == "diary" else action.actual_date,
                "note": action.note.strip(),
            },
        }
    if isinstance(action, CycleStatusAction):
        _version(action.expected_updated_at)
        evaluations, versions = normalize_evaluations(
            action.evaluations, action.expected_member_versions
        )
        return {
            "kind": "cycle",
            "values": {
                "planting_id": _identifier(action.planting, "planting", model=Planting),
                "expected_updated_at": action.expected_updated_at,
                "status": action.status,
                "started_on": action.started_on,
                "completed_on": action.completed_on,
                "category_ids": None
                if action.category_ids is None
                else normalize_category_ids(action.category_ids),
                **evaluation_payload(evaluations, versions),
            },
            "evaluations": evaluations,
            "versions": versions,
        }
    raise ValidationError(_("Choose a valid planting action."))


def action_payload(action, photos):
    if action is None:
        return None
    values = {
        key: value.isoformat() if isinstance(value, (date, datetime)) else value
        for key, value in action["values"].items()
    }
    return {"kind": action["kind"], "values": values, "photos": [photo.sha256 for photo in photos]}


def execute_action(action, rows):
    """Caller owns the transaction, active-actor check, media lock and diary receipt."""
    values = action["values"].copy()
    for field in ("started_on", "completed_on", "planned_date", "actual_date"):
        value = values.get(field)
        if value is not None and type(value) is not date:
            raise ValidationError({field: _("Enter a valid date.")})
    if action["kind"] == "new":
        if values["crop_id"] is None:
            variety = _current(Variety, values["variety_id"]) if values["variety_id"] else None
            if variety is None:
                raise ValidationError({"crop": _("Choose a crop or a variety.")})
            values["crop_id"] = variety.crop_id
        return write_planting(None, values, rows)
    planting = _current(Planting, values.pop("planting_id"))
    if action["kind"] == "event":
        write_event({"planting_id": planting.pk, **values})
    else:
        write_cycle_edit(
            planting.cycle_id,
            values["expected_updated_at"],
            values["status"],
            values["started_on"],
            values["completed_on"],
            values["category_ids"],
            evaluations=action["evaluations"],
            versions=action["versions"],
        )
    return planting
