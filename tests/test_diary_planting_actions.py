"""Combined captures, immutable replay and cycle-wide evaluation integrity."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from threading import Barrier
from uuid import uuid4

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections
from django.urls import reverse

from core.models import Photo
from core.writes import validated_write
from diary.actions import CycleStatusAction, NewPlantingAction, PlantingEventAction
from diary.models import DiaryEntry, DiarySubmission
from diary.services import SubmissionConflict, SubmissionDeleted, create_entry, delete_entry
from garden.models import Planting, PlantingCycle, PlantingEvent
from garden.services import RegisterConflict, create_planting, edit_planting
from garden.services.cycles import edit_cycle, transition_cycle

pytestmark = pytest.mark.django_db(transaction=True)


def capture(user, action=None, **kwargs):
    return create_entry(
        author=user,
        date=kwargs.pop("date", date(2026, 1, 2)),
        body=kwargs.pop("body", "Synthetic observation"),
        uploads=kwargs.pop("uploads", ()),
        submission_key=kwargs.pop("submission_key", uuid4()),
        action=action,
        **kwargs,
    )


@pytest.fixture
def members(user, planting_catalog):
    crop, first, second, area = planting_catalog
    one = create_planting(
        actor=user,
        crop=crop,
        variety=first,
        area=area,
        start_year=2024,
        lifespan="persistent",
        evaluation="First result",
    ).record
    two = create_planting(
        actor=user,
        crop=crop,
        variety=second,
        start_year=2025,
        batch_planting=one,
        evaluation="Second result",
    ).record
    one.refresh_from_db()
    two.refresh_from_db()
    return one, two


def status_action(members, status="completed", **kwargs):
    one, two = members
    cycle = PlantingCycle.objects.get(pk=one.cycle_id)
    return CycleStatusAction(
        planting=one.pk,
        expected_updated_at=cycle.updated_at,
        status=status,
        started_on=date(2024, 3, 1),
        completed_on=date(2026, 1, 2) if status == "completed" else None,
        evaluations=kwargs.pop("evaluations", {one.pk: "Better", two.pk: ""})
        if status == "completed"
        else None,
        expected_member_versions={p.pk: Planting.objects.get(pk=p.pk).updated_at for p in members}
        if status == "completed"
        else None,
        **kwargs,
    )


@pytest.mark.parametrize("photos", [0, 1, 3])
def test_new_planting_defaults_derivation_and_separate_photos(
    user, planting_catalog, image_upload, photos
):
    crop, variety, _other, area = planting_catalog
    result = capture(
        user,
        NewPlantingAction(variety=variety.pk, area=area.pk),
        body="" if photos else "New sowing",
        uploads=[image_upload() for _ in range(photos)],
        planting_uploads=[image_upload(format="PNG")],
    )
    planting = Planting.objects.get()
    assert (
        planting.crop_id,
        planting.start_year,
        planting.cycle.status,
        planting.cycle.started_on,
    ) == (crop.pk, 2026, "ongoing", date(2026, 1, 2))
    assert result.entry.links.get().planting_id == planting.pk
    assert result.entry.photos.count() == photos
    assert planting.photos.count() == 1
    assert all(
        sum(
            getattr(photo, field) is not None
            for field in ("entry_id", "planting_id", "area_id", "variety_id", "category_id")
        )
        == 1
        for photo in Photo.objects.all()
    )


@pytest.mark.parametrize("status", PlantingCycle.Status.values)
def test_repeated_events_all_statuses_independent_dates_and_deduplicated_tag(
    user, planting_catalog, status
):
    crop = planting_catalog[0]
    planting = create_planting(
        actor=user,
        crop=crop,
        start_year=2025,
        status=status,
        started_on=date(2025, 3, 1) if status == "ongoing" else None,
        completed_on=date(2025, 9, 1) if status == "completed" else None,
    ).record
    action = PlantingEventAction(
        planting=planting.pk, kind="sowing", planned_date=date(2025, 12, 31)
    )
    for _ in range(2):
        entry = capture(user, action, links=[planting]).entry
        assert entry.links.count() == 1
    planting.cycle.refresh_from_db()
    assert planting.cycle.status == status
    assert planting.events.count() == 2
    event = planting.events.first()
    assert (event.planned_date, event.actual_date) == (date(2025, 12, 31), date(2026, 1, 2))


@pytest.mark.parametrize("target", PlantingCycle.Status.values)
@pytest.mark.parametrize("source", PlantingCycle.Status.values)
def test_all_cycle_status_corrections_preserve_years_and_associations(
    user, members, source, target
):
    one, two = members
    cycle = one.cycle
    edit_cycle(
        actor=user,
        pk=cycle.pk,
        status=source,
        started_on=date(2024, 3, 1) if source != "planned" else None,
        completed_on=date(2025, 9, 1) if source == "completed" else None,
        expected_updated_at=cycle.updated_at,
    )
    before = list(
        Planting.objects.values_list("id", "crop_id", "variety_id", "area_id", "start_year")
    )
    action = status_action(members, target)
    if target == "planned":
        action = replace(action, started_on=None)
    capture(user, action)
    assert (
        list(Planting.objects.values_list("id", "crop_id", "variety_id", "area_id", "start_year"))
        == before
    )
    one.refresh_from_db()
    two.refresh_from_db()
    assert one.cycle.status == target
    assert one.end_year == (2026 if target == "completed" else None)
    assert two.end_year is None


def test_completion_evaluations_omission_clearing_and_replay_after_later_edits(user, members):
    one, two = members
    key = uuid4()
    action = status_action(members, evaluations={one.pk: ""})
    entry = capture(user, action, submission_key=key).entry
    one.refresh_from_db()
    two.refresh_from_db()
    assert one.evaluation == "" and two.evaluation == "Second result"
    edit_planting(
        actor=user,
        pk=one.pk,
        crop=one.crop_id,
        variety=one.variety_id,
        area=one.area_id,
        start_year=one.start_year,
        lifespan=one.lifespan,
        end_year=one.end_year,
        evaluation="Later result",
        expected_updated_at=one.updated_at,
    )
    replay = capture(user, action, submission_key=key)
    assert replay.entry.pk == entry.pk and not replay.created
    one.refresh_from_db()
    assert one.evaluation == "Later result"
    with pytest.raises(SubmissionConflict):
        capture(user, replace(action, evaluations={one.pk: "Changed retry"}), submission_key=key)
    delete_entry(actor=user, pk=entry.pk, expected_updated_at=entry.updated_at)
    with pytest.raises(SubmissionDeleted):
        capture(user, action, submission_key=key)
    one.cycle.refresh_from_db()
    assert one.cycle.status == "completed"


@pytest.mark.parametrize("fault", ["evaluation", "membership", "date", "member_year"])
def test_entire_capture_rolls_back_for_stale_or_invalid_cycle(user, members, image_upload, fault):
    one, two = members
    action = status_action(members)
    if fault == "evaluation":
        with validated_write():
            two.evaluation = "Concurrent result"
            two.save()
    elif fault == "membership":
        with validated_write():
            two.delete()
    elif fault == "date":
        action = replace(action, completed_on=date(2023, 1, 1))
    else:
        action = replace(action, started_on=date(2023, 1, 1), completed_on=date(2023, 2, 1))
    with pytest.raises((RegisterConflict, ValidationError)):
        capture(user, action, uploads=[image_upload()])
    assert (
        not DiaryEntry.objects.exists()
        and not DiarySubmission.objects.exists()
        and not Photo.objects.exists()
    )
    one.cycle.refresh_from_db()
    assert one.cycle.status == "planned"


@pytest.mark.parametrize(
    "field,value", [("crop", None), ("crop", 999999), ("variety", 999999), ("area", 999999)]
)
def test_invalid_new_associations_roll_back(user, planting_catalog, field, value):
    action = replace(NewPlantingAction(crop=planting_catalog[0].pk), **{field: value})
    with pytest.raises(ValidationError):
        capture(user, action)
    assert (
        not Planting.objects.exists()
        and not PlantingCycle.objects.exists()
        and not DiaryEntry.objects.exists()
    )


def test_same_key_concurrent_capture_executes_action_once(user, planting_catalog):
    barrier = Barrier(2)
    key = uuid4()
    action = NewPlantingAction(crop=planting_catalog[0].pk)

    def write():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return capture(user, action, submission_key=key).created
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: write(), range(2)))
    assert sorted(results) == [False, True]
    assert (
        Planting.objects.count()
        == DiaryEntry.objects.count()
        == DiarySubmission.objects.count()
        == 1
    )


def test_standalone_completion_evaluations_and_original_receipt_replay(user, members):
    one, two = members
    cycle = one.cycle
    cycle = transition_cycle(
        actor=user,
        pk=cycle.pk,
        action="start",
        date=date(2024, 3, 1),
        expected_updated_at=cycle.updated_at,
    ).record
    args = dict(
        actor=user,
        pk=cycle.pk,
        action="complete",
        date=date(2026, 1, 2),
        expected_updated_at=cycle.updated_at,
        evaluations={one.pk: "One", two.pk: "Two"},
        expected_member_versions={p.pk: p.updated_at for p in members},
        submission_key=uuid4(),
    )
    transition_cycle(**args)
    assert transition_cycle(**args).replayed
    assert list(Planting.objects.order_by("id").values_list("evaluation", flat=True)) == [
        "One",
        "Two",
    ]


def test_http_new_event_cycle_and_native_choice_updates(client, user, members, planting_catalog):
    client.force_login(user)
    url = reverse("diary:capture")
    base = {
        "submission_key": str(uuid4()),
        "date": "2026-01-02",
        "body": "Native draft",
        "capture_mode": "quick",
    }
    response = client.post(
        url,
        {
            **base,
            "planting_action": "new",
            "planting-crop": planting_catalog[0].pk,
            "planting-start_year": 2026,
            "planting-status": "ongoing",
            "planting-started_on": "2026-01-02",
            "planting-lifespan": "annual",
        },
    )
    assert response.status_code == 302
    response = client.post(
        url,
        {
            **base,
            "submission_key": str(uuid4()),
            "planting_action": "event",
            "action_planting": members[0].pk,
            "event-kind": "sowing",
            "event-actual_date": "2026-01-02",
        },
    )
    assert response.status_code == 302
    loaded = client.post(
        url,
        {
            **base,
            "planting_action": "cycle",
            "action_planting": members[0].pk,
            "choose_action": "1",
        },
    )
    assert loaded.status_code == 200
    subform = loaded.context["capture_form"].cycle_form
    values = {
        field.html_name: field.value() or "" for field in subform if field.name != "category_ids"
    }
    response = client.post(
        url,
        {
            **base,
            "submission_key": str(uuid4()),
            "planting_action": "cycle",
            "action_planting": members[0].pk,
            **values,
            "cycle-status": "completed",
            "cycle-completed_on": "2026-01-02",
        },
    )
    assert response.status_code == 302
    assert DiaryEntry.objects.count() == 3


def test_deactivated_actor_and_anonymous_actions_rejected(client, user, planting_catalog):
    action = NewPlantingAction(crop=planting_catalog[0].pk)
    user.is_active = False
    user.save()
    user.is_active = True
    with pytest.raises(PermissionDenied):
        capture(user, action)
    assert client.post(reverse("diary:capture"), {"planting_action": "new"}).status_code == 302
    assert not DiaryEntry.objects.exists()


@pytest.mark.parametrize("failure", ["processing", "publication", "receipt"])
def test_combined_photo_failures_are_atomic_and_identical_retry_succeeds(
    user, planting_catalog, image_upload, monkeypatch, failure
):
    from core import photo_storage

    crop = planting_catalog[0]
    key = uuid4()
    action = NewPlantingAction(crop=crop.pk)
    target, name = (
        (photo_storage, "process_staged")
        if failure == "processing"
        else (photo_storage, "publish_staged")
        if failure == "publication"
        else (DiarySubmission, "save")
    )
    original = getattr(target, name)
    calls = 0

    def fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        if failure == "receipt" or calls == 2:
            raise OSError("Synthetic combined save failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(target, name, fail)
    with pytest.raises(OSError):
        capture(
            user,
            action,
            uploads=[image_upload()],
            planting_uploads=[image_upload(format="PNG")],
            submission_key=key,
        )
    assert (
        not DiaryEntry.objects.exists()
        and not Planting.objects.exists()
        and not Photo.objects.exists()
    )
    monkeypatch.setattr(target, name, original)
    saved = capture(
        user,
        action,
        uploads=[image_upload()],
        planting_uploads=[image_upload(format="PNG")],
        submission_key=key,
    )
    assert not capture(
        user,
        action,
        uploads=[image_upload()],
        planting_uploads=[image_upload(format="PNG")],
        submission_key=key,
    ).created
    assert saved.entry.photos.count() == 1 and Photo.objects.count() == 2


def test_competing_cycle_captures_accept_one_original_version(user, members):
    barrier = Barrier(2)
    actions = [status_action(members), status_action(members, "ongoing")]

    def write(action):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                capture(user, action)
                return "saved"
            except RegisterConflict:
                return "conflict"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, actions)) == ["conflict", "saved"]
    assert DiaryEntry.objects.count() == DiarySubmission.objects.count() == 1


def test_photo_limits_apply_per_owner_with_shared_byte_budget(
    user, planting_catalog, image_upload, settings, client
):
    action = NewPlantingAction(crop=planting_catalog[0].pk)
    settings.PHOTO_MAX_COUNT = 2
    with pytest.raises(ValidationError):
        capture(user, action, planting_uploads=[image_upload() for _ in range(3)])
    settings.PHOTO_MAX_TOTAL_BYTES = image_upload().size + 1
    with pytest.raises(ValidationError):
        capture(user, action, uploads=[image_upload()], planting_uploads=[image_upload()])
    settings.PHOTO_MAX_TOTAL_BYTES = 80 * 1024 * 1024
    client.force_login(user)
    response = client.post(
        reverse("diary:capture"),
        {
            "submission_key": uuid4(),
            "date": "2026-01-02",
            "body": "",
            "capture_mode": "quick",
            "planting_action": "new",
            "planting-crop": action.crop,
            "planting-start_year": 2026,
            "planting-status": "ongoing",
            "planting-started_on": "2026-01-02",
            "planting-lifespan": "annual",
            "library": [image_upload(), image_upload()],
            "planting-photos": [image_upload(), image_upload()],
        },
    )
    assert response.status_code == 302
    assert Photo.objects.count() == 4


def test_legacy_unknown_dates_remain_unknown_without_a_status_change(user, members):
    one = members[0]
    with validated_write():
        cycle = PlantingCycle.objects.get(pk=one.cycle_id)
        cycle.status = "ongoing"
        cycle.started_on = None
        cycle.save()
    capture(
        user,
        CycleStatusAction(
            planting=one.pk, expected_updated_at=cycle.updated_at, status="ongoing", started_on=None
        ),
    )
    cycle.refresh_from_db()
    assert cycle.started_on is None


def test_standalone_completion_http_member_versions_and_clearing(client, user, members):
    one, two = members
    cycle = one.cycle
    cycle = transition_cycle(
        actor=user,
        pk=cycle.pk,
        action="start",
        date=date(2024, 3, 1),
        expected_updated_at=cycle.updated_at,
    ).record
    client.force_login(user)
    url = reverse("garden:cycle-complete", args=[cycle.pk])
    page = client.get(url)
    form = page.context["form"]
    values = {field.html_name: field.value() or "" for field in form}
    response = client.post(
        url,
        {
            **values,
            "date": "2026-01-02",
            f"evaluation_{one.pk}": "",
            f"evaluation_{two.pk}": "New result",
        },
    )
    assert response.status_code == 302
    one.refresh_from_db()
    two.refresh_from_db()
    assert (one.evaluation, two.evaluation) == ("", "New result")


def test_combined_pages_media_and_original_download_authorization(
    client, user, other_user, planting_catalog, image_upload
):
    entry = capture(
        user,
        NewPlantingAction(crop=planting_catalog[0].pk),
        uploads=[image_upload()],
        planting_uploads=[image_upload(format="PNG")],
    ).entry
    planting = entry.links.get().planting
    photos = list(Photo.objects.all())
    urls = [entry.get_absolute_url(), planting.get_absolute_url()]
    urls += [
        url
        for photo in photos
        for url in (
            photo.get_absolute_url(),
            photo.original_url,
            photo.display_url,
            photo.thumbnail_url,
        )
    ]
    for url in urls:
        assert client.get(url).status_code == 302
    client.force_login(other_user)
    for url in urls:
        assert client.get(url).status_code == 200
    response = client.get(photos[0].original_url)
    assert response["Content-Disposition"].startswith("attachment;")


def test_cycle_action_preserves_categories_and_entry_edits_do_not_repeat_events(user, members):
    from diary.services import update_entry
    from garden.services.cycles import create_planting_category

    one = members[0]
    category = create_planting_category(actor=user, name="Trial").record
    cycle = PlantingCycle.objects.get(pk=one.cycle_id)
    edit_cycle(
        actor=user,
        pk=cycle.pk,
        status="planned",
        category_ids=[category.pk],
        expected_updated_at=cycle.updated_at,
    )
    entry = capture(user, PlantingEventAction(planting=one.pk, kind="sowing")).entry
    update_entry(
        actor=user,
        pk=entry.pk,
        date=entry.date,
        body="Edited observation",
        links=(),
        expected_updated_at=entry.updated_at,
    )
    assert PlantingEvent.objects.count() == 1
    capture(user, status_action(members))
    cycle.refresh_from_db()
    assert list(cycle.categories.values_list("pk", flat=True)) == [category.pk]


@pytest.mark.parametrize("action", ["new", "event"])
@pytest.mark.parametrize("override", [False, True])
def test_native_date_defaults_follow_backdated_diary_date(
    client, user, members, planting_catalog, action, override
):
    client.force_login(user)
    values = {
        "submission_key": uuid4(),
        "date": "2025-12-31",
        "default_date": "2026-01-10",
        "body": "Backdated native observation",
        "capture_mode": "quick",
        "planting_action": action,
    }
    actual = "2025-12-15" if override else "2026-01-10"
    if action == "new":
        values.update(
            {
                "planting-crop": planting_catalog[0].pk,
                "planting-status": "ongoing",
                "planting-start_year": "2026",
                "planting-started_on": actual,
                "planting-lifespan": "annual",
            }
        )
    else:
        values.update(
            {"action_planting": members[0].pk, "event-kind": "sowing", "event-actual_date": actual}
        )
    response = client.post(reverse("diary:capture"), values)
    assert response.status_code == 302
    expected = date(2025, 12, 15) if override else date(2025, 12, 31)
    if action == "new":
        planting = DiaryEntry.objects.get().links.get().planting
        assert planting.start_year == 2025 and planting.cycle.started_on == expected
    else:
        assert PlantingEvent.objects.get().actual_date == expected
