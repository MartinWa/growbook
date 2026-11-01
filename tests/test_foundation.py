from datetime import date
from uuid import uuid4

import pytest
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import connections
from django.test import RequestFactory

from core.admin import GardenUserAdmin, PhotoAdmin
from core.checks import sqlite_runtime_check
from core.media import private_path
from core.models import Photo, User
from diary.admin import DiaryEntryAdmin
from diary.models import DiaryEntry
from diary.services import create_entry
from growbook.runtime import wal_runtime_is_fixed

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("version", [(3, 51, 3), (3, 52, 0), (3, 50, 7), (3, 44, 6)])
def test_fixed_runtime_versions_are_recognized(version):
    assert wal_runtime_is_fixed(version)


@pytest.mark.parametrize("version", [(3, 50, 4), (3, 51, 2), (3, 44, 5), (3, 45, 0)])
def test_unsafe_runtime_versions_are_rejected(version):
    assert not wal_runtime_is_fixed(version)


def test_runtime_check_surfaces_unsafe_library(monkeypatch):
    from core import checks

    monkeypatch.setattr(checks.sqlite3, "sqlite_version_info", (3, 50, 4))
    assert sqlite_runtime_check(None)[0].id == "growbook.E001"


def test_admin_has_no_bypass_for_diary_photo_writes(user, image_upload):
    entry = create_entry(
        author=user,
        date=date(2026, 5, 9),
        body="Synthetic",
        uploads=[image_upload()],
        submission_key=uuid4(),
    ).entry
    user.is_staff = True
    user.is_superuser = True
    request = RequestFactory().get("/operator/")
    request.user = user
    for model, model_admin, obj in [
        (DiaryEntry, DiaryEntryAdmin, entry),
        (Photo, PhotoAdmin, entry.photos.get()),
    ]:
        instance = model_admin(model, admin.site)
        assert not instance.has_add_permission(request)
        assert not instance.has_change_permission(request, obj)
        assert not instance.has_delete_permission(request, obj)
    assert not GardenUserAdmin(User, admin.site).has_delete_permission(request, user)


@pytest.mark.parametrize("relative", ["../db.sqlite3", "/etc/passwd", "..\\secrets", "a/../../b"])
def test_private_media_path_cannot_escape_storage(relative):
    with pytest.raises(ValueError):
        private_path(relative)


def test_database_has_foreign_key_enforcement():
    with connections["default"].cursor() as cursor:
        cursor.execute("PRAGMA foreign_keys")
        assert cursor.fetchone()[0] == 1


def test_real_date_default_uses_configured_garden_timezone(settings, monkeypatch, user):
    from datetime import UTC, datetime

    from django.utils import timezone

    settings.TIME_ZONE = "Europe/Stockholm"
    monkeypatch.setattr(timezone, "now", lambda: datetime(2026, 1, 1, 23, 30, tzinfo=UTC))
    assert timezone.localdate() == date(2026, 1, 2)


def test_anonymous_writer_denied():
    from django.contrib.auth.models import AnonymousUser

    with pytest.raises(PermissionDenied):
        create_entry(
            author=AnonymousUser(),
            date=date(2026, 5, 9),
            body="synthetic",
            uploads=[],
            submission_key=uuid4(),
        )
