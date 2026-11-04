"""Isolated filesystem/WAL tests, separate from local development state."""

import os
from pathlib import Path

from .base import *  # noqa: F403
from .base import DATABASES

SECRET_KEY = "synthetic-test-secret-not-for-real-installations-" * 2
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
TEST_DATA_DIR = Path(os.environ.get("GROWBOOK_STATE_DIR", "/state")) / "test-state"
TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASES["default"]["NAME"] = TEST_DATA_DIR / "db.sqlite3"
DATABASES["default"]["TEST"] = {"NAME": TEST_DATA_DIR / "test.sqlite3"}
STATIC_ROOT = TEST_DATA_DIR / "staticfiles"
STATIC_ROOT.mkdir(parents=True, exist_ok=True)
MEDIA_ROOT = TEST_DATA_DIR / "media"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
# Optional browser-test debug settings; no private host configuration is read.
if os.environ.get("GROWBOOK_BROWSER_TESTS") == "1":
    DEBUG = True
