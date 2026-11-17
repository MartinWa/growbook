"""Private, translated native forms and bounded retry-safe HTMX feedback."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import OperationalError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.currency import CURRENCY_COOKIE
from economy.models import Allocation, EconomySubmission, Expense, Sale
from economy.services import create_expense, edit_expense
from garden.services import archive_variety, create_crop, create_planting, create_variety

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
        **values,
    }


def test_new_expense_uses_current_year_independently_of_the_viewed_year(signed_in):
    path = reverse("economy:expense-create")
    page = signed_in.get(path, {"year": 2028})
    assert page.context["purchase_year"] == 2026
    assert page.context["form"]["purchased_on"].value() == date(2026, 5, 9)
    saved = signed_in.post(path, expense_payload())
    assert saved.status_code == 302 and Expense.objects.get().purchase_year == 2026
    assert signed_in.get(saved.url).status_code == 200
    invalid = signed_in.post(path, expense_payload(purchased_on="2027-01-01"))
    assert invalid.status_code == 422 and "purchased_on" in invalid.context["form"].errors
    assert Expense.objects.count() == 1


@pytest.mark.parametrize("language", ["en", "sv"])
def test_economy_pages_render_translated_navigation_filters_empty_states_and_currency(
    signed_in, language
):
    signed_in.cookies["django_language"] = language
    response = signed_in.get(reverse("economy:list"))
    assert response.status_code == 200 and response.context["year"] == 2026
    assert ("Economy" if language == "en" else "Ekonomi") in response.content.decode()
    assert b"<search>" in response.content and b"data-currency-form" in response.content
    assert (
        "no-store" in response["Cache-Control"]
        and "unsafe-eval" not in response["Content-Security-Policy"]
    )


def test_private_routes_and_htmx_sessions_require_authentication(client, user, planting):
    expense = create_expense(actor=user, description="Supplies", total_price="10").record
    paths = [
        reverse("economy:list"),
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
        "amount": "60",
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
            "amount": "40.01",
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
            "amount": "75",
        },
    )
    assert edited.status_code == 302 and Allocation.objects.get().amount_cents == 7500
    filtered = signed_in.get(reverse("economy:list"), {"year": 2026, "cycle": planting.cycle_id})
    assert filtered.context["totals"]["growing"] == Decimal("75")
    assert filtered.context["cycle_rows"][0]["per_plant"] == Decimal("1.50")


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
    response = signed_in.get(reverse("economy:list"), {"year": "invalid"})
    assert response.status_code == 200 and response.context["form"].errors
    assert b'data-total="spent"' not in response.content
    response = signed_in.get(reverse("economy:list"), {"year": 2026, "cycle": uuid4()})
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


def test_currency_preference_only_changes_labels_and_rejects_unsafe_destinations(signed_in, user):
    expense = create_expense(actor=user, description="Supplies", total_price="100").record
    original = expense.updated_at
    response = signed_in.post(
        reverse("set_currency"), {"currency": "EUR", "next": "/economy/?year=2026"}, secure=True
    )
    assert response.status_code == 302 and response.url == "/economy/?year=2026"
    assert (
        response.cookies[CURRENCY_COOKIE].value == "EUR"
        and response.cookies[CURRENCY_COOKIE]["secure"]
    )
    page = signed_in.get(response.url)
    assert page.context["currency_label"] == "EUR" and page.context["totals"]["spent"] == 100
    expense.refresh_from_db()
    assert expense.total_price == 100 and expense.updated_at == original
    invalid = signed_in.post(reverse("set_currency"), {"currency": "EUR\r\nInjected"})
    assert invalid.status_code == 400 and CURRENCY_COOKIE not in invalid.cookies
    assert signed_in.get(reverse("set_currency")).status_code == 405
    assert (
        signed_in.post(
            reverse("set_currency"), {"currency": "SEK", "next": "//outside.example/"}
        ).url
        == "/economy/"
    )


def test_planting_links_open_the_relevant_economy_view_without_inline_cost_summaries(
    signed_in, planting
):
    page = signed_in.get(planting.get_absolute_url())
    target = f"/economy/?cycle={planting.cycle_id}&amp;year=2026".encode()
    assert target in page.content and b"Economy for this cycle" in page.content
    assert b"Estimated cost per plant" not in page.content
    plantings = signed_in.get(reverse("garden:planting-list")).content
    assert plantings.count(b'href="/economy/"') == 1
    economy = signed_in.get(reverse("economy:list")).content
    assert b'href="/economy/" aria-current="page"' in economy
    assert b"data-currency-form" in economy.split(b"<main", 1)[0]
    assert b"data-currency-form" not in economy.split(b"<main", 1)[1]
