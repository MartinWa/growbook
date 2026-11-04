"""Version checks apply to every public garden edit/delete writer and form."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.urls import reverse
from django.utils import timezone

from core.media import private_path
from core.models import Photo, User
from garden import services
from garden.models import (
    Crop,
    GrowingArea,
    Planting,
    PlantingEvent,
    PlantingSubmission,
    RegisterSubmission,
    Variety,
)

pytestmark = pytest.mark.django_db(transaction=True)

RECEIPTED_KINDS = ["crop", "variety", "area", "planting", "event"]


@pytest.fixture(params=[*RECEIPTED_KINDS, "area_photo", "variety_photo", "planting_photo"])
def target(request, user, image_upload):
    kind = request.param.removesuffix("_photo")
    crop = services.create_crop(actor=user, name="Synthetic crop").record
    if kind == "crop":
        owner = crop
        edit, delete = services.edit_crop, services.delete_crop
        values = {"name": "Edited crop", "notes": "Synthetic edit"}
    elif kind == "variety":
        owner = services.create_variety(
            actor=user, crop=crop, name="Synthetic variety", uploads=[image_upload()]
        ).record
        edit, delete = services.edit_variety, services.delete_variety
        values = {"crop": crop.pk, "name": "Edited variety"}
    elif kind == "area":
        owner = services.create_area(
            actor=user, name="Synthetic area", uploads=[image_upload()]
        ).record
        edit, delete = services.edit_area, services.delete_area
        values = {"name": "Edited area"}
    else:
        owner = services.create_planting(
            actor=user, crop=crop, start_year=2026, uploads=[image_upload()]
        ).record
        edit, delete = services.edit_planting, services.delete_planting
        values = {
            "crop": crop.pk,
            "start_year": 2026,
            "lifespan": "annual",
            "label": "Edited batch",
        }
    record = owner
    if kind == "event":
        record = services.create_planting_event(
            actor=user, planting=owner, kind="sowing", actual_date=date(2026, 3, 1)
        ).record
        owner.refresh_from_db()
        edit, delete = services.edit_planting_event, services.delete_planting_event
        values = {
            "planting": owner.pk,
            "kind": "sowing",
            "actual_date": date(2026, 3, 2),
            "note": "Edited event",
        }
    if request.param.endswith("_photo"):
        record = owner.photos.get()
        edit, delete = services.edit_register_photo, services.delete_register_photo
        values = {"caption": "Edited photo", "position": 2}
        urls = {
            action: reverse(f"garden:photo-{action}", args=[record.pk])
            for action in ("edit", "delete")
        }
    elif kind == "event":
        urls = {
            action: reverse(f"garden:event-{action}", args=[owner.pk, record.pk])
            for action in ("edit", "delete")
        }
    else:
        urls = {
            action: reverse(f"garden:{kind}-{action}", args=[record.pk])
            for action in ("edit", "delete")
        }
    return SimpleNamespace(
        record=record,
        owner=owner,
        version=owner.updated_at if isinstance(record, Photo) else record.updated_at,
        edit=edit,
        delete=delete,
        values={"pk": record.pk, **values},
        urls=urls,
    )


def garden_state():
    state = {
        model.__name__: list(model.objects.order_by("pk").values())
        for model in (
            Crop,
            Variety,
            GrowingArea,
            Planting,
            PlantingEvent,
            Photo,
            RegisterSubmission,
            PlantingSubmission,
        )
    }
    state["files"] = {
        photo[field]: private_path(photo[field]).read_bytes()
        for photo in state["Photo"]
        for field in ("original", "display", "thumbnail")
    }
    return state


@pytest.mark.parametrize("operation", ["edit", "delete"])
@pytest.mark.parametrize(
    "token", [pytest.param(..., id="omitted"), None, "not-a-version", datetime(2026, 5, 9)]
)
def test_public_writers_reject_missing_null_malformed_and_naive_versions(
    user, target, operation, token
):
    before = garden_state()
    values = target.values if operation == "edit" else {"pk": target.record.pk}
    if token is not ...:
        values = {**values, "expected_updated_at": token}
    error = TypeError if token is ... else ValidationError
    with pytest.raises(error, match="expected_updated_at" if token is ... else None):
        getattr(target, operation)(actor=user, **values)
    assert garden_state() == before


def test_stale_edits_and_deletes_preserve_the_newer_write(user, other_user, target):
    target.edit(actor=other_user, **target.values, expected_updated_at=target.version)
    saved = garden_state()
    for operation in ("edit", "delete"):
        values = target.values if operation == "edit" else {"pk": target.record.pk}
        with pytest.raises(services.RegisterConflict):
            getattr(target, operation)(actor=user, **values, expected_updated_at=target.version)
        assert garden_state() == saved


@pytest.mark.parametrize("operation", ["edit", "delete"])
@pytest.mark.parametrize("hx", [False, True])
def test_forms_reject_invalid_versions_and_accept_the_version_read(
    client, user, target, operation, hx
):
    client.force_login(user)
    headers = {"HTTP_HX_REQUEST": "true"} if hx else {}
    values = target.values if operation == "edit" else {"confirm": "on"}
    data = {
        field: value.isoformat() if isinstance(value, date) else value
        for field, value in values.items()
        if field not in {"pk", "planting"}
    }
    data["submission_key"] = str(uuid4())
    before = garden_state()
    naive = timezone.localtime(target.version).replace(tzinfo=None).isoformat()
    for token in (None, "", "null", "not-a-version", naive):
        submitted = data if token is None else {**data, "expected_updated_at": token}
        response = client.post(target.urls[operation], submitted, **headers)
        assert response.status_code == 422
        form = response.context["form"]
        assert form.errors
        if token:
            assert "expected_updated_at" in form.errors
        if not hx:
            assert form["expected_updated_at"].value() == token
        assert garden_state() == before
    response = client.post(
        target.urls[operation],
        {**data, "expected_updated_at": target.version.isoformat()},
        **headers,
    )
    assert response.status_code == (
        204 if hx and operation == "edit" and not isinstance(target.record, Photo) else 302
    )
    assert garden_state() != before


@pytest.mark.parametrize("target", RECEIPTED_KINDS, indirect=True)
def test_edit_receipts_replay_the_original_version_and_reject_changed_payloads(
    user, other_user, target
):
    payload = {
        **target.values,
        "actor": user,
        "submission_key": uuid4(),
        "expected_updated_at": target.version,
    }
    first = target.edit(**payload)
    field = next(name for name in ("name", "label", "note") if name in target.values)
    latest = target.edit(
        **{
            **payload,
            "submission_key": uuid4(),
            "expected_updated_at": first.record.updated_at,
            field: "Later edit",
        }
    )
    saved = garden_state()
    replay = target.edit(**payload)
    assert replay.replayed and replay.record.pk == latest.record.pk
    assert garden_state() == saved
    for change in (
        {field: "Changed retry"},
        {"expected_updated_at": latest.record.updated_at},
        {"actor": other_user},
    ):
        with pytest.raises(services.RegisterConflict):
            target.edit(**{**payload, **change})
        assert garden_state() == saved
    target.delete(actor=user, pk=latest.record.pk, expected_updated_at=latest.record.updated_at)
    deleted = garden_state()
    with pytest.raises(services.RegisterConflict):
        target.edit(**payload)
    assert garden_state() == deleted


def worker(callback):
    close_old_connections()
    try:
        return callback()
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("first_operation", "second_operation"),
    [("edit", "edit"), ("edit", "delete"), ("delete", "edit")],
)
def test_waiting_writer_rechecks_the_version_after_commit(
    user, target, monkeypatch, first_operation, second_operation
):
    locked, release, started = threading.Event(), threading.Event(), threading.Event()
    current = services._current

    def paused_current(model, pk, expected_updated_at=None):
        record = current(model, pk, expected_updated_at)
        if model is type(target.record) and pk == target.record.pk and not locked.is_set():
            assert connection.in_atomic_block
            locked.set()
            assert release.wait(timeout=10)
        return record

    monkeypatch.setattr(services, "_current", paused_current)

    def invoke(operation):
        values = target.values if operation == "edit" else {"pk": target.record.pk}
        return getattr(target, operation)(
            actor=User.objects.get(pk=user.pk), **values, expected_updated_at=target.version
        )

    def waiting():
        started.set()
        with pytest.raises(services.RegisterConflict):
            invoke(second_operation)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(worker, lambda: invoke(first_operation))
        try:
            assert locked.wait(timeout=10)
            second = executor.submit(worker, waiting)
            assert started.wait(timeout=10) and not second.done()
        finally:
            release.set()
        first.result(timeout=15)
        second.result(timeout=15)
    if first_operation == "delete":
        assert not type(target.record).objects.filter(pk=target.record.pk).exists()
    else:
        versioned = target.owner if isinstance(target.record, Photo) else target.record
        versioned.refresh_from_db()
        assert versioned.updated_at > target.version


@pytest.mark.parametrize("target", RECEIPTED_KINDS, indirect=True)
def test_overlapping_matching_edit_retries_save_once(user, target):
    key, start = uuid4(), threading.Barrier(2)
    before = garden_state()

    def save():
        start.wait(timeout=10)
        return target.edit(
            actor=User.objects.get(pk=user.pk),
            **target.values,
            expected_updated_at=target.version,
            submission_key=key,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker, save) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert results[0].record.pk == results[1].record.pk == target.record.pk
    assert sorted(result.replayed for result in results) == [False, True]
    after = garden_state()
    assert after["Photo"] == before["Photo"] and after["files"] == before["files"]
    assert len(after["RegisterSubmission"]) + len(after["PlantingSubmission"]) == (
        len(before["RegisterSubmission"]) + len(before["PlantingSubmission"]) + 1
    )
