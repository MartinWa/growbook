"""Growing-area cards and detail pages retain existing records and associations."""

from datetime import date
from uuid import uuid4

import pytest
from django.urls import reverse

from core.models import Photo
from core.writes import validated_write
from diary.models import DiaryEntry, DiaryLink
from diary.services import create_entry
from garden.models import GrowingArea, Planting
from garden.services import archive_area, create_area, create_crop, create_planting

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("language", ["sv", "en"])
def test_overview_cards_show_only_top_level_areas_and_reuse_the_first_private_photo(
    client, user, image_upload, django_assert_num_queries, language
):
    root = create_area(
        actor=user, name="Main garden", uploads=[image_upload(), image_upload()]
    ).record
    child = create_area(actor=user, name="Child bed", parent=root, uploads=[image_upload()]).record
    leaf = create_area(actor=user, name="Standalone bed").record
    client.force_login(user)
    client.cookies["django_language"] = language
    response = client.get(reverse("garden:area-list"))
    assert response.status_code == 200
    assert [area.pk for area in response.context["records"]] == [root.pk, leaf.pk]
    assert response.text.count('class="category-card area-card"') == 2
    assert child.name not in response.text
    assert f'href="{root.get_absolute_url()}"' in response.text
    assert "data-register-toggle" not in response.text
    assert "data-record-workspace" not in response.text
    with django_assert_num_queries(0):
        photo = response.context["records"][0].photos.first()
        assert len(response.context["records"][0].photos.all()) == 2
    assert response.text.count(f'src="{photo.display_url}"') == 1
    assert "no-store" in response["Cache-Control"]
    assert GrowingArea.objects.count() == 3
    assert Photo.objects.count() == 3


@pytest.mark.parametrize("show_archived", [False, True])
def test_overview_keeps_recovered_legacy_cycle_roots_visible(client, user, show_archived):
    first = create_area(actor=user, name="Cycle A").record
    second = create_area(actor=user, name="Cycle B", parent=first).record
    leaf = create_area(actor=user, name="Cycle leaf", parent=second).record
    with validated_write():
        # Simulate legacy state that current validated operations reject.
        GrowingArea.objects.filter(pk=first.pk).update(parent_id=second.pk)
    client.force_login(user)
    response = client.get(reverse("garden:area-list"), {"archived": "1"} if show_archived else {})
    assert response.status_code == 200
    assert [area.pk for area in response.context["records"]] == [first.pk]
    assert response.text.count('class="category-card area-card"') == 1
    expected_url = first.get_absolute_url() + ("?archived=1" if show_archived else "")
    assert f'href="{expected_url}"' in response.text
    first_page = client.get(expected_url)
    assert [child.pk for child in first_page.context["children"]] == [second.pk]
    second_page = client.get(second.get_absolute_url())
    assert leaf in second_page.context["children"]


@pytest.mark.parametrize("language", ["sv", "en"])
@pytest.mark.parametrize("partial", [False, True])
def test_detail_pages_keep_children_photos_and_deduplicated_planting_diary_associations(
    client, user, image_upload, django_assert_num_queries, language, partial
):
    root = create_area(
        actor=user,
        name="Main garden",
        description="Synthetic garden description",
        notes="Synthetic parent notes",
        uploads=[image_upload()],
    ).record
    child = create_area(
        actor=user,
        name="Child bed",
        parent=root,
        length_cm=180,
        width_cm=90,
        notes="Synthetic bed notes",
        uploads=[image_upload(), image_upload()],
    ).record
    grandchild = create_area(actor=user, name="Inner section", parent=child).record
    retired = create_area(actor=user, name="Retired bed", parent=root).record
    unrelated = create_area(actor=user, name="Other garden").record
    crop = create_crop(actor=user, name="Synthetic crop").record
    direct = create_planting(
        actor=user, crop=crop, area=root, start_year=2026, label="Parent batch"
    ).record
    linked = create_planting(
        actor=user, crop=crop, area=child, start_year=2026, label="Child batch", quantity=0
    ).record
    deep = create_planting(
        actor=user,
        crop=crop,
        area=grandchild,
        start_year=2023,
        lifespan="persistent",
        label="Deep batch",
        uploads=[image_upload()],
    ).record
    ended = create_planting(
        actor=user,
        crop=crop,
        area=retired,
        start_year=2020,
        end_year=2022,
        lifespan="persistent",
        label="Ended batch",
    ).record
    create_planting(actor=user, crop=crop, area=unrelated, start_year=2026)
    for day, body, links in (
        (1, "Parent observation", [root]),
        (2, "Shared observation", [root, child, linked]),
        (3, "Deep observation", [deep]),
        (4, "Archived observation", [retired]),
        (5, "Unrelated observation", [unrelated]),
    ):
        create_entry(
            author=user,
            date=date(2026, 5, day),
            body=body,
            uploads=[image_upload()] if day == 4 else [],
            submission_key=uuid4(),
            links=links,
        )
    archive_area(actor=user, pk=retired.pk)
    original_areas = list(GrowingArea.objects.values())
    original_plantings = list(Planting.objects.values())
    original_photos = list(Photo.objects.values())
    original_links = list(DiaryLink.objects.values())
    client.force_login(user)
    client.cookies["django_language"] = language
    headers = {"HTTP_HX_REQUEST": "true"} if partial else {}
    response = client.get(root.get_absolute_url(), **headers)
    assert response.status_code == 200
    assert [area.pk for area in response.context["children"]] == [child.pk]
    assert response.context["plantings"] == [direct]
    assert response.context["observation_count"] == 4
    assert len(response.context["observations"]) == 4
    assert "Unrelated observation" not in response.text
    assert response.text.index("area-child-section") < response.text.index("area-information")
    assert "data-register-toggle" not in response.text
    for label in ("Planting history", "Open record", "Back to list"):
        assert label not in response.text
    with django_assert_num_queries(0):
        photo = response.context["children"][0].photos.first()
        assert len(response.context["children"][0].photos.all()) == 2
    assert f'src="{photo.display_url}"' in response.text
    assert root.photos.get().display_url in response.text
    assert f'href="{direct.get_absolute_url()}"' in response.text
    for planting in (linked, deep, ended):
        assert f'href="{planting.get_absolute_url()}"' not in response.text
    archived = client.get(root.get_absolute_url(), {"archive_toggle": "1"}, follow=True, **headers)
    assert archived.redirect_chain == [(root.get_absolute_url() + "?archived=1", 302)]
    assert [area.pk for area in archived.context["children"]] == [child.pk, retired.pk]
    assert f'href="{retired.get_absolute_url()}?archived=1"' in archived.text
    hidden = client.get(
        root.get_absolute_url(), {"archived": "1", "archive_toggle": "0"}, follow=True, **headers
    )
    assert hidden.redirect_chain == [(root.get_absolute_url(), 302)]
    assert [area.pk for area in hidden.context["children"]] == [child.pk]
    child_page = client.get(child.get_absolute_url(), **headers)
    assert [area.pk for area in child_page.context["children"]] == [grandchild.pk]
    assert [area.pk for area in child_page.context["ancestors"]] == [root.pk]
    assert child_page.context["plantings"] == [linked]
    assert f'href="{deep.get_absolute_url()}"' not in child_page.text
    assert child_page.context["observation_count"] == 2
    assert "Synthetic bed notes" in child_page.text
    assert "Synthetic parent notes" not in child_page.text
    assert child_page.text.count("<figure>") == 2
    grandchild_page = client.get(grandchild.get_absolute_url(), **headers)
    assert grandchild_page.context["plantings"] == [deep]
    retired_page = client.get(retired.get_absolute_url(), **headers)
    assert retired_page.context["plantings"] == [ended]
    assert list(GrowingArea.objects.values()) == original_areas
    assert list(Planting.objects.values()) == original_plantings
    assert list(Photo.objects.values()) == original_photos
    assert list(DiaryLink.objects.values()) == original_links
    assert DiaryEntry.objects.count() == 5


@pytest.mark.parametrize("language,raised_bed", [("sv", "Pallkrage"), ("en", "Raised bed")])
def test_raised_bed_suggestion_and_custom_types_use_existing_retry_safe_forms(
    client, user, image_upload, language, raised_bed
):
    parent = create_area(actor=user, name="Main garden").record
    area = create_area(
        actor=user,
        name="Synthetic bed",
        parent=parent,
        area_type="Synthetic custom type",
        length_cm=180,
        width_cm=90,
        description="Synthetic description",
        notes="Synthetic notes",
        uploads=[image_upload()],
    ).record
    photo_id = area.photos.get().pk
    client.force_login(user)
    client.cookies["django_language"] = language
    create_page = client.get(reverse("garden:area-create"))
    assert f'<option value="{raised_bed}">' in create_page.text
    path = reverse("garden:area-edit", args=[area.pk]) + "?archived=1"
    form_page = client.get(path)
    assert form_page.context["form"]["area_type"].value() == "Synthetic custom type"
    assert f'<option value="{raised_bed}">' in form_page.text
    assert form_page.context["cancel_url"] == area.get_absolute_url() + "?archived=1"
    payload = {
        "submission_key": str(uuid4()),
        "expected_updated_at": area.updated_at.isoformat(),
        "name": area.name,
        "parent": parent.pk,
        "area_type": raised_bed,
        "length_cm": 180,
        "width_cm": 90,
        "description": area.description,
        "notes": area.notes,
    }
    for _attempt in range(2):
        saved = client.post(path, payload)
        assert saved.status_code == 302
        assert saved.url == area.get_absolute_url() + "?archived=1"
    area.refresh_from_db()
    assert area.area_type == raised_bed
    assert area.parent_id == parent.pk
    assert (area.length_cm, area.width_cm) == (180, 90)
    assert area.description == payload["description"]
    assert area.notes == payload["notes"]
    assert area.photos.get().pk == photo_id
    assert GrowingArea.objects.count() == 2
