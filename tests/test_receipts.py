"""Shared receipt primitives: the stored text forms and frozen digests."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from django.core.exceptions import ValidationError
from django.utils import translation

from core import receipts


@pytest.fixture(autouse=True)
def english_messages():
    with translation.override("en"):
        yield


PROBE_UUID = UUID("8f14e45f-ceea-467a-9c2f-1b4e1a0d9d4b")
PROBE_DATETIME = datetime(2026, 5, 9, 12, 30, 45, tzinfo=UTC)


def test_canonical_keeps_the_text_forms_already_stored_in_receipts():
    """Garden dates use ISO text; economy datetimes and normalized decimals keep theirs."""
    assert receipts.canonical(
        {
            "date": date(2026, 5, 9),
            "datetime": PROBE_DATETIME,
            "whole": Decimal("30.0"),
            "fraction": Decimal("1.50"),
            "identifier": PROBE_UUID,
            "tuple": (1, Decimal("1.50")),
        }
    ) == {
        "date": "2026-05-09",
        "datetime": "2026-05-09 12:30:45+00:00",
        "whole": "3E+1",
        "fraction": "1.5",
        "identifier": str(PROBE_UUID),
        "tuple": [1, "1.5"],
    }


def test_fingerprint_matches_the_stored_digest():
    payload = {
        "version": 1,
        "kind": "probe",
        "when": "2026-05-09 12:30:45+00:00",
        "day": "2026-05-09",
        "amount": "3E+1",
        "share": "1.5",
        "cycle": str(PROBE_UUID),
        "ids": [1, 2],
    }
    assert (
        receipts.fingerprint(payload)
        == "cad6f968ed9fec15cc926c66574c771937a5ad16da9d79e100e5bacdee3e2d06"
    )


def test_unsorted_fingerprint_matches_the_stored_diary_digest():
    """The diary creation payload hashes insertion order, not sorted keys."""
    payload = {
        "version": 1,
        "kind": "probe",
        "when": "2026-05-09 12:30:45+00:00",
        "day": "2026-05-09",
        "amount": "3E+1",
        "share": "1.5",
        "cycle": str(PROBE_UUID),
        "ids": [1, 2],
    }
    assert (
        receipts.fingerprint(payload, sort_keys=False)
        == "663ea37a61a6d2f360f9a86844657650789e7955a7f7df0df18bc23ec08e471c"
    )


def test_submission_key_parses_an_existing_key_or_mints_a_new_one():
    key = receipts.submission_key(str(PROBE_UUID))
    assert key == PROBE_UUID
    minted = receipts.submission_key(None)
    assert minted.version == 4
    with pytest.raises(ValidationError, match="Reload the form"):
        receipts.submission_key("not-a-key")
    with pytest.raises(ValidationError, match="Reload the capture form"):
        receipts.submission_key(
            "not-a-key", message="The save key is invalid. Reload the capture form."
        )


def test_version_token_rejects_missing_and_naive_values():
    assert receipts.version_token(PROBE_DATETIME) == PROBE_DATETIME
    for value in (None, "not-a-version", datetime(2026, 5, 9, 12, 30, 45)):
        with pytest.raises(ValidationError, match="Reload the edit form"):
            receipts.version_token(value)


def test_accepted_target_reports_conflicts_and_tombstones():
    class Conflict(ValidationError):
        pass

    def make(**changes):
        values = {"author_id": 7, "payload_sha256": "abc", "kind": "task", "task": object()}
        return type("Receipt", (), {**values, **changes})()

    conflict = Conflict("different content")
    deleted = Conflict("deleted")

    def accept(receipt, **kwargs):
        return receipts.accepted_target(
            receipt, 7, digest="abc", kind="task", conflict=conflict, deleted=deleted, **kwargs
        )

    assert accept(make()) is not None
    assert accept(None) is None
    for broken in (
        make(author_id=8),
        make(payload_sha256="other"),
        make(kind="note"),
    ):
        with pytest.raises(Conflict, match="different content"):
            accept(broken)
    with pytest.raises(Conflict, match="deleted"):
        accept(make(task=None))
