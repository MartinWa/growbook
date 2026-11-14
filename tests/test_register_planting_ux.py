"""Grouping preserves normalized names, archive context and independent batches."""

from datetime import date
from urllib.parse import parse_qs, quote, urlsplit
from uuid import uuid4

import pytest
from django.urls import reverse
from django.utils import timezone

from diary.services import create_entry
from garden import queries
from garden.models import GrowingArea
from garden.queries import area_tree
from garden.services import (
    archive_area,
    archive_crop,
    archive_variety,
    create_area,
    create_crop,
    create_planting,
    create_variety,
    delete_area,
    edit_planting,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def varieties(user):
    pea = create_crop(actor=user, name="Ärtor").record
    bean = create_crop(actor=user, name="Bean").record
    first = create_variety(actor=user, crop=pea, name="Åsa  Grön", description="Hidden note").record
    second = create_variety(actor=user, crop=bean, name="Åsa Grön").record
    archived = create_variety(actor=user, crop=pea, name="Old trial").record
    archive_variety(actor=user, pk=archived.pk)
    archive_crop(actor=user, pk=pea.pk)
    return pea, bean, first, second, archived


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize(
    ("search", "indices"),
    [
        ("A\u030asa\t GRO\u0308N", [3, 2]),
        ("a\u0308RTOR", [2]),
        ("Hidden note", []),
        ("Asa", []),
        ("%", []),
        ("_", []),
        ("   ", [3, 2]),
    ],
)
def test_variety_search_uses_literal_normalized_name_keys(
    client, user, varieties, partial, search, indices
):
    client.force_login(user)
    headers = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "register-results"} if partial else {}
    response = client.get(reverse("garden:all-crops"), {"q": search}, **headers)
    assert response.status_code == 200
    assert [record.pk for record in response.context["records"]] == [
        varieties[i].pk for i in indices
    ]
    groups = response.context["variety_groups"]
    assert sum(len(group["records"]) for group in groups) == len(indices)
    assert all(group["records"] for group in groups)
    assert "<html" not in response.text if partial else "<!doctype" in response.text
    assert "no-store" in response["Cache-Control"]
    assert "unsafe-eval" not in response["Content-Security-Policy"]
    if 2 in indices:
        assert varieties[0].name in response.text and "archive-label" in response.text


def test_variety_archive_clear_and_empty_results_keep_filter_choices(client, user, varieties):
    client.force_login(user)
    path = reverse("garden:all-crops")
    response = client.get(path, {"q": "ÄRTOR", "archived": "1"})
    assert [record.pk for record in response.context["records"]] == [
        varieties[4].pk,
        varieties[2].pk,
    ]
    hide = response.context["hide_archived_url"]
    assert parse_qs(urlsplit(hide).query) == {"q": ["ÄRTOR"]}
    assert [record.pk for record in client.get(hide).context["records"]] == [varieties[2].pk]
    response = client.get(path, {"q": "Missing", "archived": "1"})
    clear = response.context["empty_action_url"]
    assert clear == path + "?archived=1"
    assert len(client.get(clear).context["records"]) == 3
    create_link = urlsplit(response.context["create_url"])
    assert create_link.path == reverse("garden:variety-create")
    assert parse_qs(create_link.query) == {"next": [path + "?q=Missing&archived=1"]}
    client.cookies["django_language"] = "en"
    assert "No crops or varieties match this search" in client.get(path, {"q": "Missing"}).text


@pytest.mark.parametrize("current, toggle, count", [("", "1", 2), ("1", "0", 1)])
def test_archive_submit_preserves_unapplied_search_without_javascript(
    client, user, varieties, current, toggle, count
):
    client.force_login(user)
    path = reverse("garden:all-crops")
    response = client.get(path, {"q": "ÄRTOR", "archived": current, "archive_toggle": toggle})
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.url).query)
    assert query["q"] == ["ÄRTOR"]
    assert query.get("archived", []) == (["1"] if toggle == "1" else [])
    assert "archive_toggle" not in query
    assert len(client.get(response.url).context["records"]) == count


def test_combined_register_keeps_empty_crops_and_crop_maintenance(client, user):
    crop = create_crop(actor=user, name="Pea", notes="Crop note").record
    other = create_crop(actor=user, name="Bean").record
    variety = create_variety(actor=user, crop=crop, name="Trial").record
    unrelated = create_variety(actor=user, crop=other, name="Other trial").record
    archive_variety(actor=user, pk=variety.pk)
    empty = create_crop(actor=user, name="Empty crop").record
    client.force_login(user)
    path = reverse("garden:all-crops")
    response = client.get(reverse("garden:crop-list"), {"q": "Pea", "archived": "1"})
    assert response.status_code == 302
    assert parse_qs(urlsplit(response.url).query) == {"q": ["Pea"], "archived": ["1"]}
    assert urlsplit(response.url).path == path
    response = client.get(path)
    groups = {group["crop"].pk: group["records"] for group in response.context["variety_groups"]}
    assert set(groups) == {crop.pk, other.pk, empty.pk}
    assert groups[crop.pk] == []
    assert groups[other.pk] == [unrelated]
    assert groups[empty.pk] == []
    assert 'class="crop-maintenance"' in response.text
    crop_link = crop.get_absolute_url() + "?next=" + quote(path, safe="/")
    assert f'href="{crop_link}"' in response.text
    searched = client.get(path, {"q": "Empty"})
    assert searched.context["has_records"]
    assert len(searched.context["variety_groups"]) == 1
    assert searched.context["variety_groups"][0]["crop"] == empty
    assert not searched.context["records"]
    for headers in ({}, {"HTTP_HX_REQUEST": "true"}):
        text = client.get(crop.get_absolute_url(), **headers).text
        assert f'href="{variety.get_absolute_url()}"' in text
        assert unrelated.name not in text
        assert text.index("Trial") < text.index("Crop note")
        assert "archive-label" in text
    client.cookies["django_language"] = "en"
    assert "No varieties for this crop yet." in client.get(empty.get_absolute_url()).text


def test_area_register_keeps_only_needed_archived_ancestors_from_one_read(
    client, user, django_assert_num_queries
):
    root = create_area(actor=user, name="Archived root").record
    middle = create_area(actor=user, name="Archived middle", parent=root).record
    leaf = create_area(actor=user, name="Active leaf", parent=middle).record
    unrelated = create_area(actor=user, name="Hidden archive").record
    for area in (root, middle, unrelated):
        archive_area(actor=user, pk=area.pk)
    with django_assert_num_queries(1):
        tree = area_tree(visible_ids={leaf.pk})
    assert [event["node"].area.pk for event in tree["events"] if not event["closing"]] == [
        root.pk,
        middle.pk,
        leaf.pk,
    ]
    assert all(not event["node"].open for event in tree["events"])
    client.force_login(user)
    response = client.get(reverse("garden:area-list"))
    assert [record.pk for record in response.context["records"]] == [root.pk]
    assert unrelated.name not in response.text
    assert response.text.count('class="category-card area-card"') == 1
    assert f'data-area-id="{leaf.pk}"' not in response.text
    parent_page = client.get(root.get_absolute_url())
    assert [child.pk for child in parent_page.context["children"]] == [middle.pk]
    middle_page = client.get(middle.get_absolute_url())
    assert [child.pk for child in middle_page.context["children"]] == [leaf.pk]
    assert f'href="{leaf.get_absolute_url()}"' in middle_page.text
    assert unrelated.name in client.get(reverse("garden:area-list"), {"archived": "1"}).text


def test_area_tree_cuts_cycles_and_handles_depth_without_recursion():
    areas = [
        GrowingArea(id=i, parent_id=i - 1 if i > 1 else None, name=f"Area {i}", name_key=f"{i:04}")
        for i in range(1, 1201)
    ]
    areas.extend(
        [
            GrowingArea(id=1201, parent_id=1202, name="Cycle A", name_key="a"),
            GrowingArea(id=1202, parent_id=1201, name="Cycle B", name_key="b"),
        ]
    )
    tree = area_tree(areas=areas, visible_ids={1200, 1202})
    opening = [event["node"].area.pk for event in tree["events"] if not event["closing"]]
    assert opening == list(range(1, 1203))
    assert len(tree["events"]) == 2404
    assert [node.area.pk for node in tree["roots"]] == [1, 1201]


@pytest.fixture
def batches(user):
    crop = create_crop(actor=user, name="Pea").record
    variety = create_variety(actor=user, crop=crop, name="Green").record
    root = create_area(actor=user, name="Root").record
    unused = create_area(actor=user, name="Unused").record
    areas = {"root": root}
    records = []
    for label, area, selected, quantity in (
        ("Parent batch", "root", variety, None),
        ("Child zero", "child", variety, 0),
        ("Child batch", "child", variety, 7),
        ("Unknown batch", "grandchild", None, None),
        ("Unassigned", None, None, None),
    ):
        if area == "child" and area not in areas:
            areas[area] = create_area(actor=user, name="Bed", parent=root).record
        elif area == "grandchild":
            areas[area] = create_area(actor=user, name="Bed", parent=areas["child"]).record
        records.append(
            create_planting(
                actor=user,
                crop=crop,
                variety=selected,
                area=areas.get(area),
                start_year=2024,
                end_year=2026,
                lifespan="persistent",
                label=label,
                quantity=quantity,
            ).record
        )
    archive_area(actor=user, pk=root.pk)
    archive_variety(actor=user, pk=variety.pk)
    return crop, variety, root, areas["child"], areas["grandchild"], unused, records


@pytest.mark.parametrize("scope", ["planting", "crop", "variety", "area"])
@pytest.mark.parametrize("year", [None, 2023, 2024, 2026, 2027])
def test_grouped_histories_preserve_year_boundaries_and_exact_assignments(
    client, user, batches, scope, year
):
    crop, variety, root, child, grandchild, unused, records = batches
    client.force_login(user)
    pk = {"crop": crop.pk, "variety": variety.pk, "area": root.pk}.get(scope)
    path = reverse(f"garden:{scope}-history", args=[pk] if pk else [])
    response = client.get(path, {"year": year} if year else {})
    expected = records if year in (None, 2024, 2026) else []
    if scope == "variety":
        expected = [record for record in expected if record.variety_id]
    if scope == "area":
        expected = [record for record in expected if record.area_id]
    assert {record.pk for record in response.context["records"]} == {
        record.pk for record in expected
    }
    nodes = {
        event["node"].area.pk: event["node"]
        for event in response.context["area_events"]
        if not event["closing"]
    }
    if expected:
        assert unused.pk not in nodes
        assert nodes[root.pk].count == sum(record.area_id is not None for record in expected)
        assert nodes[root.pk].open == (scope == "variety")
        assert [record.pk for record in nodes[root.pk].records] == [records[0].pk]
        assert [record.pk for record in nodes[child.pk].records] == [records[1].pk, records[2].pk]
        assert nodes[child.pk].open == (scope == "variety")
        for record in expected:
            if not response.context["category_overview"]:
                assert response.text.count(f'id="details-cycle-{record.cycle_id}"') == 1
        assert len(response.context["cycle_groups"]) == len(expected)
        if scope != "variety":
            assert nodes[grandchild.pk].open
            fragment = client.get(records[3].get_absolute_url(), HTTP_HX_REQUEST="true")
            assert "Okänd sort" in fragment.text and "—</dd>" in fragment.text
        fragment = client.get(records[1].get_absolute_url(), HTTP_HX_REQUEST="true")
        assert "0</dd>" in fragment.text


def test_filtered_overview_opens_same_pair_batches_and_keeps_year_links(client, user, batches):
    crop, variety, root, child, grandchild, unused, records = batches
    client.force_login(user)
    response = client.get(
        reverse("garden:planting-status", args=["planned"]),
        {"year": 2026, "area": root.pk, "variety": variety.pk},
        HTTP_HX_REQUEST="true",
        HTTP_HX_TARGET="planting-results",
    )
    assert "<html" not in response.text
    nodes = [event["node"] for event in response.context["area_events"] if not event["closing"]]
    assert all(node.open for node in nodes)
    assert [node.area.pk for node in nodes] == [root.pk, child.pk]
    assert not response.context["unassigned_records"]
    assert records[1].cycle.get_absolute_url() in response.text
    assert "&amp;year=2026" in response.text
    details = client.get(records[1].get_absolute_url(), {"year": 2026}, HTTP_HX_REQUEST="true")
    assert f'href="{reverse("garden:area-history", args=[child.pk])}?year=2026"' in details.text
    unknown = client.get(reverse("garden:planting-list"), {"year": 2026, "unknown_variety": "on"})
    assert len(unknown.context["records"]) == 2
    assert len(unknown.context["unassigned_records"]) == 1


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize(
    "route",
    ["planting-list", "planting-history", "crop-history", "variety-history", "area-history"],
)
def test_planting_pages_reload_batches_when_an_assigned_area_disappears(
    client, user, monkeypatch, partial, route
):
    crop = create_crop(actor=user, name="Pea").record
    variety = create_variety(actor=user, crop=crop, name="Green").record
    root = create_area(actor=user, name="Garden").record
    old_area = create_area(actor=user, name="Old bed", parent=root).record
    new_area = create_area(actor=user, name="New bed", parent=root).record
    planting = create_planting(
        actor=user,
        crop=crop,
        variety=variety,
        area=old_area,
        start_year=2026,
        label="Moved batch",
        quantity=0,
    ).record
    create_planting(actor=user, crop=crop, variety=variety, area=new_area, start_year=2025)
    history = queries.planting_history
    reads = 0

    def remove_area_between_reads(**filters):
        nonlocal reads
        records = list(history(**filters))
        reads += 1
        if reads == 1:
            edit_planting(
                actor=user,
                pk=planting.pk,
                expected_updated_at=planting.updated_at,
                crop=crop,
                variety=variety,
                area=new_area,
                start_year=2026,
                label=planting.label,
                quantity=planting.quantity,
            )
            delete_area(actor=user, pk=old_area.pk, expected_updated_at=old_area.updated_at)
        return records

    client.force_login(user)
    scope = {"crop-history": crop.pk, "variety-history": variety.pk, "area-history": root.pk}.get(
        route
    )
    path = reverse(f"garden:{route}", args=[scope] if scope else [])
    # Import the routes before patching so other views retain their real query helper.
    monkeypatch.setattr(queries, "planting_history", remove_area_between_reads)
    headers = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "planting-results"} if partial else {}
    response = client.get(path, {"year": 2026}, **headers)

    assert response.status_code == 200
    assert reads == 2
    records = response.context["records"]
    assert [record.pk for record in records] == [planting.pk]
    assert records[0].area_id == new_area.pk and records[0].quantity == 0
    assert records[0].area_path == f"{root.name} / {new_area.name}"
    assert not response.context["unassigned_records"]
    nodes = {
        event["node"].area.pk: event["node"]
        for event in response.context["area_events"]
        if not event["closing"]
    }
    assert set(nodes) == {root.pk, new_area.pk}
    assert nodes[root.pk].count == 1
    assert [record.pk for record in nodes[new_area.pk].records] == [planting.pk]
    if response.context["category_overview"]:
        assert len(response.context["cycle_groups"]) == 1
    else:
        assert response.text.count(f'id="details-cycle-{planting.cycle_id}"') == 1


def test_register_search_fragments_require_an_active_session(client, user, varieties):
    path = reverse("garden:all-crops")
    headers = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "register-results"}
    assert client.get(path, {"q": "Åsa"}).status_code == 302
    response = client.get(path, {"q": "Åsa"}, **headers)
    assert response.status_code == 401 and "no-store" in response["Cache-Control"]
    client.force_login(user)
    user.is_active = False
    user.save(update_fields=["is_active"])
    assert client.get(path, {"q": "Åsa"}, **headers).status_code == 401


@pytest.mark.parametrize("partial", [False, True])
def test_invalid_variety_search_returns_retryable_field_feedback(client, user, partial):
    client.force_login(user)
    headers = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "register-results"} if partial else {}
    response = client.get(reverse("garden:all-crops"), {"q": "\x00", "archived": "1"}, **headers)
    assert response.status_code == 422
    assert response.context["form"].errors["q"]
    assert 'data-field-errors="q"' in response.text
    assert response.context["empty_action_url"] == reverse("garden:all-crops") + "?archived=1"


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("year", [2026, 2027])
def test_area_information_links_to_current_year_and_direct_plantings(
    client, user, monkeypatch, partial, year
):
    monkeypatch.setattr(timezone, "localdate", lambda: date(year, 11, 8))
    crop = create_crop(actor=user, name="Pea").record
    parent = create_area(actor=user, name="Parent context").record
    area = create_area(
        actor=user, name="Selected bed", parent=parent, area_type="Raised bed"
    ).record
    other = create_area(actor=user, name="Other bed").record
    current = create_planting(actor=user, crop=crop, area=area, start_year=year, quantity=0).record
    for place, start in ((area, year - 1), (area, year + 1), (other, year)):
        create_planting(actor=user, crop=crop, area=place, start_year=start)
    child = create_area(actor=user, name="Child section", parent=area).record
    persistent = create_planting(
        actor=user, crop=crop, area=child, start_year=year - 1, lifespan="persistent", end_year=year
    ).record
    client.force_login(user)
    response = client.get(area.get_absolute_url(), HTTP_HX_REQUEST="true" if partial else "false")
    assert response.status_code == 200
    assert parent.name in response.text
    assert "Raised bed" in response.text
    assert response.context["planting_year"] == year
    assert response.context["planting_count"] == 1
    assert len(response.context["plantings"]) == 3
    assert persistent not in response.context["plantings"]
    target = response.context["planting_url"]
    assert urlsplit(target).path == reverse("garden:planting-list")
    assert parse_qs(urlsplit(target).query) == {
        "year": [str(year)],
        "area": [str(area.pk)],
        "area_only": ["1"],
    }
    plantings = client.get(target)
    assert plantings.context["year"] == year
    assert plantings.context["form"]["area"].value() == str(area.pk)
    assert plantings.context["records"] == [current]
    all_years = client.get(plantings.context["all_years_url"])
    assert len(all_years.context["records"]) == 3
    assert persistent not in all_years.context["records"]
    previous_year = client.get(plantings.context["previous_url"])
    assert len(previous_year.context["records"]) == 1
    assert persistent not in previous_year.context["records"]
    inclusive = client.get(reverse("garden:planting-list"), {"year": year, "area": area.pk})
    assert set(inclusive.context["records"]) == {current, persistent}


@pytest.mark.parametrize("partial", [False, True])
def test_removing_area_filter_restores_descendants_and_preserves_other_filters(
    client, user, batches, partial
):
    crop, variety, root, child, grandchild, unused, records = batches
    client.force_login(user)
    headers = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "planting-results"} if partial else {}
    path = reverse("garden:planting-list")
    response = client.get(
        path, {"year": 2026, "crop": crop.pk, "area": child.pk, "area_only": "1"}, **headers
    )
    assert len(response.context["records"]) == 2
    area_filter = next(
        item for item in response.context["active_filters"] if item["value"] == "Root / Bed"
    )
    clear_query = parse_qs(urlsplit(area_filter["url"]).query)
    assert clear_query == {"year": ["2026"], "crop": [str(crop.pk)]}
    cleared = client.get(area_filter["url"], **headers)
    assert set(cleared.context["records"]) == set(records)
    assert not cleared.context["area_only"]
    clear_query["area"] = [str(child.pk)]
    selected = client.get(path, clear_query, **headers)
    assert set(selected.context["records"]) == set(records[1:4])
    assert not selected.context["area_only"]


def test_direct_area_modifier_without_an_area_is_removed_from_navigation(client, user, batches):
    client.force_login(user)
    response = client.get(reverse("garden:planting-list"), {"year": 2026, "area_only": "1"})
    assert not response.context["form"].cleaned_data["area_only"]
    for name in ("previous_url", "next_url", "all_years_url", "overview_url"):
        assert "area_only" not in parse_qs(urlsplit(response.context[name]).query)


@pytest.mark.parametrize("kind", ["area", "variety"])
@pytest.mark.parametrize("count", [0, 5, 6])
def test_register_observations_keep_older_entries_reachable(client, user, kind, count):
    if kind == "area":
        record = create_area(actor=user, name="Observation bed").record
    else:
        crop = create_crop(actor=user, name="Pea").record
        record = create_variety(actor=user, crop=crop, name="Green").record
    for day in range(1, count + 1):
        create_entry(
            author=user,
            date=date(2026, 5, day),
            body=f"Observation {day}",
            uploads=[],
            submission_key=uuid4(),
            links=[record],
        )
    client.force_login(user)
    client.cookies["django_language"] = "en"
    for headers in ({}, {"HTTP_HX_REQUEST": "true"}):
        response = client.get(record.get_absolute_url(), **headers)
        assert response.context["observation_count"] == count
        assert len(response.context["observations"]) == min(count, 5)
        assert f"Diary observations ({count})" in response.text
        assert ("View all observations" in response.text) == (count > 5)
        if count > 5:
            history = client.get(response.context["observations_url"])
            assert "Observation 1" in history.text


@pytest.mark.parametrize("partial", [False, True])
def test_variety_groups_show_one_private_thumbnail_per_record(
    client, user, image_upload, partial, django_assert_num_queries
):
    crop = create_crop(actor=user, name="Pea").record
    varieties = [
        create_variety(
            actor=user, crop=crop, name=name, uploads=[image_upload(), image_upload()]
        ).record
        for name in ("Green", "Yellow")
    ]
    create_variety(actor=user, crop=crop, name="No photo")
    client.force_login(user)
    headers = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "register-results"} if partial else {}
    response = client.get(reverse("garden:all-crops"), **headers)
    assert response.text.count('class="variety-thumbnail"') == 2
    with django_assert_num_queries(0):
        for record in response.context["records"]:
            photos = list(record.photos.all())
            if record.pk in {variety.pk for variety in varieties}:
                assert photos[0].thumbnail_url in response.text
                assert photos[1].thumbnail_url not in response.text
