import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, close_old_connections, connection, transaction

from core.media import private_path
from core.models import Photo
from core.writes import validated_write
from diary import services as diary_services
from diary.models import DiaryEntry, DiaryLink, DiarySubmission
from diary.queries import filter_entries
from diary.services import (
    EntryConflict,
    create_entry,
    delete_entry,
    delete_entry_photo,
    update_entry,
    update_entry_photo,
)
from garden.models import Planting
from garden.services import (
    archive_crop,
    create_area,
    create_crop,
    create_planting,
    create_variety,
    delete_crop,
    delete_planting,
)

pytestmark = pytest.mark.django_db(transaction=True)


def entry(user, *, body="Synthetic observation", uploads=()):
    return create_entry(
        author=user,
        date=date(2026, 5, 9),
        body=body,
        uploads=uploads,
        submission_key=uuid4(),
    ).entry


def edit(record, *, actor=None, **overrides):
    values = {
        "date": record.date,
        "body": record.body,
        "links": (),
        "expected_updated_at": record.updated_at,
    }
    values.update(overrides)
    return update_entry(actor=actor or record.author, pk=record.pk, **values)


@pytest.fixture
def garden(user):
    crop = create_crop(actor=user, name="Synthetic tomato").record
    variety = create_variety(actor=user, crop=crop, name="Synthetic Ferline").record
    parent = create_area(actor=user, name="Synthetic greenhouse").record
    child = create_area(actor=user, name="Synthetic bed", parent=parent).record
    planting = create_planting(
        actor=user,
        crop=crop,
        start_year=2026,
        variety=variety,
        area=child,
        label="Synthetic batch",
    ).record
    return SimpleNamespace(
        crop=crop, variety=variety, parent=parent, child=child, planting=planting
    )


def test_author_edits_own_entry_and_search_text(user):
    record = entry(user, body="Old note")
    original_author_id = record.author_id
    updated = edit(
        record,
        date=date(2026, 6, 1),
        body="  Ångström ärtor  ",
    )
    updated.refresh_from_db()
    assert updated.date == date(2026, 6, 1)
    assert updated.body == "Ångström ärtor"
    assert updated.search_text == "ångström ärtor"
    assert updated.author_id == original_author_id


def test_other_member_cannot_edit_or_delete(user, other_user):
    record = entry(user)
    before = record.updated_at
    with pytest.raises(PermissionDenied):
        edit(record, actor=other_user, body="Intruder")
    with pytest.raises(PermissionDenied):
        delete_entry(actor=other_user, pk=record.pk, expected_updated_at=before)
    record.refresh_from_db()
    assert record.body == "Synthetic observation"
    assert record.updated_at == before


def test_staff_administrator_edits_any_entry_and_author_is_unchanged(user, staff_user):
    record = entry(user)
    edit(record, actor=staff_user, body="Administrator correction")
    record.refresh_from_db()
    assert record.body == "Administrator correction"
    assert record.author_id == user.pk


def test_deactivated_author_is_denied_inside_the_write(user):
    record = entry(user)
    user.is_active = False
    user.save(update_fields=["is_active"])
    with pytest.raises(PermissionDenied):
        edit(record)
    record.refresh_from_db()
    assert record.body == "Synthetic observation"


def test_stale_version_conflict_preserves_saved_values(user):
    record = entry(user)
    original_version = record.updated_at
    edit(record, body="First save")
    record.refresh_from_db()
    with pytest.raises(EntryConflict):
        update_entry(
            actor=user,
            pk=record.pk,
            date=record.date,
            body="Second save",
            expected_updated_at=original_version,
        )
    record.refresh_from_db()
    assert record.body == "First save"


def test_links_are_replaced_and_each_has_exactly_one_target(user, garden):
    record = entry(user)
    edit(record, links=[garden.parent, garden.crop, garden.variety, garden.planting])
    record.refresh_from_db()
    assert record.links.count() == 4
    edit(record, links=[garden.planting])
    record.refresh_from_db()
    links = list(record.links.all())
    assert len(links) == 1
    assert links[0].planting_id == garden.planting.pk
    assert links[0].target == garden.planting


def test_link_constraints_reject_two_targets_and_duplicates(user, garden):
    record = entry(user)
    with pytest.raises(ValidationError), transaction.atomic(), validated_write():
        DiaryLink(entry=record, crop=garden.crop, variety=garden.variety).full_clean()
    with pytest.raises(IntegrityError), transaction.atomic(), validated_write():
        DiaryLink.objects.create(entry=record, crop=garden.crop, variety=garden.variety)
    with pytest.raises(IntegrityError), transaction.atomic(), validated_write():
        DiaryLink.objects.create(entry=record, crop=garden.crop)
        DiaryLink.objects.create(entry=record, crop=garden.crop)


def test_duplicate_targets_in_one_edit_are_rejected(user, garden):
    record = entry(user)
    with pytest.raises(ValidationError):
        edit(record, links=[garden.crop, garden.crop])


def test_new_links_to_archived_records_are_rejected_but_existing_links_survive(user, garden):
    record = entry(user)
    second_crop = create_crop(actor=user, name="Synthetic archived crop").record
    edit(record, links=[garden.crop, garden.variety])
    archive_crop(actor=user, pk=second_crop.pk)
    with pytest.raises(ValidationError):
        edit(record, links=[garden.crop, garden.variety, second_crop])
    record.refresh_from_db()
    edit(record, links=[garden.crop, garden.variety])
    record.refresh_from_db()
    assert {link.crop_id for link in record.links.all() if link.crop_id} == {garden.crop.pk}


def test_linked_garden_records_cannot_be_deleted_while_a_diary_link_exists(user, garden):
    record = entry(user)
    edit(record, links=[garden.crop, garden.planting])
    with pytest.raises(ValidationError):
        delete_crop(actor=user, pk=garden.crop.pk, expected_updated_at=garden.crop.updated_at)
    with pytest.raises(ValidationError):
        delete_planting(
            actor=user, pk=garden.planting.pk, expected_updated_at=garden.planting.updated_at
        )
    assert DiaryLink.objects.filter(entry=record).count() == 2
    garden.crop.refresh_from_db()
    assert garden.crop.pk
    assert Planting.objects.filter(pk=garden.planting.pk).exists()


def test_archived_records_remain_filterable_through_existing_links(user, garden):
    record = entry(user)
    edit(record, links=[garden.crop])
    archive_crop(actor=user, pk=garden.crop.pk)
    assert list(filter_entries(crop=garden.crop).values_list("pk", flat=True)) == [record.pk]


def test_photo_removal_requires_note_or_remaining_photo(user, image_upload):
    record = entry(user, body="", uploads=[image_upload()])
    photo = record.photos.get()
    with pytest.raises(ValidationError):
        edit(record, remove_photo_ids=[photo.pk])
    record.refresh_from_db()
    assert record.photos.count() == 1


def test_failed_photo_edit_keeps_existing_photo_and_cleans_staging(user, image_upload, settings):
    record = entry(user, body="Note", uploads=[image_upload()])
    original_photo = record.photos.get()
    original_body = record.body
    bad_upload = SimpleUploadedFile(
        "synthetic.svg",
        b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
        content_type="image/svg+xml",
    )
    with pytest.raises(ValidationError):
        edit(record, body="Changed note", uploads=[bad_upload])
    record.refresh_from_db()
    assert record.body == original_body
    assert list(record.photos.values_list("pk", flat=True)) == [original_photo.pk]
    assert list((settings.MEDIA_ROOT / ".staging").glob("*")) == []


def test_removing_a_photo_deletes_its_private_bundle_when_note_remains(user, image_upload):
    record = entry(user, body="Note", uploads=[image_upload()])
    photo = record.photos.get()
    bundle = private_path(photo.original.name).parent
    assert bundle.exists()
    edit(record, remove_photo_ids=[photo.pk])
    record.refresh_from_db()
    assert record.photos.count() == 0
    assert not bundle.exists()


def test_editing_adds_photos_after_existing_positions(user, image_upload):
    record = entry(user, body="Note", uploads=[image_upload()])
    first_position = record.photos.get().position
    edit(record, uploads=[image_upload(), image_upload()])
    record.refresh_from_db()
    positions = list(record.photos.values_list("position", flat=True))
    assert len(positions) == 3
    assert min(positions) == first_position
    assert len(set(positions)) == 3


def test_photo_limit_is_enforced_across_existing_and_new_photos(user, image_upload):
    record = entry(user, body="Note", uploads=[image_upload() for _ in range(10)])
    with pytest.raises(ValidationError):
        edit(record, uploads=[image_upload()])
    record.refresh_from_db()
    assert record.photos.count() == 10


def test_photo_caption_and_order_use_the_entry_version(user, image_upload):
    record = entry(user, body="Note", uploads=[image_upload()])
    photo = record.photos.get()
    original_version = record.updated_at
    update_entry_photo(
        actor=user,
        pk=photo.pk,
        caption="Synthetic caption",
        position=3,
        expected_updated_at=original_version,
    )
    photo.refresh_from_db()
    record.refresh_from_db()
    assert (photo.caption, photo.position) == ("Synthetic caption", 3)
    assert record.updated_at != original_version
    with pytest.raises(EntryConflict):
        update_entry_photo(
            actor=user,
            pk=photo.pk,
            caption="Stale",
            position=4,
            expected_updated_at=original_version,
        )


def test_last_photo_cannot_be_deleted_from_a_photo_only_entry(user, image_upload):
    record = entry(user, body="", uploads=[image_upload()])
    photo = record.photos.get()
    with pytest.raises(ValidationError):
        delete_entry_photo(actor=user, pk=photo.pk, expected_updated_at=record.updated_at)
    edit(record, body="Now there is a note")
    record.refresh_from_db()
    delete_entry_photo(actor=user, pk=photo.pk, expected_updated_at=record.updated_at)
    assert not Photo.objects.filter(pk=photo.pk).exists()


def test_deleting_an_entry_removes_links_and_photos_and_keeps_the_tombstone(
    user, garden, image_upload
):
    record = entry(user, body="Note", uploads=[image_upload()])
    edit(record, links=[garden.planting])
    record.refresh_from_db()
    submission = DiarySubmission.objects.get(entry=record)
    photo = record.photos.get()
    bundle = private_path(photo.original.name).parent
    delete_entry(actor=user, pk=record.pk, expected_updated_at=record.updated_at)
    assert not DiaryEntry.objects.filter(pk=record.pk).exists()
    assert not DiaryLink.objects.exists()
    assert not Photo.objects.exists()
    assert not bundle.exists()
    submission.refresh_from_db()
    assert submission.entry_id is None
    assert Planting.objects.filter(pk=garden.planting.pk).exists()


@pytest.mark.parametrize(
    ("query", "expected_bodies"),
    [
        ("ÅNGSTRÖM", {"Ångström ärtor", "ångström note"}),
        ("ångström", {"Ångström ärtor", "ångström note"}),
        ("Ångstr\u00f6m", {"Ångström ärtor", "ångström note"}),
        ("angstrom", {"angstrom note"}),
    ],
)
def test_search_is_unicode_case_insensitive_and_diacritic_sensitive(user, query, expected_bodies):
    for body in ("Ångström ärtor", "ångström note", "angstrom note"):
        entry(user, body=body)
    found = set(filter_entries(q=query).values_list("body", flat=True))
    assert found == expected_bodies


def test_filters_follow_direct_and_derived_relationships_without_duplicates(user, garden):
    direct_crop = entry(user, body="Direct crop")
    edit(direct_crop, links=[garden.crop])
    variety_only = entry(user, body="Variety only")
    edit(variety_only, links=[garden.variety])
    planting_only = entry(user, body="Planting only")
    edit(planting_only, links=[garden.planting])
    direct_area = entry(user, body="Direct area")
    edit(direct_area, links=[garden.parent])
    unrelated = entry(user, body="Unrelated")

    crop_matches = set(filter_entries(crop=garden.crop).values_list("pk", flat=True))
    assert crop_matches == {direct_crop.pk, variety_only.pk, planting_only.pk}
    variety_matches = set(filter_entries(variety=garden.variety).values_list("pk", flat=True))
    assert variety_matches == {variety_only.pk, planting_only.pk}
    parent_area_matches = set(filter_entries(area=garden.parent).values_list("pk", flat=True))
    assert parent_area_matches == {direct_area.pk, planting_only.pk}
    child_area_matches = set(filter_entries(area=garden.child).values_list("pk", flat=True))
    assert child_area_matches == {planting_only.pk}
    planting_matches = set(filter_entries(planting=garden.planting).values_list("pk", flat=True))
    assert planting_matches == {planting_only.pk}
    assert unrelated.pk not in crop_matches


def test_deduplicates_an_entry_matching_several_paths(user, garden):
    record = entry(user)
    edit(record, links=[garden.crop, garden.variety, garden.planting])
    results = list(filter_entries(crop=garden.crop).values_list("pk", flat=True))
    assert results == [record.pk]


def test_year_filter_uses_the_entry_date_not_the_planting_year(user, garden):
    record = entry(user)
    update = edit(record, date=date(2024, 12, 31), links=[garden.planting])
    assert update.date.year == 2024
    assert list(
        filter_entries(year=2024, planting=garden.planting).values_list("pk", flat=True)
    ) == [record.pk]
    assert not filter_entries(year=2026, planting=garden.planting).exists()


def test_concurrent_edits_with_the_same_version_allow_only_one_write(user):
    record = entry(user)
    original_version = record.updated_at
    start = threading.Barrier(2)
    author_id = user.pk

    def write(number):
        close_old_connections()
        try:
            start.wait(timeout=10)
            actor = type(user).objects.get(pk=author_id)
            return update_entry(
                actor=actor,
                pk=record.pk,
                date=record.date,
                body=f"Concurrent edit {number}",
                expected_updated_at=original_version,
            ).body
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(write, number) for number in range(2)]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=20))
            except EntryConflict:
                outcomes.append("conflict")
    assert outcomes.count("conflict") == 1
    record.refresh_from_db()
    assert record.body in {"Concurrent edit 0", "Concurrent edit 1"}


def test_created_entries_have_search_text_and_edit_updates_it(user):
    record = entry(user, body="Ärtor")
    assert record.search_text == "ärtor"
    edit(record, body="Bönor")
    record.refresh_from_db()
    assert record.search_text == "bönor"


@pytest.mark.parametrize("operation", ["edit", "delete", "photo-edit", "photo-delete"])
def test_concurrent_deletion_after_preflight_returns_a_conflict(
    user, image_upload, settings, monkeypatch, operation
):
    record = entry(user, body="Note", uploads=[image_upload()])
    photo = record.photos.get()
    addition = image_upload()
    ready = threading.Event()
    resume = threading.Event()

    def pause_preflight(lookup):
        def paused(*args, **kwargs):
            result = lookup(*args, **kwargs)
            if (
                threading.current_thread() is not threading.main_thread()
                and not connection.in_atomic_block
            ):
                ready.set()
                if not resume.wait(timeout=10):
                    raise TimeoutError("Concurrent deletion did not finish.")
            return result

        return paused

    monkeypatch.setattr(
        diary_services, "_entry_for_editor", pause_preflight(diary_services._entry_for_editor)
    )
    monkeypatch.setattr(
        diary_services, "_entry_photo", pause_preflight(diary_services._entry_photo)
    )

    def write():
        close_old_connections()
        try:
            actor = type(user).objects.get(pk=user.pk)
            if operation == "edit":
                return update_entry(
                    actor=actor,
                    pk=record.pk,
                    date=record.date,
                    body="Concurrent edit",
                    uploads=[addition],
                    expected_updated_at=record.updated_at,
                )
            if operation == "delete":
                return delete_entry(
                    actor=actor, pk=record.pk, expected_updated_at=record.updated_at
                )
            if operation == "photo-edit":
                return update_entry_photo(
                    actor=actor,
                    pk=photo.pk,
                    caption="Concurrent caption",
                    position=1,
                    expected_updated_at=record.updated_at,
                )
            return delete_entry_photo(
                actor=actor, pk=photo.pk, expected_updated_at=record.updated_at
            )
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(write)
        try:
            assert ready.wait(timeout=10)
            delete_entry(actor=user, pk=record.pk, expected_updated_at=record.updated_at)
        finally:
            resume.set()
        with pytest.raises(EntryConflict):
            future.result(timeout=20)

    assert not DiaryEntry.objects.filter(pk=record.pk).exists()
    assert not Photo.objects.filter(entry_id=record.pk).exists()
    assert not list(settings.MEDIA_ROOT.glob("photos/*/*/*"))
    assert not list((settings.MEDIA_ROOT / ".staging").glob("*"))
