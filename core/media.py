"""Serialize media mutation, staging cleanup and snapshots across local processes."""

from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.db import connection
from filelock import FileLock, Timeout


class MediaBusy(Exception):
    pass


def private_path(relative):
    """Resolve only generated, relative paths under private media storage."""
    value = PurePosixPath(str(relative))
    if value.is_absolute() or ".." in value.parts or "\\" in str(relative):
        raise ValueError("Unsafe private media path.")
    root = Path(settings.MEDIA_ROOT).resolve()
    result = (root / str(value)).resolve()
    if not result.is_relative_to(root):
        raise ValueError("Private media path escaped storage.")
    return result


@contextmanager
def media_operation():
    if connection.in_atomic_block:
        raise RuntimeError("Media processing/snapshotting must run outside a database transaction.")
    root = Path(settings.MEDIA_ROOT)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = FileLock(root / ".operation.lock", timeout=settings.MEDIA_LOCK_TIMEOUT, mode=0o600)
    try:
        with lock:
            yield
    except Timeout as exc:
        raise MediaBusy("Another media operation is still running. Please retry.") from exc
