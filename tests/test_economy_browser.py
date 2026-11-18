"""Real economy forms, uncertain retries, current estimates and native fallbacks."""

import re
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pytest
from browser_helpers import browser_page, login
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from economy.models import Allocation, EconomySubmission, Expense, Sale
from economy.services import create_expense
from garden.services import create_crop, create_planting, create_variety, edit_planting

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.fixture(autouse=True)
def calendar(monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: date(2026, 5, 9))


@pytest.fixture
def catalog(user):
    crop = create_crop(actor=user, name="Tomato").record
    golden = create_variety(actor=user, crop=crop, name="Golden").record
    red = create_variety(actor=user, crop=crop, name="Red").record
    first = create_planting(
        actor=user, crop=crop, variety=golden, start_year=2026, quantity=50
    ).record
    second = create_planting(
        actor=user, crop=crop, variety=red, start_year=2026, quantity=50
    ).record
    return crop, golden, red, first, second


@contextmanager
def economy_page(browser_endpoint, live_server, user, language="en", **options):
    with browser_page(
        browser_endpoint,
        timeout=10000,
        **{"viewport": {"width": 390, "height": 844}, **options},
    ) as page:
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script(
            "window.economyCspViolations = []; "
            "document.addEventListener('securitypolicyviolation', event => "
            "economyCspViolations.push(event.violatedDirective));"
        )
        login(page, live_server, user, language)
        page.on("dialog", lambda dialog: dialog.accept())
        yield page
        assert errors == []
        if options.get("java_script_enabled", True):
            assert page.evaluate("economyCspViolations") == []


def save(page):
    page.locator("#economy-form .form-actions button[type=submit]").click()


def money_text(locator, value, language="en"):
    expect(locator.locator(".money-number")).to_have_text(
        value.replace(".", ",") if language == "sv" else value
    )


@pytest.mark.parametrize("language", ["en", "sv"])
def test_minimal_purchase_currency_and_retry_after_a_committed_but_uncertain_save(
    browser_endpoint, live_server, user, language, monkeypatch
):
    path = reverse("economy:expense-create")
    with economy_page(browser_endpoint, live_server, user, language) as page:
        page.goto(live_server.url + path + "?year=2028")
        expect(page.locator("#id_purchased_on")).to_have_value("2026-05-09")
        expect(page.locator("#id_purchased_quantity")).to_be_hidden()
        page.locator("#id_description").fill("Synthetic seed packet")
        page.locator("#id_total_price").fill("12,34" if language == "sv" else "12.34")
        key = page.locator("#id_submission_key").input_value()
        url = page.url
        page.locator("#account-menu > summary").click()
        page.locator("#currency").select_option("EUR")
        page.keyboard.press("Escape")
        expect(page.locator("html")).to_have_attribute("data-currency-label", "EUR")
        expect(page.locator("#id_description")).to_have_value("Synthetic seed packet")
        expect(page.locator("#id_submission_key")).to_have_value(key)
        expect(page).to_have_url(url)

        committed = []

        def uncertain(route):
            response = route.fetch()
            committed.append(response.status)
            route.fulfill(
                status=503,
                content_type="text/html",
                body='<div class="error-summary">Synthetic uncertain response; retry.</div>',
            )

        page.route(live_server.url + path, uncertain)
        save(page)
        expect(page.locator("#economy-errors")).to_contain_text("Synthetic uncertain response")
        expect(page.locator("#economy-errors")).to_be_focused()
        expect(page.locator("#id_submission_key")).to_have_value(key)
        expect(page.locator("#id_description")).to_be_editable()
        assert committed == [204]
        page.unroute(live_server.url + path)
        monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: date(2027, 1, 1))
        page.reload()
        expect(page.locator("#id_purchased_on")).to_have_value("2026-05-09")
        expect(page.locator("#id_submission_key")).to_have_value(key)
        assert page.locator("#id_purchased_on").evaluate("input => input.validity.valid")
        save(page)
        expect(page).to_have_url(re.compile(r"/economy/expenses/\d+/\?year=2026"))
        money_text(page.locator(".record-fields"), "12.34", language)
        expect(page.locator(".record-fields [data-currency-label]")).to_have_text("EUR")
        assert page.evaluate("Object.keys(sessionStorage).every(key => !key.includes(':expense:'))")
        page.reload()
        expect(page.locator("html")).to_have_attribute("data-currency-label", "EUR")
    expense = Expense.objects.get()
    assert expense.total_cents == 1234 and expense.purchase_year == 2026
    assert expense.purchased_quantity is None and EconomySubmission.objects.count() == 1


def test_pending_group_reserves_only_its_amount_and_quantity_edits_redistribute_it(
    browser_endpoint, live_server, user, catalog
):
    crop, _, _, first, second = catalog
    second = edit_planting(
        actor=user,
        pk=second.pk,
        expected_updated_at=second.updated_at,
        crop=crop,
        variety=second.variety_id,
        start_year=2026,
        quantity=None,
    ).record
    expense = create_expense(
        actor=user, description="Synthetic shared soil", total_price="100"
    ).record
    with economy_page(browser_endpoint, live_server, user) as page:
        page.goto(live_server.url + reverse("economy:allocation-create", args=[expense.pk]))
        page.locator("#id_amount").fill("80")
        for member in (first, second):
            page.locator(f'[name=cycle_ids][value="{member.cycle_id}"]').check()
        save(page)
        expect(page).to_have_url(re.compile(r"/economy/expenses/\d+/\?year=2026#allocation-"))
        expect(page.locator("main")).to_contain_text("Pending: supply the unknown plant quantities")
        money_text(page.locator("[data-available-balance]"), "20.00")

        page.get_by_role("link", name="Allocate expense", exact=True).click()
        page.locator("#id_amount").fill("20.01")
        page.locator("[name=scope][value=year]").check()
        expect(page.locator("#id_cycle_ids_group")).to_be_hidden()
        save(page)
        expect(page.locator("#economy-errors")).to_contain_text("available balance")
        expect(page.locator("#economy-errors")).to_be_focused()
        page.get_by_role("button", name="Discard unsaved writing").click()

        for member, count in ((second, "50"), (first, "0")):
            page.goto(live_server.url + reverse("garden:planting-edit", args=[member.pk]))
            page.locator("#id_quantity").fill(count)
            page.locator("#register-form .form-actions button[type=submit]").click()
            expect(page).to_have_url(live_server.url + member.get_absolute_url())
            page.goto(live_server.url + reverse("economy:list", query={"year": 2026}))
            money_text(page.locator('[data-total="allocated"]'), "80.00")
            money_text(page.locator('[data-total="unallocated"]'), "20.00")
            row = page.locator(f'tr[data-cycle="{second.cycle_id}"]')
            money_text(row.locator("td").nth(1), "40.00" if count == "50" else "80.00")
        expect(
            page.locator(f'tr[data-cycle="{first.cycle_id}"]').locator("td").nth(2)
        ).to_have_text("Unavailable")
    assert Allocation.objects.get().amount_cents == 8000
    expense.refresh_from_db()
    assert expense.total_cents == 10000


@pytest.mark.parametrize(
    "scope,selected_count", [("selected", 2), ("year", 0), ("year", 1), ("year", 2)]
)
def test_all_year_group_and_selected_cycles_survive_draft_recovery(
    browser_endpoint, live_server, user, catalog, scope, selected_count
):
    members = catalog[3:]
    expense = create_expense(actor=user, description="Synthetic supplies", total_price="100").record
    with economy_page(browser_endpoint, live_server, user) as page:
        path = reverse("economy:allocation-create", args=[expense.pk])
        page.goto(live_server.url + path)
        key = page.locator("#id_submission_key").input_value()
        version = page.locator("#id_expected_expense_updated_at").input_value()
        page.locator("#id_share").fill("60")
        for member in members[:selected_count]:
            page.locator(f'[name=cycle_ids][value="{member.cycle_id}"]').check()
        page.locator(f"[name=scope][value={scope}]").check()
        expect(page.locator("[data-draft-storage]")).to_be_hidden()
        page.reload()
        expect(page.locator("[data-draft-status]")).to_contain_text("was restored")
        expect(page.locator("#id_share")).to_have_value("60")
        expect(page.locator("#id_submission_key")).to_have_value(key)
        expect(page.locator("#id_expected_expense_updated_at")).to_have_value(version)
        page.locator("[name=scope][value=selected]").check()
        expect(page.locator("#id_cycle_ids_group")).to_be_visible()
        for index, member in enumerate(members):
            expect(page.locator(f'[name=cycle_ids][value="{member.cycle_id}"]')).to_be_checked(
                checked=index < selected_count
            )
        page.locator("[name=scope][value=year]").check()
        expect(page.locator("#id_cycle_ids_group")).to_be_hidden()
        page.locator(f"[name=scope][value={scope}]").check()
        save(page)
        expect(page).to_have_url(re.compile(r"#allocation-"))
        expect(page.locator("main")).to_contain_text(
            "All planting cycles in the growing year"
            if scope == "year"
            else "Selected planting cycles"
        )
        money_text(page.locator("[data-available-balance]"), "40.00")
    allocation = Allocation.objects.get()
    assert allocation.scope == scope and allocation.amount_cents == 6000
    assert set(allocation.cycles.values_list("pk", flat=True)) == (
        {member.cycle_id for member in members[:selected_count]} if scope == "selected" else set()
    )


@pytest.mark.parametrize("language", ["en", "sv"])
def test_equipment_schedule_editing_keyboard_tables_and_confirmed_deletion(
    browser_endpoint, live_server, user, language
):
    expense = create_expense(
        actor=user,
        description="Synthetic seed trays",
        total_price="900",
        category="equipment",
        useful_life_years=3,
    ).record
    with economy_page(browser_endpoint, live_server, user, language) as page:
        page.goto(live_server.url + expense.get_absolute_url() + "?year=2027")
        equipment = page.locator('[aria-labelledby="equipment-caption"]')
        expect(equipment).to_have_attribute("role", "region")
        expect(equipment.locator("tbody tr")).to_have_count(3)
        for width in (320, 390, 1280):
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert equipment.locator("td[data-label]").count() == 12
            assert all(
                equipment.locator("td[data-label]").evaluate_all(
                    "cells => cells.map(cell => cell.dataset.label.length > 0)"
                )
            )
        page.set_viewport_size({"width": 390, "height": 844})
        equipment.focus()
        equipment.press("End")
        page.wait_for_function(
            "() => document.querySelector('[aria-labelledby=equipment-caption]').scrollTop > 0"
        )
        page.goto(live_server.url + reverse("economy:expense-edit", args=[expense.pk]))
        expect(page.locator("#id_useful_life_years")).to_be_visible()
        page.locator("#id_useful_life_years").fill("2")
        save(page)
        expect(page).to_have_url(re.compile(r"/economy/expenses/\d+/"))
        expect(page.locator('[aria-labelledby="equipment-caption"] tbody tr')).to_have_count(2)
        money_text(
            page.locator('[aria-labelledby="equipment-caption"] tbody tr')
            .first.locator("td")
            .first,
            "450.00",
            language,
        )
        page.goto(live_server.url + reverse("economy:expense-delete", args=[expense.pk]))
        button = page.locator("main button[type=submit]")
        button.click()
        expect(page.locator("#id_confirm")).not_to_be_checked()
        expect(page.locator("#id_confirm")).to_be_focused()
        page.locator("#id_confirm").check()
        button.click()
        expect(page).to_have_url(re.compile(r"/economy/\?year=2026"))
        page.goto(live_server.url + reverse("economy:list", query={"year": 2027}))
        money_text(page.locator('[data-total="growing"]'), "0.00", language)
    assert not Expense.objects.exists()


def test_sale_derives_its_variety_and_rejects_a_stale_browser_edit(
    browser_endpoint, live_server, user, catalog
):
    first = catalog[3]
    with economy_page(browser_endpoint, live_server, user) as page:
        page.goto(live_server.url + reverse("economy:sale-create"))
        page.locator("#id_description").fill("Synthetic plants")
        page.locator("#id_quantity").fill("3")
        page.locator("#id_unit_price").fill("12.50")
        page.locator("#id_cycle").select_option(str(first.cycle_id))
        expect(page.locator("#id_variety")).to_have_value(str(first.variety_id))
        save(page)
        expect(page).to_have_url(re.compile(r"/economy/\?year=2026#sale-"))
        money_text(page.locator('[data-total="income"]'), "37.50")
        page.get_by_role("link", name=re.compile("Edit plant sale")).click()
        edit_url = page.url
        page.locator("#id_description").fill("Unsaved correction")
        other = page.context.new_page()
        other.goto(edit_url)
        other.locator("#id_description").fill("Saved correction")
        save(other)
        expect(other).to_have_url(re.compile(r"#sale-"))
        other.close()
        save(page)
        expect(page.locator("#economy-errors")).to_contain_text("changed while the form was open")
        expect(page.locator("#economy-errors")).to_be_focused()
        expect(page.locator("#id_description")).to_have_value("Unsaved correction")
    sale = Sale.objects.get()
    assert sale.total_price == Decimal("37.50") and sale.description == "Saved correction"
    first.refresh_from_db()
    assert first.quantity == 50


def test_a_cycle_with_an_unknown_variety_does_not_autoselect_its_only_known_variety(
    browser_endpoint, live_server, user, catalog
):
    crop, golden, *_ = catalog
    unknown = create_planting(actor=user, crop=crop, start_year=2026, quantity=10).record
    create_planting(
        actor=user,
        crop=crop,
        variety=golden,
        start_year=2026,
        quantity=20,
        batch_planting=unknown,
    )
    with economy_page(browser_endpoint, live_server, user) as page:
        page.goto(live_server.url + reverse("economy:sale-create"))
        page.locator("#id_description").fill("Synthetic mixed plants")
        page.locator("#id_quantity").fill("2")
        page.locator("#id_total_price").fill("25")
        page.locator("#id_cycle").select_option(str(unknown.cycle_id))
        expect(page.locator("#id_variety")).to_have_value("")
        save(page)
        expect(page).to_have_url(re.compile(r"#sale-"))
    assert Sale.objects.get().variety_id is None


def test_native_forms_keep_invalid_values_and_support_quantities_allocations_sales_and_currency(
    browser_endpoint, live_server, user, catalog
):
    first = catalog[3]
    with economy_page(browser_endpoint, live_server, user, java_script_enabled=False) as page:
        page.goto(live_server.url + reverse("economy:expense-create"))
        page.locator("#id_description").fill("Synthetic native soil")
        page.locator("#id_total_price").fill("100.50")
        page.locator("[data-expense-details] > summary").click()
        page.locator("#id_category").select_option("soil")
        page.locator("#id_purchased_quantity").fill("20.5")
        page.locator("#id_unit").select_option("items")
        save(page)
        expect(page.locator("#economy-errors")).to_contain_text("Items must be a whole number")
        expect(page.locator("#id_purchased_quantity")).to_have_value("20.5")
        page.locator("#id_unit").select_option("litres")
        save(page)
        expect(page).to_have_url(re.compile(r"/economy/expenses/\d+/"))
        page.locator("#account-menu > summary").click()
        page.locator("#currency").select_option("EUR")
        page.locator("[data-currency-form] button").click()
        expect(page.locator(".record-fields [data-currency-label]")).to_have_text("EUR")
        money_text(page.locator(".record-fields"), "100.50")
        page.get_by_role("link", name="Allocate expense", exact=True).click()
        page.locator("#id_quantity_used").fill("10.25")
        page.locator("[name=scope][value=year]").check()
        save(page)
        expect(page).to_have_url(re.compile(r"#allocation-"))
        money_text(page.locator("[data-available-balance]"), "50.25")
        page.goto(live_server.url + reverse("economy:sale-create"))
        page.locator("#id_description").fill("Synthetic native sale")
        page.locator("#id_quantity").fill("2")
        page.locator("#id_unit_price").fill("10.50")
        page.locator("#id_cycle").select_option(str(first.cycle_id))
        save(page)
        expect(page).to_have_url(re.compile(r"#sale-"))
        money_text(page.locator('[data-total="income"]'), "21.00")
    assert Expense.objects.get().purchased_quantity == Decimal("20.500")
    assert Allocation.objects.get().amount_cents == 5025
    assert Sale.objects.get().variety_id == first.variety_id
