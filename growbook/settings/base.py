"""Shared settings. Infrastructure-specific values come from the environment."""

import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(os.environ.get("GROWBOOK_DATA_DIR", "/state/development")).resolve()


def env_bool(name, default=False):
    value = os.environ.get(name, str(default)).lower()
    if value not in {"true", "false", "1", "0"}:
        raise ValueError(f"{name} must be true or false.")
    return value in {"true", "1"}


def env_list(name):
    return [value.strip() for value in os.environ.get(name, "").split(",") if value.strip()]


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "core.apps.CoreConfig",
    "diary.apps.DiaryConfig",
    "garden.apps.GardenConfig",
    "economy.apps.EconomyConfig",
    "planner.apps.PlannerConfig",
    "api.apps.ApiConfig",
    "axes",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "core.middleware.PrivateResponseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "core.middleware.GardenLocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "core.middleware.PhotoUploadLimitMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.GardenLoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",
]
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]
ROOT_URLCONF = "growbook.urls"
WSGI_APPLICATION = "growbook.wsgi.application"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.photo_limits",
                "core.context_processors.app_release",
                "core.appearance.appearance_context",
                "core.context_processors.currency_label",
            ],
        },
    },
]
DATABASES = {
    "default": {
        "ENGINE": "core.db.backends.sqlite3",
        "NAME": DATA_DIR / "db.sqlite3",
        "OPTIONS": {"timeout": 10, "transaction_mode": "IMMEDIATE"},
        "ATOMIC_REQUESTS": False,
        "CONN_MAX_AGE": 0,
    },
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "core.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "diary:list"
LOGOUT_REDIRECT_URL = "login"
CSRF_FAILURE_VIEW = "core.views.csrf_failure"
LANGUAGE_CODE = "sv"
LANGUAGES = [("sv", "Svenska"), ("en", "English")]
LOCALE_PATHS = [Path(os.environ.get("GROWBOOK_LOCALE_DIR", BASE_DIR / "locale"))]


def environment_time_zone():
    """Return a valid IANA zone, refusing a typo instead of failing later at runtime."""
    name = os.environ.get("GROWBOOK_TIME_ZONE", "Europe/Stockholm").strip() or "Europe/Stockholm"
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ImproperlyConfigured(
            f"GROWBOOK_TIME_ZONE must be an IANA zone name such as Europe/Stockholm, not {name!r}."
        ) from exc
    return name


# One display label for the whole instance; amounts are never converted or summed across labels.
CURRENCY_LABELS = ("SEK", "EUR", "USD", "GBP", "NOK", "DKK")


def environment_currency():
    """Refuse an unknown label at startup instead of rendering a misleading amount."""
    label = os.environ.get("GROWBOOK_CURRENCY", "SEK").strip() or "SEK"
    if label not in CURRENCY_LABELS:
        raise ImproperlyConfigured(
            f"GROWBOOK_CURRENCY must be one of {', '.join(CURRENCY_LABELS)}, not {label!r}."
        )
    return label


CURRENCY_LABEL = environment_currency()
REST_FRAMEWORK = {
    # Bearer access tokens only: browser sessions never authenticate API calls.
    "DEFAULT_AUTHENTICATION_CLASSES": ("api.auth.ApiTokenAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    # JSON only: no browsable API pages are rendered inside the private installation.
    "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
    "DEFAULT_PAGINATION_CLASS": "api.pagination.BoundedPageNumberPagination",
    "PAGE_SIZE": 50,
    # JSON only, and never selected by a query parameter before authentication.
    "URL_FORMAT_OVERRIDE": None,
}
TIME_ZONE = environment_time_zone()
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        # Build-time assets must be readable by the configured non-root runtime.
        # Do not inherit the private-upload permission modes below.
        "OPTIONS": {"file_permissions_mode": 0o644, "directory_permissions_mode": 0o755},
    },
}
# No URL mapping or public web-server alias points at this directory.
MEDIA_ROOT = DATA_DIR / "media"
MEDIA_URL = "/private-files-not-a-public-route/"
FILE_UPLOAD_PERMISSIONS = 0o600
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o700
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
PHOTO_MAX_COUNT = 10
# Allow both capture owners; the upload handler rejects either owner's extra file.
DATA_UPLOAD_MAX_NUMBER_FILES = 2 * PHOTO_MAX_COUNT + 1
PHOTO_MAX_FILE_BYTES = 20 * 1024 * 1024
PHOTO_MAX_TOTAL_BYTES = 80 * 1024 * 1024
PHOTO_MAX_PIXELS = 50_000_000
PHOTO_DISPLAY_EDGE = 1920
PHOTO_THUMBNAIL_EDGE = 300
MEDIA_LOCK_TIMEOUT = 30
MIGRATE_ON_START = env_bool("GROWBOOK_MIGRATE_ON_START", True)
MIGRATION_LOCK_TIMEOUT = 300
APP_VERSION = os.environ.get("GROWBOOK_VERSION", "development").strip() or "development"
UPDATE_CHECK_ENABLED = env_bool("GROWBOOK_UPDATE_CHECK", True)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
# Failed sign-ins are recorded in the database and locked out per username and client address.
AXES_ENABLED = True
AXES_FAILURE_LIMIT = 5
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
# The proxy sets X-Forwarded-For; without it the direct peer address is used.
AXES_IPWARE_META_PRECEDENCE_ORDER = ("HTTP_X_FORWARDED_FOR", "REMOTE_ADDR")
AXES_COOLOFF_TIME = 1
AXES_RESET_ON_SUCCESS = True
AXES_ENABLE_ACCESS_FAILURE_LOG = True
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"axes": {"handlers": ["console"], "level": "INFO", "propagate": False}},
}
