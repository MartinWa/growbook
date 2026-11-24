"""Overlapping calendar writes: one receipt per submission and one note per month."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import uuid4

import pytest
from django.db import close_old_connections, connection

from core.models import User
from planner import services
from planner.models import MonthNote, PlannedTask, PlannerSubmission

pytestmark = pytest.mark.django_db(transaction=True)


def worker(callback):
    close_old_connections()
    try:
        return callback()
    finally:
        connection.close()


def test_simultaneous_same_key_task_retries_create_one_task(user):
    key = uuid4()
    start = threading.Barrier(2)

    def create():
        start.wait(timeout=10)
        return services.create_task(
            actor=User.objects.get(pk=user.pk),
            planned_date=date(2026, 5, 10),
            title="Concurrent task",
            submission_key=key,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result(timeout=20)
            for future in [executor.submit(worker, create), executor.submit(worker, create)]
        ]
    assert results[0].task.pk == results[1].task.pk
    assert sorted(result.replayed for result in results) == [False, True]
    assert PlannedTask.objects.count() == 1
    assert PlannerSubmission.objects.filter(key=key).count() == 1


def test_simultaneous_first_note_saves_keep_one_note_and_no_lost_text(user):
    start = threading.Barrier(2)

    def save(body):
        start.wait(timeout=10)
        return services.save_month_note(
            actor=User.objects.get(pk=user.pk),
            year=2026,
            month=5,
            body=body,
            submission_key=uuid4(),
        )

    outcomes = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(worker, lambda text=body: save(text)) for body in ("First", "Second")
        ]
        for future in futures:
            try:
                outcomes.append(future.result(timeout=20))
            except services.PlannerConflict as exc:
                outcomes.append(exc)
    assert MonthNote.objects.count() == 1
    assert sum(isinstance(outcome, services.PlannerConflict) for outcome in outcomes) == 1
    saved = next(outcome for outcome in outcomes if not isinstance(outcome, Exception))
    assert MonthNote.objects.get().body == saved.note.body
