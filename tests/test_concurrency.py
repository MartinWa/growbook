import io
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import uuid4

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from PIL import Image

from core.backups import create_backup, verify_backup
from core.cleanup import cleanup_orphans
from core.media import media_operation
from core.models import User
from diary.models import DiaryEntry
from diary.services import create_entry

pytestmark = pytest.mark.django_db(transaction=True)


def worker(callback):
    close_old_connections()
    try:
        return callback()
    finally:
        connection.close()


def test_wal_full_durability_and_busy_timeout_are_configured():
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA journal_mode")
        assert cursor.fetchone()[0] == "wal"
        cursor.execute("PRAGMA synchronous")
        assert cursor.fetchone()[0] == 2  # FULL
        cursor.execute("PRAGMA busy_timeout")
        assert cursor.fetchone()[0] == 10000
    assert connection.settings_dict["OPTIONS"]["transaction_mode"] == "IMMEDIATE"
    assert not connection.settings_dict["ATOMIC_REQUESTS"]


def test_simultaneous_diary_session_writes_and_backup(user, tmp_path):
    start = threading.Barrier(5)
    config = tmp_path / "synthetic.env"
    config.write_text("synthetic configuration", encoding="utf-8")

    def capture(number):
        start.wait(timeout=10)
        author = User.objects.get(pk=user.pk)
        return create_entry(
            author=author,
            date=date(2026, 5, 9),
            body=f"Concurrent {number}",
            uploads=[],
            submission_key=uuid4(),
        ).entry.pk

    def session_write():
        start.wait(timeout=10)
        session = SessionStore()
        session["synthetic"] = True
        session.save()
        return session.session_key

    def snapshot():
        start.wait(timeout=10)
        path = tmp_path / "backup"
        create_backup(path, [config])
        return verify_backup(path)

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(worker, lambda n=n: capture(n)) for n in range(3)]
        futures += [executor.submit(worker, session_write), executor.submit(worker, snapshot)]
        results = [future.result(timeout=20) for future in futures]
    assert len(set(results[:3])) == 3
    assert DiaryEntry.objects.count() == 3


def test_simultaneous_same_key_retries_make_one_complete_entry(user):
    key = uuid4()
    start = threading.Barrier(2)

    def capture():
        start.wait(timeout=10)
        author = User.objects.get(pk=user.pk)
        return create_entry(
            author=author,
            date=date(2026, 5, 9),
            body="Same request",
            uploads=[],
            submission_key=key,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result(timeout=15)
            for future in [
                executor.submit(worker, capture),
                executor.submit(worker, capture),
            ]
        ]
    assert results[0].entry.pk == results[1].entry.pk
    assert sorted(result.created for result in results) == [False, True]
    assert DiaryEntry.objects.count() == 1


def test_cleanup_waits_for_inflight_save_and_never_deletes_published_photo(
    user,
    settings,
    monkeypatch,
):
    from core import photo_storage

    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), "green").save(buffer, format="JPEG")
    data = buffer.getvalue()
    published = threading.Event()
    release = threading.Event()
    original_publish = photo_storage.publish_staged

    def paused_publish(photo):
        result = original_publish(photo)
        published.set()
        assert release.wait(timeout=10)
        return result

    monkeypatch.setattr(photo_storage, "publish_staged", paused_publish)

    def capture():
        author = User.objects.get(pk=user.pk)
        return create_entry(
            author=author,
            date=date(2026, 5, 9),
            body="Inflight",
            uploads=[SimpleUploadedFile("synthetic.jpg", data)],
            submission_key=uuid4(),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        save = executor.submit(worker, capture)
        assert published.wait(timeout=10)
        cleanup = executor.submit(worker, lambda: cleanup_orphans(apply=True, minimum_age=0))
        assert not cleanup.done()
        release.set()
        saved = save.result(timeout=15)
        assert cleanup.result(timeout=15) == []
    assert saved.entry.photos.count() == 1
    assert list(settings.MEDIA_ROOT.glob("photos/*/*/*/original.jpg"))


def test_snapshot_mutex_rejects_whole_request_atomic_wrapper():
    from django.db import transaction

    with transaction.atomic(), pytest.raises(RuntimeError, match="outside"), media_operation():
        pass
