"""User feedback: independent batch members, navigation context and photo previews."""

import hashlib
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.urls import reverse
from django.utils.translation import override

from core.backups import create_backup, open_snapshot, restore_backup
from core.models import User
from core.writes import validated_write
from diary.queries import observations_for
from diary.services import create_entry
from garden.forms import PlantingForm
from garden.models import Planting
from garden.queries import planting_history, planting_tree
from garden.services import (
    archive_area,
    create_area,
    create_category,
    create_crop,
    create_planting,
    create_planting_event,
    create_variety,
    delete_planting,
    edit_planting,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def feedback_catalog(user):
    crop = create_crop(actor=user, name="Tomato").record
    first = create_variety(actor=user, crop=crop, name="Golden").record
    second = create_variety(actor=user, crop=crop, name="Red").record
    area = create_area(actor=user, name="Bed").record
    return crop, first, second, area


def test_generated_label_handles_maximum_names_and_preserves_custom_edits(user):
    crop = create_crop(actor=user, name="C" * 100).record
    variety = create_variety(actor=user, crop=crop, name="V" * 150).record
    generated = create_planting(actor=user, crop=crop, variety=variety, start_year=2027).record
    assert generated.label == crop.name + "-" + variety.name
    custom = create_planting(
        actor=user, crop=crop, variety=variety, start_year=2027, label="Custom trial"
    ).record
    edited = edit_planting(
        actor=user,
        pk=custom.pk,
        crop=crop,
        start_year=2027,
        label=custom.label,
        expected_updated_at=custom.updated_at,
    ).record
    assert edited.label == "Custom trial"


def test_batch_members_keep_independent_tracking_and_derived_diary_filters(
    user, feedback_catalog, image_upload
):
    crop, first, second, area = feedback_catalog
    original = create_planting(
        actor=user,
        crop=crop,
        variety=first,
        area=area,
        start_year=2027,
        quantity=0,
        evaluation="First evaluation",
        uploads=[image_upload()],
    ).record
    added = create_planting(
        actor=user,
        crop=crop,
        variety=second,
        area=area,
        start_year=2027,
        batch_planting=original,
        quantity=12,
        evaluation="Second evaluation",
    ).record
    event = create_planting_event(
        actor=user,
        planting=added,
        kind="sowing",
        planned_date=date(2027, 3, 1),
    ).record
    entry = create_entry(
        author=user,
        date=date(2027, 3, 1),
        body="Second variety observation",
        links=[added],
        uploads=[],
        submission_key=uuid4(),
    ).entry
    assert added.batch_key == original.batch_key
    assert (original.label, added.label) == ("Tomato-Golden", "Tomato-Red")
    assert (original.quantity, added.quantity) == (0, 12)
    assert (original.evaluation, added.evaluation) == ("First evaluation", "Second evaluation")
    assert original.photos.count() == 1 and added.photos.count() == 0
    assert not original.events.exists() and added.events.get() == event
    assert list(observations_for("variety", second)) == [entry]
    assert not observations_for("variety", first)
    assert list(planting_history(variety=second)) == [added]
    tree = planting_tree(year=2027, area=area)
    assert len(tree["roots"][0].groups) == 1
    assert len(tree["roots"][0].groups[0]["records"]) == 2
    filtered = planting_tree(year=2027, variety=second)
    assert filtered["roots"][0].groups[0]["records"] == [added]
    other_batch = create_planting(actor=user, crop=crop, variety=second, start_year=2027).record
    assert other_batch.batch_key != added.batch_key


@override("en")
def test_batch_constraints_and_retry_survive_deleted_original_member(user, feedback_catalog):
    crop, first, second, _ = feedback_catalog
    original = create_planting(actor=user, crop=crop, variety=first, start_year=2027).record
    with pytest.raises(ValidationError, match="already in the batch"):
        create_planting(
            actor=user, crop=crop, variety=first, start_year=2027, batch_planting=original
        )
    with pytest.raises(ValidationError, match="Choose a variety"):
        create_planting(actor=user, crop=crop, start_year=2027, batch_planting=original)
    other_crop = create_crop(actor=user, name="Bean").record
    with pytest.raises(ValidationError, match="same crop"):
        create_planting(actor=user, crop=other_crop, start_year=2027, batch_planting=original)
    key = uuid4()
    values = dict(
        actor=user,
        crop=crop,
        variety=second,
        start_year=2027,
        batch_planting=original,
        submission_key=key,
    )
    added = create_planting(**values).record
    with pytest.raises(ValidationError, match="same crop"):
        edit_planting(
            actor=user,
            pk=added.pk,
            crop=other_crop,
            start_year=2027,
            expected_updated_at=added.updated_at,
        )
    delete_planting(actor=user, pk=original.pk, expected_updated_at=original.updated_at)
    assert create_planting(**values).replayed
    assert Planting.objects.get(pk=added.pk).batch_key == added.batch_key


def test_batch_variety_choices_stay_required_after_reloading(client, user, feedback_catalog):
    crop, first, second, _ = feedback_catalog
    original = create_planting(actor=user, crop=crop, variety=first, start_year=2027).record
    client.force_login(user)
    response = client.get(
        reverse("garden:planting-varieties"),
        {"crop": crop.pk, "batch_planting": original.pk},
    )
    assert response.status_code == 200
    assert response.context["field"].field.required
    assert f'value="{first.pk}"' not in response.text
    assert f'value="{second.pk}"' in response.text


def test_overlapping_additions_cannot_duplicate_a_variety(user, feedback_catalog):
    crop, first, second, _ = feedback_catalog
    original = create_planting(actor=user, crop=crop, variety=first, start_year=2027).record
    barrier = threading.Barrier(2)

    def add():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                return create_planting(
                    actor=User.objects.get(pk=user.pk),
                    crop=crop.pk,
                    variety=second.pk,
                    start_year=2027,
                    batch_planting=original.pk,
                )
            except ValidationError:
                return None
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(add) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert sum(result is not None for result in results) == 1
    assert Planting.objects.filter(batch_key=original.batch_key).count() == 2


def test_batch_member_uniqueness_is_enforced_by_sqlite(user, feedback_catalog):
    crop, first, _, _ = feedback_catalog
    original = create_planting(actor=user, crop=crop, start_year=2027).record
    create_planting(actor=user, crop=crop, variety=first, start_year=2027, batch_planting=original)
    for variety in (None, first):
        with pytest.raises(IntegrityError), transaction.atomic(), validated_write():
            Planting.objects.create(
                crop=crop, variety=variety, start_year=2027, batch_key=original.batch_key
            )
    assert Planting.objects.filter(batch_key=original.batch_key).count() == 2


@pytest.mark.parametrize("hx", [False, True])
def test_contextual_creation_returns_after_save_cancel_and_failed_submission(
    client, user, feedback_catalog, hx
):
    crop, _, _, area = feedback_catalog
    client.force_login(user)
    destination = area.get_absolute_url() + "?archived=1"
    path = reverse("garden:planting-create")
    form = client.get(path, {"area": area.pk, "next": destination})
    assert form.context["form"]["area"].value() == str(area.pk)
    assert form.context["cancel_url"] == destination
    data = {
        "crop": crop.pk,
        "area": area.pk,
        "start_year": 2027,
        "lifespan": "annual",
        "submission_key": uuid4(),
        "next": destination,
    }
    headers = {"HTTP_HX_REQUEST": "true"} if hx else {}
    invalid = client.post(path, {**data, "start_year": "bad"}, **headers)
    assert invalid.status_code == 422
    if not hx:
        assert invalid.context["cancel_url"] == destination
        assert 'name="next"' in invalid.text
    saved = client.post(path, data, **headers)
    assert saved["HX-Redirect" if hx else "Location"] == destination
    assert Planting.objects.get().label == "Tomato"
    retry = client.post(path, data, **headers)
    assert retry["HX-Redirect" if hx else "Location"] == destination
    assert Planting.objects.count() == 1


@pytest.mark.parametrize(
    "destination",
    [
        "https://example.invalid/",
        "//example.invalid/",
        "/\\example.invalid/",
        "/accounts/logout/",
        "/garden/plantings/new/",
    ],
)
def test_return_destination_rejects_external_or_unrelated_pages(
    client, user, feedback_catalog, destination
):
    crop, _, _, _ = feedback_catalog
    client.force_login(user)
    response = client.post(
        reverse("garden:planting-create"),
        {
            "crop": crop.pk,
            "start_year": 2027,
            "lifespan": "annual",
            "submission_key": uuid4(),
            "next": destination,
        },
    )
    assert response["Location"] == Planting.objects.get().get_absolute_url()


def test_category_variety_creation_retains_scope_and_filters(client, user, feedback_catalog):
    crop, _, _, _ = feedback_catalog
    category = create_category(actor=user, name="Vegetables", crops=[crop]).record
    unrelated = create_crop(actor=user, name="Rose").record
    destination = category.get_absolute_url() + "?q=tom&archived=1"
    client.force_login(user)
    page = client.get(destination)
    target = page.context["create_url"]
    form = client.get(target)
    assert form.context["cancel_url"] == destination
    assert not form.context["form"].fields["crop"].queryset.filter(pk=unrelated.pk).exists()
    response = client.post(
        reverse("garden:variety-create"),
        {
            "crop": crop.pk,
            "name": "New variety",
            "submission_key": uuid4(),
            "next": destination,
        },
        HTTP_HX_REQUEST="true",
    )
    assert response["HX-Redirect"] == destination


def test_leaf_area_choices_preserve_existing_parent_assignments(user, feedback_catalog):
    crop, _, _, area = feedback_catalog
    existing = create_planting(actor=user, crop=crop, area=area, start_year=2027).record
    child = create_area(actor=user, name="Child", parent=area).record
    archive_area(actor=user, pk=child.pk)
    assert not PlantingForm().fields["area"].queryset.filter(pk=area.pk).exists()
    assert PlantingForm(record=existing).fields["area"].queryset.filter(pk=area.pk).exists()


@pytest.mark.parametrize("archived_child", [False, True])
def test_parent_area_writes_preserve_existing_assignments_and_receipts(
    user, feedback_catalog, archived_child
):
    crop, _, _, area = feedback_catalog
    values = dict(actor=user, crop=crop, area=area, start_year=2027, submission_key=uuid4())
    existing = create_planting(**values).record
    unassigned = create_planting(actor=user, crop=crop, start_year=2027).record
    child = create_area(actor=user, name="Child", parent=area).record
    if archived_child:
        archive_area(actor=user, pk=child.pk)
    with pytest.raises(ValidationError) as rejected:
        create_planting(actor=user, crop=crop, area=area, start_year=2027)
    assert "area" in rejected.value.message_dict
    with pytest.raises(ValidationError) as rejected:
        edit_planting(
            actor=user,
            pk=unassigned.pk,
            crop=crop,
            area=area,
            start_year=2027,
            expected_updated_at=unassigned.updated_at,
        )
    assert "area" in rejected.value.message_dict
    edited = edit_planting(
        actor=user,
        pk=existing.pk,
        crop=crop,
        area=area,
        start_year=2027,
        label="Existing assignment",
        expected_updated_at=existing.updated_at,
    ).record
    assert edited.area_id == area.pk
    assert create_planting(**values).replayed
    assert Planting.objects.count() == 2


@pytest.mark.parametrize("hx", [False, True])
def test_area_gaining_children_during_photo_processing_rejects_new_assignment(
    client, user, feedback_catalog, image_upload, monkeypatch, hx
):
    from core.models import Photo
    from garden.services import plantings as services

    crop, _, _, area = feedback_catalog
    client.force_login(user)
    path = reverse("garden:planting-create")
    draft = client.get(path, {"area": area.pk})
    assert draft.context["form"]["area"].value() == str(area.pk)
    write_planting = services._write_planting

    def add_child_before_write(*args, **kwargs):
        create_area(actor=user, name="New child", parent=area)
        return write_planting(*args, **kwargs)

    monkeypatch.setattr(services, "_write_planting", add_child_before_write)
    key = uuid4()
    response = client.post(
        path,
        {
            "crop": crop.pk,
            "area": area.pk,
            "start_year": 2027,
            "lifespan": "annual",
            "submission_key": key,
            "photos": image_upload(),
        },
        HTTP_HX_REQUEST="true" if hx else "false",
    )
    assert response.status_code == 422
    assert 'data-field-errors="area"' in response.text
    assert not Planting.objects.exists() and not Photo.objects.exists()
    if not hx:
        assert response.context["form"]["area"].value() == str(area.pk)
        assert response.context["form"]["submission_key"].value() == str(key)


def test_area_and_diary_use_compact_private_previews_and_explicit_year_links(
    client, user, feedback_catalog, image_upload
):
    crop, first, _, area = feedback_catalog
    batch = create_planting(
        actor=user, crop=crop, variety=first, area=area, start_year=2027, uploads=[image_upload()]
    ).record
    entry = create_entry(
        author=user,
        date=date(2027, 3, 1),
        body="",
        uploads=[image_upload()],
        submission_key=uuid4(),
    ).entry
    client.force_login(user)
    page = client.get(area.get_absolute_url())
    assert batch.photos.get().thumbnail_url in page.text
    assert "year=2027" in page.text or "2027" in page.text
    assert page.context["planting_all_years_url"] in page.text.replace("&amp;", "&")
    assert page.context["can_create_planting"]
    observation = client.get(entry.get_absolute_url())
    photo = entry.photos.get()
    assert 'class="photo-grid entry-photos"' in observation.text
    assert photo.thumbnail_url in observation.text
    assert photo.display_url not in observation.text
    assert photo.get_absolute_url() in observation.text


def test_area_batch_overview_uses_a_member_photo_when_the_first_member_has_none(
    client, user, feedback_catalog, image_upload
):
    crop, first, second, area = feedback_catalog
    original = create_planting(
        actor=user, crop=crop, variety=first, area=area, start_year=2027, label="Custom batch"
    ).record
    added = create_planting(
        actor=user,
        crop=crop,
        variety=second,
        area=area,
        start_year=2027,
        batch_planting=original,
        uploads=[image_upload()],
    ).record
    client.force_login(user)
    response = client.get(area.get_absolute_url())
    group = response.context["planting_groups"][0]
    assert group["records"][0] == original
    assert group["thumbnail"] == added.photos.get()
    summary = response.text.split('<details class="planting-batch"')[1].split("</summary>")[0]
    assert group["thumbnail"].thumbnail_url in summary and "Custom batch" in summary


def test_batch_migration_preserves_old_ids_fields_and_immutable_receipts():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    before = [("garden", "0003_cropcategory_cropcategorylink_and_more")]
    try:
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        user = apps.get_model("core", "User").objects.create(username="synthetic-batch-upgrade")
        crop = apps.get_model("garden", "Crop").objects.create(name="Tomato", name_key="tomato")
        planting = apps.get_model("garden", "Planting")
        first = planting.objects.create(crop=crop, start_year=2027, label="Old batch", quantity=0)
        second = planting.objects.create(crop=crop, start_year=2027, label="Old batch")
        key = uuid4()
        # The pre-batch receipt format must remain replayable after the migration.
        legacy_payload = {
            "version": 1,
            "kind": "planting",
            "pk": None,
            "expected_updated_at": None,
            "values": {
                "crop_id": crop.pk,
                "variety_id": None,
                "area_id": None,
                "start_year": 2027,
                "lifespan": "annual",
                "end_year": None,
                "label": "Old batch",
                "quantity": 0,
                "notes": "",
                "evaluation": "",
            },
            "photos": [],
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                legacy_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        apps.get_model("garden", "PlantingSubmission").objects.create(
            key=key,
            author=user,
            planting=first,
            kind="planting",
            payload_sha256=fingerprint,
        )
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        upgraded = Planting.objects.get(pk=first.pk)
        assert upgraded.label == "Old batch" and upgraded.quantity == 0
        assert upgraded.batch_key != Planting.objects.get(pk=second.pk).batch_key
        receipt = (
            executor.loader.project_state(latest)
            .apps.get_model("garden", "PlantingSubmission")
            .objects.get(key=key)
        )
        assert receipt.planting_id == first.pk and receipt.payload_sha256 == fingerprint
        replay = create_planting(
            actor=User.objects.get(pk=user.pk),
            crop=crop.pk,
            start_year=2027,
            label="Old batch",
            quantity=0,
            submission_key=key,
        )
        assert replay.replayed and replay.record.pk == first.pk
    finally:
        MigrationExecutor(connection).migrate(latest)


def test_clean_restore_preserves_batch_members_and_independent_tracking(
    user, feedback_catalog, image_upload, tmp_path
):
    crop, first, second, area = feedback_catalog
    original = create_planting(
        actor=user,
        crop=crop,
        variety=first,
        area=area,
        start_year=2027,
        quantity=0,
        evaluation="First",
        uploads=[image_upload()],
    ).record
    added = create_planting(
        actor=user,
        crop=crop,
        variety=second,
        area=area,
        start_year=2027,
        quantity=7,
        evaluation="Second",
        batch_planting=original,
    ).record
    create_planting_event(actor=user, planting=added, kind="sowing", planned_date=date(2027, 3, 1))
    configuration = tmp_path / "operator.env"
    configuration.write_text(
        "GROWBOOK_SECRET_KEY=synthetic-recovery-configuration\n", encoding="utf-8"
    )
    create_backup(tmp_path / "backup", [configuration])
    restored = tmp_path / "restored"
    restore_backup(tmp_path / "backup", restored)
    snapshot = open_snapshot(restored / "db.sqlite3")
    try:
        rows = snapshot.execute(
            "SELECT batch_key, quantity, evaluation FROM garden_planting ORDER BY id"
        ).fetchall()
        assert rows == [(original.batch_key.hex, 0, "First"), (original.batch_key.hex, 7, "Second")]
    finally:
        snapshot.close()
    script = """
import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'growbook.settings.development'
import django
django.setup()
from core.models import User, Photo
from garden.models import Planting, PlantingEvent
from garden.services import create_variety, create_planting
from django.test import Client
user = User.objects.get(username='gardener')
records = list(Planting.objects.order_by('id'))
assert records[0].batch_key == records[1].batch_key
assert records[0].photos.count() == 1 and not records[1].photos.exists()
assert not records[0].events.exists() and records[1].events.count() == 1
client = Client(HTTP_HOST='localhost')
assert client.get(records[0].get_absolute_url()).status_code == 302
client.force_login(user)
assert client.get(records[1].get_absolute_url()).status_code == 200
variety = create_variety(actor=user, crop=records[0].crop, name='Restored variety').record
added = create_planting(actor=user, crop=records[0].crop, variety=variety, start_year=2027,
                        batch_planting=records[1]).record
assert added.batch_key == records[0].batch_key
"""
    environment = {**os.environ, "GROWBOOK_DATA_DIR": str(restored)}
    result = subprocess.run(
        [sys.executable, "-c", script], env=environment, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
