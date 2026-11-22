"""Economy state survives the backup API and works in a separate restored instance."""

import os
import subprocess
import sys
from contextlib import closing
from datetime import date
from uuid import uuid4

import pytest
from django.conf import settings
from django.utils import timezone

from core.backups import create_backup, open_snapshot, restore_backup, verify_backup
from economy.services import create_allocation, create_expense, create_sale, delete_expense
from garden.services import create_crop, create_planting, create_variety

pytestmark = pytest.mark.django_db(transaction=True)


def test_clean_restore_preserves_money_reservations_equipment_sales_receipts_and_writes(
    user, tmp_path, monkeypatch
):
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: date(2026, 5, 9))
    crop = create_crop(actor=user, name="Tomato").record
    golden = create_variety(actor=user, crop=crop, name="Golden").record
    red = create_variety(actor=user, crop=crop, name="Red").record
    first = create_planting(
        actor=user, crop=crop, variety=golden, start_year=2026, quantity=50
    ).record
    second = create_planting(actor=user, crop=crop, variety=red, start_year=2026).record
    purchase_key, allocation_key, deleted_key = uuid4(), uuid4(), uuid4()
    expense = create_expense(
        actor=user,
        description="Shared fertilizer",
        purchased_on=date(2026, 5, 9),
        total_price="100.00",
        purchased_quantity="5",
        unit="kilograms",
        submission_key=purchase_key,
    ).record
    original_version = expense.updated_at.isoformat()
    allocation = create_allocation(
        actor=user,
        expense=expense,
        expected_expense_updated_at=expense.updated_at,
        growing_year=2026,
        amount="80",
        cycle_ids=[first.cycle_id, second.cycle_id],
        submission_key=allocation_key,
    ).record
    equipment = create_expense(
        actor=user,
        description="Seed trays",
        total_price="900",
        category="equipment",
        useful_life_years=3,
    ).record
    create_allocation(
        actor=user,
        expense=equipment,
        expected_expense_updated_at=equipment.updated_at,
        growing_year=2027,
        share="100",
        scope="year",
    )
    create_sale(
        actor=user,
        description="Plants",
        sold_on=date(2026, 5, 10),
        quantity=4,
        unit_price="12.50",
        cycle=first.cycle_id,
    )
    removed = create_expense(
        actor=user, description="Removed purchase", total_price="1", submission_key=deleted_key
    ).record
    delete_expense(actor=user, pk=removed.pk, expected_updated_at=removed.updated_at)
    config = tmp_path / "operator.env"
    config.write_text(
        "GROWBOOK_SECRET_KEY=" + "synthetic-economy-key-" * 4 + "\n", encoding="utf-8"
    )
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    manifest = create_backup(backup, [config])
    assert verify_backup(backup) == manifest
    restore_backup(backup, restored)
    with closing(open_snapshot(restored / "db.sqlite3")) as snapshot:
        assert snapshot.execute(
            "SELECT purchase_year, total_cents, purchased_quantity, unit FROM economy_expense "
            "WHERE id=?",
            [expense.pk],
        ).fetchone() == (2026, 10000, 5, "kilograms")
        assert snapshot.execute(
            "SELECT growing_year, amount_cents, scope, method FROM economy_allocation WHERE id=?",
            [allocation.pk],
        ).fetchone() == (2026, 8000, "selected", "plants")
        assert snapshot.execute("SELECT COUNT(*) FROM economy_allocationcycle").fetchone()[0] == 2
        assert snapshot.execute(
            "SELECT expense_id FROM economy_economysubmission WHERE key=?", [deleted_key.hex]
        ).fetchone() == (None,)

    script = f"""
import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'growbook.settings.development'
import django
django.setup()
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client
from core.models import User
from economy.calculations import load_economy
from economy.models import Expense, Allocation, Sale, EconomySubmission
from economy.queries import overview, expense_detail
from economy.services import create_expense, create_allocation, EconomyConflict
from garden.models import Crop, Planting
from garden.services import edit_planting, create_planting, delete_planting
actor = User.objects.get(username='gardener')
crop = Crop.objects.get(name='Tomato')
first = Planting.objects.get(pk={first.pk})
second = Planting.objects.get(pk={second.pk})
state = load_economy()
totals = overview(state, 2026)['totals']
assert totals['spent'] == 1000 and totals['growing'] == 400
assert totals['income'] == 50 and totals['growing_difference'] == -350
assert totals['allocated'] == 0 and totals['unallocated'] == 400
assert overview(state, 2027)['pending_allocations'][0].reason == 'empty'
assert overview(state, 2028)['totals']['growing'] == 300
equipment = next(item for item in state.estimates if item.expense.description == 'Seed trays')
assert expense_detail(equipment, 2026)['future_shares'] == 600
assert Sale.objects.get().variety_id == first.variety_id
assert EconomySubmission.objects.count() == 6
replayed = create_expense(
    actor=actor, description='Shared fertilizer', purchased_on=date(2026, 5, 9),
    total_price='100.00', purchased_quantity='5', unit='kilograms',
    submission_key=UUID('{purchase_key}'),
)
assert replayed.replayed
replayed = create_allocation(
    actor=actor, expense={expense.pk},
    expected_expense_updated_at=datetime.fromisoformat('{original_version}'),
    growing_year=2026, amount='80', cycle_ids=[second.cycle_id, first.cycle_id],
    submission_key=UUID('{allocation_key}'),
)
assert replayed.replayed and Allocation.objects.count() == 2
edit_planting(actor=actor, pk=second.pk, expected_updated_at=second.updated_at,
              crop=crop.pk, variety=second.variety_id, start_year=2026, quantity=50)
state = load_economy()
assert overview(state, 2026)['totals']['allocated'] == 80
assert overview(state, 2026)['totals']['unallocated'] == 320
assert overview(state, 2026, cycle=first.cycle_id)['cycle_rows'][0]['per_plant'] == Decimal('0.8')
assert first.quantity == 50
try:
    delete_planting(actor=actor, pk=first.pk, expected_updated_at=first.updated_at)
except ValidationError:
    pass
else:
    raise AssertionError('financial reference was removed after restore')
create_planting(actor=actor, crop=crop.pk, start_year=2027, quantity=10)
assert overview(load_economy(), 2027)['totals']['allocated'] == 300
try:
    create_expense(actor=actor, description='Removed purchase', total_price='1',
                   submission_key=UUID('{deleted_key}'))
except EconomyConflict:
    pass
else:
    raise AssertionError('deleted purchase was recreated after restore')
create_expense(actor=actor, description='Restored purchase', total_price='1.25')
anonymous = Client(HTTP_HOST='localhost')
assert anonymous.get('/economy/').status_code == 302
client = Client(HTTP_HOST='localhost')
client.force_login(actor)
for record in Expense.objects.all():
    assert client.get(record.get_absolute_url()).status_code == 200
assert client.get('/economy/?year=2027').status_code == 200
with connection.cursor() as cursor:
    cursor.execute('PRAGMA journal_mode')
    assert cursor.fetchone()[0] == 'wal'
print('restored economy retained exact costs, current estimates, receipts and validated writes')
"""
    environment = os.environ.copy()
    environment["GROWBOOK_DATA_DIR"] = str(restored)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=settings.BASE_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert "restored economy retained exact costs" in completed.stdout
