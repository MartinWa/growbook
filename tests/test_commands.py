from datetime import date
from io import StringIO
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from diary.services import create_entry

pytestmark = pytest.mark.django_db(transaction=True)


def test_operational_management_commands(user, image_upload, tmp_path):
    create_entry(
        author=user,
        date=date(2026, 5, 9),
        body="Synthetic command test",
        uploads=[image_upload()],
        submission_key=uuid4(),
    )
    config = tmp_path / "recovery.env"
    config.write_text("synthetic recovery configuration", encoding="utf-8")
    output = StringIO()
    backup = tmp_path / "backup"
    restored = tmp_path / "restored"
    call_command("backup_garden", str(backup), configuration=[str(config)], stdout=output)
    call_command("verify_backup", str(backup), stdout=output)
    call_command("restore_garden", str(backup), str(restored), stdout=output)
    call_command("cleanup_media", stdout=output)
    assert (restored / "db.sqlite3").exists()
    assert "Backup verified" in output.getvalue()
    assert "Restore verified" in output.getvalue()
    assert "Would remove 0" in output.getvalue()
    with pytest.raises(CommandError):
        call_command("restore_garden", str(backup), str(restored))
