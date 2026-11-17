"""Overlapping file-backed SQLite/WAL writes must preserve budgets and references."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.utils import timezone

from core.models import User
from economy.models import Allocation
from economy.services import create_allocation, create_expense, edit_expense
from garden.services import create_crop, create_planting, delete_planting

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def setup(user, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: date(2026, 5, 9))
    crop = create_crop(actor=user, name="Tomato").record
    planting = create_planting(actor=user, crop=crop, start_year=2026, quantity=50).record
    expense = create_expense(actor=user, description="Soil", total_price="100").record
    return expense, planting


def worker(callback):
    close_old_connections()
    try:
        return callback()
    finally:
        connection.close()


def test_overlapping_allocations_cannot_reserve_more_than_the_purchase(user, setup):
    expense, planting = setup
    ready = threading.Barrier(2)

    def reserve():
        ready.wait(timeout=10)
        try:
            return create_allocation(
                actor=User.objects.get(pk=user.pk),
                expense=expense.pk,
                expected_expense_updated_at=expense.updated_at,
                growing_year=2026,
                amount="75",
                cycle_ids=[planting.cycle_id],
            )
        except ValidationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result(timeout=15)
            for future in [pool.submit(worker, reserve), pool.submit(worker, reserve)]
        ]
    assert sum(result is not None for result in results) == 1
    assert list(Allocation.objects.values_list("amount_cents", flat=True)) == [7500]


def test_overlapping_identical_allocation_retries_return_one_reservation(user, setup):
    expense, planting = setup
    ready, key = threading.Barrier(2), uuid4()

    def reserve():
        ready.wait(timeout=10)
        return create_allocation(
            actor=User.objects.get(pk=user.pk),
            expense=expense.pk,
            expected_expense_updated_at=expense.updated_at,
            growing_year=2026,
            share="100",
            cycle_ids=[planting.cycle_id],
            submission_key=key,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result(timeout=15)
            for future in [pool.submit(worker, reserve), pool.submit(worker, reserve)]
        ]
    assert sorted(result.replayed for result in results) == [False, True]
    assert Allocation.objects.count() == 1 and Allocation.objects.get().amount_cents == 10000


@pytest.mark.parametrize("first_writer", ["price", "allocation"])
def test_price_edits_and_allocation_creation_revalidate_both_orderings(
    user, setup, monkeypatch, first_writer
):
    from economy import services

    expense, planting = setup
    locked, release, started = threading.Event(), threading.Event(), threading.Event()
    original = (
        services._validate_budgets if first_writer == "price" else services._allocation_amount
    )

    def pause(*args):
        result = original(*args)
        locked.set()
        assert release.wait(timeout=10)
        return result

    monkeypatch.setattr(
        services, "_validate_budgets" if first_writer == "price" else "_allocation_amount", pause
    )

    def price():
        return edit_expense(
            actor=User.objects.get(pk=user.pk),
            pk=expense.pk,
            expected_updated_at=expense.updated_at,
            description="Soil",
            total_price="40",
        )

    def allocation():
        return create_allocation(
            actor=User.objects.get(pk=user.pk),
            expense=expense.pk,
            expected_expense_updated_at=expense.updated_at,
            growing_year=2026,
            amount="80",
            cycle_ids=[planting.cycle_id],
        )

    first, second = (price, allocation) if first_writer == "price" else (allocation, price)

    def blocked():
        started.set()
        with pytest.raises(ValidationError):
            second()

    with ThreadPoolExecutor(max_workers=2) as pool:
        accepted = pool.submit(worker, first)
        assert locked.wait(timeout=10)
        rejected = pool.submit(worker, blocked)
        assert started.wait(timeout=10) and not rejected.done()
        release.set()
        accepted.result(timeout=15)
        rejected.result(timeout=15)
    expense.refresh_from_db()
    assert sum(Allocation.objects.values_list("amount_cents", flat=True)) <= expense.total_cents


@pytest.mark.parametrize("first_writer", ["delete", "allocation"])
def test_allocation_and_planting_deletion_serialize_without_dangling_references(
    user, setup, monkeypatch, first_writer
):
    from economy import services
    from garden.services import plantings

    expense, planting = setup
    locked, release, started = threading.Event(), threading.Event(), threading.Event()
    original = (
        services._allocation_amount if first_writer == "allocation" else plantings._active_actor
    )

    def pause(*args):
        result = original(*args)
        locked.set()
        assert release.wait(timeout=10)
        return result

    monkeypatch.setattr(
        services if first_writer == "allocation" else plantings,
        "_allocation_amount" if first_writer == "allocation" else "_active_actor",
        pause,
    )

    def allocate():
        return create_allocation(
            actor=User.objects.get(pk=user.pk),
            expense=expense.pk,
            expected_expense_updated_at=expense.updated_at,
            growing_year=2026,
            amount="80",
            cycle_ids=[planting.cycle_id],
        )

    def delete():
        return delete_planting(
            actor=User.objects.get(pk=user.pk),
            pk=planting.pk,
            expected_updated_at=planting.updated_at,
        )

    first, second = (delete, allocate) if first_writer == "delete" else (allocate, delete)

    def blocked():
        started.set()
        with pytest.raises(ValidationError):
            second()

    with ThreadPoolExecutor(max_workers=2) as pool:
        accepted = pool.submit(worker, first)
        assert locked.wait(timeout=10)
        rejected = pool.submit(worker, blocked)
        assert started.wait(timeout=10) and not rejected.done()
        release.set()
        accepted.result(timeout=15)
        rejected.result(timeout=15)
    assert connection.cursor().execute("PRAGMA foreign_key_check").fetchall() == []
