"""Secure private-server settings; refuse missing operator configuration."""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import env_bool, env_list


def read_secret_key():
    """Read the secret from the environment, or from a file for Docker/Podman secrets."""
    value = os.environ.get("GROWBOOK_SECRET_KEY", "").strip()
    if value:
        return value
    path = os.environ.get("GROWBOOK_SECRET_KEY_FILE", "").strip()
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ImproperlyConfigured(f"Cannot read GROWBOOK_SECRET_KEY_FILE {path}: {exc}") from exc


SECRET_KEY = read_secret_key()
ALLOWED_HOSTS = env_list("GROWBOOK_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env_list("GROWBOOK_CSRF_TRUSTED_ORIGINS")
if (
    len(SECRET_KEY) < 50
    or SECRET_KEY.startswith("REPLACE")
    or not ALLOWED_HOSTS
    or "*" in ALLOWED_HOSTS
):
    raise ImproperlyConfigured(
        "Set a long GROWBOOK_SECRET_KEY (or GROWBOOK_SECRET_KEY_FILE) and explicit "
        "GROWBOOK_ALLOWED_HOSTS."
    )
if not os.environ.get("GROWBOOK_DATA_DIR"):
    raise ImproperlyConfigured("Set GROWBOOK_DATA_DIR to private storage local to the server.")
DEBUG = False
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
# Enable only behind a trusted proxy that replaces this header on a private network.
if env_bool("GROWBOOK_TRUST_PROXY"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
