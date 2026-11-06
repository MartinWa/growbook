"""Grouping preserves normalized names, archive context and independent batches."""

from urllib.parse import parse_qs, urlsplit

import pytest
from django.urls import reverse

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
    response = client.get(reverse("garden:variety-list"), {"q": search}, **headers)
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
    path = reverse("garden:variety-list")
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
    assert f'href="{reverse("garden:variety-create")}"' in response.text
    client.cookies["django_language"] = "en"
    assert "No varieties match this search" in client.get(path, {"q": "Missing"}).text


def test_crop_variety_count_and_links_include_archived_and_precede_notes(client, user):
    crop = create_crop(actor=user, name="Pea", notes="Crop note").record
    other = create_crop(actor=user, name="Bean").record
    variety = create_variety(actor=user, crop=crop, name="Trial").record
    unrelated = create_variety(actor=user, crop=other, name="Other trial").record
    archive_variety(actor=user, pk=variety.pk)
    empty = create_crop(actor=user, name="Empty crop").record
    client.force_login(user)
    response = client.get(reverse("garden:crop-list"))
    assert next(row for row in response.context["records"] if row.pk == crop.pk).variety_count == 1
    assert f'aria-label="Sorter: {crop.name}"' in response.text
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
    assert [record.pk for record in response.context["records"]] == [leaf.pk]
    assert response.text.count("data-structural") == 2
    assert unrelated.name not in response.text
    assert response.text.count('class="area-branch"') == 2
    assert response.text.count(f'data-area-id="{leaf.pk}"') == 1
    assert f'aria-label="Anteckningar: {leaf.name}"' in response.text
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
    child = create_area(actor=user, name="Bed", parent=root).record
    grandchild = create_area(actor=user, name="Bed", parent=child).record
    unused = create_area(actor=user, name="Unused").record
    records = []
    for label, area, selected, quantity in (
        ("Parent batch", root, variety, None),
        ("Child zero", child, variety, 0),
        ("Child batch", child, variety, 7),
        ("Unknown batch", grandchild, None, None),
        ("Unassigned", None, None, None),
    ):
        records.append(
            create_planting(
                actor=user,
                crop=crop,
                variety=selected,
                area=area,
                start_year=2024,
                end_year=2026,
                lifespan="persistent",
                label=label,
                quantity=quantity,
            ).record
        )
    archive_area(actor=user, pk=root.pk)
    archive_variety(actor=user, pk=variety.pk)
    return crop, variety, root, child, grandchild, unused, records


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
        assert "0</td>" in response.text and "—</td>" in response.text
        for record in expected:
            assert response.text.count(f'id="details-planting-{record.pk}"') == 1
        if scope != "variety":
            assert nodes[grandchild.pk].open
            assert "Okänd sort" in response.text


def test_filtered_overview_opens_same_pair_batches_and_keeps_year_links(client, user, batches):
    crop, variety, root, child, grandchild, unused, records = batches
    client.force_login(user)
    response = client.get(
        reverse("garden:planting-list"),
        {"year": 2026, "area": root.pk, "variety": variety.pk},
        HTTP_HX_REQUEST="true",
        HTTP_HX_TARGET="planting-results",
    )
    assert "<html" not in response.text
    nodes = [event["node"] for event in response.context["area_events"] if not event["closing"]]
    assert all(node.open for node in nodes)
    assert [node.area.pk for node in nodes] == [root.pk, child.pk]
    assert not response.context["unassigned_records"]
    assert f'href="{reverse("garden:area-history", args=[child.pk])}?year=2026"' in response.text
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

    monkeypatch.setattr(queries, "planting_history", remove_area_between_reads)
    client.force_login(user)
    scope = {"crop-history": crop.pk, "variety-history": variety.pk, "area-history": root.pk}.get(
        route
    )
    path = reverse(f"garden:{route}", args=[scope] if scope else [])
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
    assert response.text.count(f'id="details-planting-{planting.pk}"') == 1


def test_register_search_fragments_require_an_active_session(client, user, varieties):
    path = reverse("garden:variety-list")
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
    response = client.get(reverse("garden:variety-list"), {"q": "\x00", "archived": "1"}, **headers)
    assert response.status_code == 422
    assert response.context["form"].errors["q"]
    assert 'data-field-errors="q"' in response.text
    assert response.context["empty_action_url"] == reverse("garden:variety-list") + "?archived=1"
