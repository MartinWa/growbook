"""Private, translated native forms and bounded retry-safe HTMX feedback."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import OperationalError
from django.test import Client
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from economy.models import Allocation, EconomySubmission, Expense, Sale
from economy.services import (
    create_allocation,
    create_expense,
    create_sale,
    edit_allocation,
    edit_expense,
)
from garden.services import (
    archive_variety,
    create_crop,
    create_planting,
    create_variety,
    edit_cycle,
    edit_planting,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def calendar(monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: date(2026, 5, 9))


@pytest.fixture
def signed_in(client, user):
    client.force_login(user)
    client.cookies["django_language"] = "en"
    return client


@pytest.fixture
def planting(user):
    crop = create_crop(actor=user, name="Tomato").record
    variety = create_variety(actor=user, crop=crop, name="Golden").record
    return create_planting(
        actor=user, crop=crop, variety=variety, start_year=2026, quantity=50
    ).record


def expense_payload(**values):
    return {
        "submission_key": str(uuid4()),
        "description": "Synthetic soil",
        "purchased_on": "2026-05-09",
        "total_price": "100.10",
        "purchased_quantity": "10",
        "unit": "items",
        **values,
    }


def test_new_expense_defaults_to_today_and_saves_the_purchase_date_s_year(signed_in):
    path = reverse("economy:expense-create")
    page = signed_in.get(path, {"year": 2028})
    assert page.context["form"]["purchased_on"].value() == date(2026, 5, 9)
    saved = signed_in.post(path, expense_payload())
    assert saved.status_code == 302 and Expense.objects.get().purchase_year == 2026
    assert signed_in.get(saved.url).status_code == 200
    backdated = signed_in.post(path, expense_payload(purchased_on="2027-01-01"))
    assert backdated.status_code == 302 and "year=2027" in backdated.url
    assert Expense.objects.count() == 2
    assert Expense.objects.order_by("pk").last().purchase_year == 2027


@pytest.mark.parametrize("language", ["en", "sv"])
def test_economy_pages_render_translated_navigation_filters_empty_states_and_currency(
    signed_in, language
):
    signed_in.cookies["django_language"] = language
    response = signed_in.get(reverse("economy:list"))
    assert response.status_code == 200 and response.context["year"] == 2026
    assert ("Economy" if language == "en" else "Ekonomi") in response.content.decode()
    assert b"SEK" in response.content and b"data-currency-form" not in response.content
    records = signed_in.get(reverse("economy:records"))
    assert records.status_code == 200 and b"<search>" in records.content
    assert records.context["year"] == 2026 and records.context["expense_page"].number == 1
    assert "no-store" in response["Cache-Control"]
    assert "unsafe-eval" not in response["Content-Security-Policy"]


def test_private_routes_and_htmx_sessions_require_authentication(client, user, planting):
    expense = create_expense(actor=user, description="Supplies", total_price="10").record
    paths = [
        reverse("economy:list"),
        reverse("economy:records"),
        reverse("economy:cost-per-plant"),
        reverse("economy:expense-create"),
        expense.get_absolute_url(),
        reverse("economy:expense-edit", args=[expense.pk]),
        reverse("economy:expense-delete", args=[expense.pk]),
        reverse("economy:allocation-create", args=[expense.pk]),
        reverse("economy:sale-create"),
    ]
    for path in paths:
        assert client.get(path).status_code == 302
        assert client.post(path, {}, HTTP_HX_REQUEST="true").status_code == 401
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(user)
    assert csrf.post(reverse("economy:expense-create"), expense_payload()).status_code == 403


def test_expense_htmx_retry_is_identical_and_changed_keys_are_rejected(signed_in):
    path = reverse("economy:expense-create")
    payload = expense_payload()
    for _ in range(2):
        response = signed_in.post(path, payload, HTTP_HX_REQUEST="true")
        assert (
            response.status_code == 204
            and response["X-Growbook-Saved-Key"] == payload["submission_key"]
        )
        assert response["HX-Redirect"].startswith("/economy/expenses/")
    assert Expense.objects.count() == EconomySubmission.objects.count() == 1
    response = signed_in.post(path, {**payload, "total_price": "101"}, HTTP_HX_REQUEST="true")
    assert response.status_code == 409 and b"different content" in response.content
    assert b'id="economy-form"' not in response.content


def test_failed_and_stale_saves_keep_native_values_and_use_error_only_htmx_responses(
    signed_in, user, monkeypatch
):
    path = reverse("economy:expense-create")
    payload = expense_payload(total_price="invalid")
    error = signed_in.post(path, payload)
    assert (
        error.status_code == 422
        and error.context["form"]["description"].value() == payload["description"]
    )
    assert (
        b'aria-invalid="true"' in error.content
        and b'aria-describedby="id_total_price_errors"' in error.content
    )
    expense = create_expense(actor=user, description="Soil", total_price="100").record
    old = expense.updated_at
    edit_expense(
        actor=user,
        pk=expense.pk,
        expected_updated_at=old,
        description="Saved correction",
        total_price="100",
    )
    response = signed_in.post(
        reverse("economy:expense-edit", args=[expense.pk]),
        expense_payload(expected_updated_at=old.isoformat(), description="Unsaved correction"),
    )
    assert (
        response.status_code == 409
        and response.context["form"]["description"].value() == "Unsaved correction"
    )
    from economy import services

    def unavailable(**kwargs):
        raise OperationalError("Synthetic contention")

    monkeypatch.setattr(services, "create_expense", unavailable)
    failure = signed_in.post(path, expense_payload(), HTTP_HX_REQUEST="true")
    assert failure.status_code == 503 and b"retry the same submission" in failure.content
    assert b'id="economy-form"' not in failure.content


def test_allocation_native_forms_reserve_pending_cost_and_offer_later_editing(
    signed_in, user, planting
):
    expense = create_expense(actor=user, description="Soil", total_price="100").record
    path = reverse("economy:allocation-create", args=[expense.pk])
    payload = {
        "submission_key": str(uuid4()),
        "expected_expense_updated_at": expense.updated_at.isoformat(),
        "growing_year": 2026,
        "share": "60",
        "scope": "selected",
        "method": "plants",
        "cycle_ids": [str(planting.cycle_id)],
    }
    saved = signed_in.post(path, payload)
    assert saved.status_code == 302 and Allocation.objects.get().amount_cents == 6000
    assert signed_in.get(saved.url).status_code == 200
    expense.refresh_from_db()
    over = signed_in.post(
        path,
        {
            **payload,
            "submission_key": str(uuid4()),
            "expected_expense_updated_at": expense.updated_at.isoformat(),
            "share": "40.01",
        },
    )
    assert over.status_code == 422 and b"available balance" in over.content
    allocation = Allocation.objects.get()
    edit_path = reverse("economy:allocation-edit", args=[expense.pk, allocation.pk])
    edited = signed_in.post(
        edit_path,
        {
            **payload,
            "submission_key": str(uuid4()),
            "expected_updated_at": allocation.updated_at.isoformat(),
            "expected_expense_updated_at": expense.updated_at.isoformat(),
            "share": "75",
        },
    )
    assert edited.status_code == 302 and Allocation.objects.get().amount_cents == 7500
    filtered = signed_in.get(reverse("economy:records"), {"year": 2026, "cycle": planting.cycle_id})
    assert filtered.context["totals"]["growing"] == Decimal("75")
    detail = signed_in.get(
        reverse("economy:cost-per-plant-cycle", args=[planting.cycle_id]), {"year": 2026}
    )
    assert detail.context["row"]["per_plant"] == Decimal("1.50")


def test_stale_allocation_edits_conflict_and_preserve_entered_values(signed_in, user, planting):
    expense = create_expense(actor=user, description="Soil", total_price="100").record
    allocation = create_allocation(
        actor=user,
        expense=expense,
        expected_expense_updated_at=expense.updated_at,
        growing_year=2026,
        amount="20",
        cycle_ids=[planting.cycle_id],
    ).record
    expense.refresh_from_db()
    edit_allocation(
        actor=user,
        pk=allocation.pk,
        expected_updated_at=allocation.updated_at,
        expected_expense_updated_at=expense.updated_at,
        expense=expense,
        growing_year=2026,
        amount="25",
        cycle_ids=[planting.cycle_id],
    )
    expense.refresh_from_db()
    stale = signed_in.post(
        reverse("economy:allocation-edit", args=[expense.pk, allocation.pk]),
        {
            "submission_key": str(uuid4()),
            "expected_updated_at": allocation.updated_at.isoformat(),
            "expected_expense_updated_at": expense.updated_at.isoformat(),
            "growing_year": 2026,
            "share": "40",
            "scope": "selected",
            "method": "plants",
            "cycle_ids": [str(planting.cycle_id)],
        },
    )
    assert stale.status_code == 409
    assert stale.context["form"].cleaned_data["share"] == Decimal("40")
    assert Allocation.objects.get(pk=allocation.pk).amount_cents == 2500


def test_sale_source_is_derived_and_localized_decimal_inputs_are_exact(signed_in, planting):
    signed_in.cookies["django_language"] = "sv"
    saved = signed_in.post(
        reverse("economy:sale-create"),
        {
            "submission_key": str(uuid4()),
            "description": "Synthetic plants",
            "sold_on": "2026-05-09",
            "quantity": "3",
            "unit_price": "12,50",
            "cycle": str(planting.cycle_id),
        },
    )
    assert saved.status_code == 302 and Sale.objects.get().total_price == Decimal("37.50")
    assert Sale.objects.get().variety_id == planting.variety_id
    response = signed_in.get(saved.url)
    assert response.status_code == 200 and response.context["totals"]["income"] == Decimal("37.50")


def test_cycle_sale_links_prefill_a_known_variety_including_existing_archived_sources(
    signed_in, user, planting
):
    archive_variety(actor=user, pk=planting.variety_id)
    response = signed_in.get(reverse("economy:sale-create"), {"cycle": planting.cycle_id})
    assert response.context["form"]["variety"].value() == planting.variety_id
    assert "Golden (Tomato)" in response.content.decode()
    choices = {
        str(key): str(label) for key, label in response.context["form"].fields["variety"].choices
    }
    assert choices[str(planting.variety_id)] == "Golden (Tomato)"


def test_invalid_filters_show_errors_without_misleading_zero_totals(signed_in):
    response = signed_in.get(reverse("economy:records"), {"year": "invalid"})
    assert response.status_code == 200 and response.context["form"].errors
    assert "expense_page" not in response.context
    response = signed_in.get(reverse("economy:records"), {"year": 2026, "cycle": uuid4()})
    assert response.context["form"].errors


def test_deletion_requires_confirmation_and_a_current_version_for_every_member(
    signed_in, user, other_user
):
    expense = create_expense(
        actor=user,
        description="Equipment",
        total_price="900",
        category="equipment",
        useful_life_years=3,
    ).record
    signed_in.force_login(other_user)
    path = reverse("economy:expense-delete", args=[expense.pk])
    assert signed_in.get(path).status_code == 200
    payload = {"expected_updated_at": expense.updated_at.isoformat()}
    assert signed_in.post(path, payload).status_code == 422 and Expense.objects.exists()
    response = signed_in.post(path, {**payload, "confirm": "on"})
    assert response.status_code == 302 and not Expense.objects.exists()
    assert signed_in.get(reverse("economy:list"), {"year": 2028}).context["totals"]["growing"] == 0


def test_currency_label_is_configuration_and_not_a_per_user_route(signed_in, settings):
    assert settings.CURRENCY_LABEL == "SEK"
    page = signed_in.get(reverse("economy:list"))
    assert page.context["currency_label"] == "SEK" and b"SEK" in page.content
    settings.CURRENCY_LABEL = "EUR"
    page = signed_in.get(reverse("economy:list"))
    assert page.context["currency_label"] == "EUR" and b"EUR" in page.content
    assert b"SEK" not in page.content
    with pytest.raises(NoReverseMatch):
        reverse("set_currency")
    assert signed_in.post("/accounts/currency/", {"currency": "EUR"}).status_code == 404


def test_planting_links_open_the_cost_per_plant_view_without_inline_cost_summaries(
    signed_in, planting
):
    page = signed_in.get(planting.get_absolute_url())
    target = f"/economy/cost-per-plant/{planting.cycle_id}/?year=2026".encode()
    assert target in page.content and b"Economy for this cycle" in page.content
    assert b"Estimated cost per plant" not in page.content
    plantings = signed_in.get(reverse("garden:planting-list")).content
    assert plantings.count(b'href="/economy/"') == 1
    economy = signed_in.get(reverse("economy:list")).content
    assert b'href="/economy/" aria-current="page"' in economy
    assert b"data-currency-form" not in economy


def test_allocation_cycle_picker_follows_the_growing_year_and_keeps_completed_cycles(
    signed_in, user, planting
):
    other = create_planting(actor=user, crop=planting.crop, start_year=2027, quantity=10).record
    planting.cycle.refresh_from_db()
    picker_cycle = planting.cycle
    edit_cycle(
        actor=user,
        pk=picker_cycle.pk,
        status="completed",
        completed_on=date(2026, 4, 1),
        expected_updated_at=picker_cycle.updated_at,
    )
    expense = create_expense(actor=user, description="Soil", total_price="100").record
    path = reverse("economy:allocation-create", args=[expense.pk])
    first = signed_in.get(path, {"year": 2026})
    choices = dict(first.context["form"].fields["cycle_ids"].choices)
    assert str(planting.cycle_id) in choices and str(other.cycle_id) not in choices
    second = signed_in.get(path, {"year": 2027})
    choices = dict(second.context["form"].fields["cycle_ids"].choices)
    assert str(other.cycle_id) in choices and str(planting.cycle_id) not in choices
    fragment = signed_in.get(
        reverse("economy:allocation-cycles", args=[expense.pk]), {"growing_year": 2027}
    )
    assert fragment.status_code == 200
    assert str(other.cycle_id).encode() in fragment.content
    assert str(planting.cycle_id).encode() not in fragment.content


def test_allocate_form_shows_the_purchase_year_and_current_share(signed_in, user, planting):
    expense = create_expense(actor=user, description="Soil", total_price="100").record
    allocation = create_allocation(
        actor=user,
        expense=expense,
        expected_expense_updated_at=expense.updated_at,
        growing_year=2026,
        amount="60",
        cycle_ids=[planting.cycle_id],
    ).record
    expense.refresh_from_db()
    page = signed_in.get(reverse("economy:allocation-create", args=[expense.pk]), {"year": 2026})
    assert page.context["purchase_year"] == 2026
    assert page.context["form"]["share"].value() == Decimal("100")
    edited = signed_in.get(reverse("economy:allocation-edit", args=[expense.pk, allocation.pk]))
    assert edited.context["form"]["share"].value() == Decimal("60")


def test_counted_purchase_allocates_by_whole_purchased_units(signed_in, user, planting):
    expense = create_expense(
        actor=user,
        description="Pots",
        total_price="250",
        purchased_quantity="50",
        unit="items",
    ).record
    path = reverse("economy:allocation-create", args=[expense.pk])
    page = signed_in.get(path, {"year": 2026})
    form = page.context["form"]
    assert "quantity_used" in form.fields and "share" not in form.fields
    assert form["quantity_used"].value() == Decimal("50")
    attrs = form.fields["quantity_used"].widget.attrs
    assert attrs["max"] == "50.000" and attrs["step"] == "1" and attrs["min"] == 0
    assert page.context["quantity_mode"] is True
    assert page.context["share_percent"] == Decimal("100.0")

    payload = {
        "submission_key": str(uuid4()),
        "expected_expense_updated_at": expense.updated_at.isoformat(),
        "growing_year": 2026,
        "quantity_used": "16",
        "scope": "selected",
        "method": "plants",
        "cycle_ids": [str(planting.cycle_id)],
    }
    saved = signed_in.post(path, payload)
    assert saved.status_code == 302
    allocation = Allocation.objects.get()
    assert allocation.amount_cents == 8000

    expense.refresh_from_db()
    edited = signed_in.get(
        reverse("economy:allocation-edit", args=[expense.pk, allocation.pk]),
        {"year": 2026},
    )
    assert edited.context["form"]["quantity_used"].value() == Decimal("16")
    whole = signed_in.post(
        path,
        {
            **payload,
            "submission_key": str(uuid4()),
            "expected_expense_updated_at": expense.updated_at.isoformat(),
            "quantity_used": "16.5",
        },
    )
    assert whole.status_code == 422 and b"whole number" in whole.content

    preview = signed_in.post(
        reverse("economy:allocation-preview", args=[expense.pk]),
        {**payload, "quantity_used": "10"},
    )
    assert preview.status_code == 200 and preview.context["amount"] == Decimal("50.00")


def test_measured_purchases_step_by_percentage_and_periodized_equipment_keeps_shares(
    signed_in, user, planting
):
    measured = create_expense(
        actor=user,
        description="Compost",
        total_price="100",
        purchased_quantity="2",
        unit="kilograms",
    ).record
    page = signed_in.get(reverse("economy:allocation-create", args=[measured.pk]), {"year": 2026})
    assert page.context["form"].fields["quantity_used"].widget.attrs["step"] == "0.020"

    equipment = create_expense(
        actor=user,
        description="Greenhouse",
        total_price="900",
        category="equipment",
        purchased_quantity="1",
        unit="items",
        useful_life_years=3,
    ).record
    page = signed_in.get(reverse("economy:allocation-create", args=[equipment.pk]), {"year": 2026})
    assert "share" in page.context["form"].fields
    assert "quantity_used" not in page.context["form"].fields
    assert page.context["form"]["share"].value() == Decimal("100")


def test_expense_form_requires_the_purchased_quantity_and_unit(signed_in):
    path = reverse("economy:expense-create")
    response = signed_in.post(path, expense_payload(purchased_quantity="", unit=""))
    assert response.status_code == 422
    assert set(response.context["form"].errors) == {"purchased_quantity", "unit"}
    assert not Expense.objects.exists()
    saved = signed_in.post(path, expense_payload())
    assert saved.status_code == 302
    expense = Expense.objects.get()
    assert expense.purchased_quantity == Decimal("10.000") and expense.unit == "items"


def test_allocation_preview_reports_the_split_without_saving(signed_in, user, planting):
    expense = create_expense(actor=user, description="Soil", total_price="100").record
    path = reverse("economy:allocation-preview", args=[expense.pk])
    payload = {
        "growing_year": "2026",
        "share": "60",
        "scope": "selected",
        "method": "plants",
        "cycle_ids": [str(planting.cycle_id)],
    }
    response = signed_in.post(path, payload)
    assert response.status_code == 200
    estimate = response.context["estimate"]
    assert not estimate.pending and estimate.groups[0].cents == 6000
    assert response.context["amount"] == Decimal("60.00")
    assert response.context["available"] == Decimal("100.00")
    assert response.context["remaining"] == Decimal("40.00")
    assert b"60.00" in response.content
    missing = signed_in.post(path, {**payload, "share": ""})
    assert missing.context["state"] == "incomplete" and b"preview the split" in missing.content
    assert not Allocation.objects.exists()


def test_landing_links_to_records_while_records_paginate_and_keep_the_year(
    signed_in, user, planting
):
    for index in range(27):
        create_expense(
            actor=user,
            description=f"Synthetic purchase {index}",
            total_price="1",
            purchased_on=date(2026, 5, 9),
        )
    landing_page = signed_in.get(reverse("economy:list"))
    assert landing_page.context["has_records"]
    assert (
        f'href="{reverse("economy:records")}?all=1&amp;year=2026"' in landing_page.content.decode()
    )
    first = signed_in.get(reverse("economy:records"), {"year": 2026})
    assert first.context["expense_page"].paginator.num_pages == 2
    assert len(first.context["expense_page"].object_list) == 25
    second = signed_in.get(reverse("economy:records"), {"year": 2026, "page_expenses": 2})
    assert len(second.context["expense_page"].object_list) == 2
    assert "year=2026" in second.context["expense_previous_url"]


def test_economy_toolbar_keeps_a_year_reachable_from_all_years(signed_in, user):
    create_expense(
        actor=user, description="Synthetic twine", total_price="8", purchased_on=date(2026, 5, 9)
    )
    page = signed_in.get(reverse("economy:list"), {"all_years": 1})
    assert page.context["anchor_year"] == timezone.localdate().year
    assert page.context["year_url"] == f"{reverse('economy:list')}?year={timezone.localdate().year}"
    assert page.context["previous_url"] and page.context["next_url"]
    body = page.content.decode()
    assert f'href="{page.context["all_years_url"]}" aria-current="page"' in body
    previous = signed_in.get(page.context["previous_url"])
    assert previous.context["year"] == timezone.localdate().year - 1
    records = signed_in.get(reverse("economy:records"), {"year": 2026})
    assert records.context["previous_url"] == f"{reverse('economy:records')}?year=2025"
    assert records.context["year_url"] == f"{reverse('economy:records')}?year=2026"


def test_all_years_aggregates_purchases_and_cuts_future_equipment_shares(signed_in, user):
    create_expense(
        actor=user,
        description="Synthetic seed trays",
        total_price="900",
        category="equipment",
        useful_life_years=3,
    )
    create_expense(
        actor=user,
        description="Past purchase",
        total_price="50",
        purchased_on=date(2025, 5, 9),
    )
    every_year = signed_in.get(reverse("economy:list"), {"all_years": 1})
    assert every_year.context["all_years"] and every_year.context["year"] is None
    assert every_year.context["totals"]["spent"] == Decimal("950.00")
    assert every_year.context["totals"]["growing"] == Decimal("350.00")
    assert signed_in.get(reverse("economy:list"), {"year": 2028}).context["totals"][
        "growing"
    ] == Decimal("300.00")
    index = signed_in.get(reverse("economy:cost-per-plant"), {"all_years": 1})
    assert index.context["all_years"]
    assert index.status_code == 200


def test_cost_per_plant_indexes_cycles_and_shows_each_reaching_allocation(
    signed_in, user, planting
):
    expense = create_expense(actor=user, description="Soil", total_price="100").record
    create_allocation(
        actor=user,
        expense=expense,
        expected_expense_updated_at=expense.updated_at,
        growing_year=2026,
        amount="50",
        cycle_ids=[planting.cycle_id],
    )
    index = signed_in.get(reverse("economy:cost-per-plant"), {"year": 2026})
    row = next(
        item for item in index.context["cycle_rows"] if item["cycle"].pk == planting.cycle_id
    )
    assert row["per_plant"] == Decimal("1.00") and row["cost"] == Decimal("50.00")
    detail = signed_in.get(
        reverse("economy:cost-per-plant-cycle", args=[planting.cycle_id]), {"year": 2026}
    )
    assert detail.context["row"]["per_plant"] == Decimal("1.00")
    assert detail.context["allocations"][0]["amount"] == Decimal("50.00")
    assert detail.context["totals"]["growing"] == Decimal("50.00")


def test_landing_groups_pending_splits_by_growing_year_and_lists_unallocated_balances(
    signed_in, user, planting
):
    edit = planting
    edit.refresh_from_db()
    edit_planting(
        actor=user,
        pk=edit.pk,
        expected_updated_at=edit.updated_at,
        crop=edit.crop_id,
        variety=edit.variety_id,
        start_year=2026,
        quantity=None,
    )
    expense = create_expense(actor=user, description="Reserved soil", total_price="100").record
    create_allocation(
        actor=user,
        expense=expense,
        expected_expense_updated_at=expense.updated_at,
        growing_year=2026,
        amount="40",
        cycle_ids=[planting.cycle_id],
    )
    other = create_expense(actor=user, description="Open balance", total_price="30").record
    page = signed_in.get(reverse("economy:list"))
    groups = page.context["pending_groups"]
    assert [group["year"] for group in groups] == [2026]
    assert page.context["unallocated_purchases"]
    assert b"Reserved soil" in page.content and b"Open balance" in page.content
    assert other.pk in {row["expense"].pk for row in page.context["unallocated_purchases"]}


def test_edit_preview_excludes_the_saved_allocation_it_replaces(signed_in, user, planting):
    expense = create_expense(actor=user, description="Soil", total_price="100").record
    allocation = create_allocation(
        actor=user,
        expense=expense,
        expected_expense_updated_at=expense.updated_at,
        growing_year=2026,
        amount="60",
        cycle_ids=[planting.cycle_id],
    ).record
    payload = {
        "growing_year": "2026",
        "share": "60",
        "scope": "selected",
        "method": "plants",
        "cycle_ids": [str(planting.cycle_id)],
    }
    create = signed_in.post(reverse("economy:allocation-preview", args=[expense.pk]), payload)
    assert create.context["available"] == Decimal("40.00")
    assert create.context["over"]
    edit = signed_in.post(
        reverse("economy:allocation-preview", args=[expense.pk]) + f"?allocation={allocation.pk}",
        payload,
    )
    assert edit.context["available"] == Decimal("100.00")
    assert edit.context["remaining"] == Decimal("40.00")
    assert not edit.context["over"]


def test_unallocated_links_open_the_year_whose_balance_is_shown(signed_in, user):
    expense = create_expense(
        actor=user,
        description="Synthetic seed trays",
        total_price="100",
        purchased_on=date(2025, 5, 9),
        category="equipment",
        useful_life_years=2,
    ).record
    create_allocation(
        actor=user,
        expense=expense,
        expected_expense_updated_at=expense.updated_at,
        growing_year=2025,
        amount="50",
        scope="year",
    )
    page = signed_in.get(reverse("economy:list"), {"year": 2026})
    row = next(
        item for item in page.context["unallocated_purchases"] if item["expense"].pk == expense.pk
    )
    assert row["available"] == Decimal("50.00") and row["detail_year"] == 2026
    target = f"/economy/expenses/{expense.pk}/?year=2026"
    assert target.encode() in page.content
    detail = signed_in.get(target)
    assert detail.status_code == 200
    assert detail.context["available"] == Decimal("50.00")

    records = signed_in.get(reverse("economy:records"), {"year": 2026})
    expense_row = next(
        item for item in records.context["expense_rows"] if item["expense"].pk == expense.pk
    )
    assert expense_row["detail_year"] == 2026

    every_year = signed_in.get(reverse("economy:list"), {"all_years": 1})
    assert b"year=None" not in every_year.content
    fallback = next(
        item["detail_year"]
        for item in every_year.context["unallocated_purchases"]
        if item["expense"].pk == expense.pk
    )
    assert fallback == 2026
    assert signed_in.get(f"/economy/expenses/{expense.pk}/?year={fallback}").status_code == 200


def test_saved_sale_links_to_the_records_page_that_contains_it(signed_in, user, planting):
    for index in range(26):
        create_sale(
            actor=user,
            description=f"Synthetic sale {index}",
            sold_on=date(2026, 5, 9),
            quantity=1,
            unit_price="1",
            cycle=planting.cycle_id,
        )
    saved = signed_in.post(
        reverse("economy:sale-create"),
        {
            "submission_key": str(uuid4()),
            "description": "Synthetic older sale",
            "sold_on": "2026-01-01",
            "quantity": "1",
            "unit_price": "1",
            "cycle": str(planting.cycle_id),
        },
    )
    assert saved.status_code == 302
    sale = Sale.objects.get(description="Synthetic older sale")
    assert "page_income=2" in saved.url and "year=2026" in saved.url
    assert saved.url.endswith(f"#sale-{sale.pk}")
    records = signed_in.get(saved.url)
    assert records.status_code == 200
    assert f'id="sale-{sale.pk}"'.encode() in records.content
