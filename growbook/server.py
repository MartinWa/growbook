"""One production WSGI process with explicit trusted-proxy configuration."""

import os
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from waitress import serve


def server_options():
    options = {
        "listen": "0.0.0.0:8000",
        "threads": 2,
        "connection_limit": 20,
        "max_request_body_size": 85_000_000,
        "clear_untrusted_proxy_headers": True,
    }
    if os.environ.get("GROWBOOK_TRUST_PROXY", "false").lower() in {"true", "1"}:
        # The private network and a proxy that sets these headers establish trust.
        # Waitress resolves REMOTE_ADDR from the client address header, which the
        # per-address sign-in limit reads instead of trusting a request header itself.
        options.update(
            trusted_proxy="*",
            trusted_proxy_count=1,
            trusted_proxy_headers={"x-forwarded-proto", "x-forwarded-for"},
        )
    return options


def pending_migrations(connection):
    """Return the migration plan required by the code loaded in this process."""
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    return executor.migration_plan(executor.loader.graph.leaf_nodes())


def assert_current_schema():
    """Refuse a missing or outdated database instead of serving broken pages."""
    from django.db import connections

    database = Path(connections["default"].settings_dict["NAME"])
    if not database.is_file():
        raise ImproperlyConfigured(
            f"Database {database} does not exist. Initialize this instance with "
            "`python manage.py migrate` before starting the server."
        )
    plan = pending_migrations(connections["default"])
    if plan:
        names = ", ".join(f"{migration.app_label}.{migration.name}" for migration, _ in plan)
        raise ImproperlyConfigured(
            f"Database {database} is out of date. Run `python manage.py migrate` "
            f"before starting the server. Migration plan: {names}."
        )


def verify_data_directory():
    """Refuse a missing or unwritable data directory with the fix, not an opaque SQLite error."""
    from django.db import connections

    directory = Path(connections["default"].settings_dict["NAME"]).parent
    if not directory.is_dir():
        raise ImproperlyConfigured(
            f"Data directory {directory} does not exist. Create it and give it to the configured "
            "non-root user before starting the server; see docs/deployment.md."
        )
    try:
        # An exclusively created temporary file keeps concurrent starts from sharing a
        # probe name: separate containers can hold the same process ID.
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".write-probe-"):
            pass
    except OSError as exc:
        raise ImproperlyConfigured(
            f"Data directory {directory} is not writable: {exc}. The configured non-root user "
            "must own it, for example `sudo chown <APP_UID>:<APP_GID> <APP_DATA_PATH>`."
        ) from exc


def migrate_on_start():
    """Apply pending migrations under a data-directory lock, then verify the schema."""
    from django.core.management import call_command
    from django.core.management.base import CommandError
    from django.db import connections
    from filelock import FileLock, Timeout

    database = Path(connections["default"].settings_dict["NAME"])
    data_directory = database.parent
    verify_data_directory()
    lock = FileLock(
        data_directory / ".migrate.lock",
        timeout=settings.MIGRATION_LOCK_TIMEOUT,
        mode=0o600,
    )
    try:
        with lock:
            call_command("migrate", interactive=False, verbosity=1)
    except Timeout as exc:
        raise ImproperlyConfigured(
            f"Timed out waiting for another process to migrate {database}."
        ) from exc
    except CommandError as exc:
        raise ImproperlyConfigured(f"Database migration failed for {database}: {exc}") from exc
    assert_current_schema()


def main():
    from .wsgi import application

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise ImproperlyConfigured("Run the production application as a non-root user.")
    if settings.DEBUG:
        raise ImproperlyConfigured("The production server must not use development settings.")
    options = server_options()
    if settings.MIGRATE_ON_START:
        migrate_on_start()
    else:
        verify_data_directory()
        assert_current_schema()
    serve(application, **options)


if __name__ == "__main__":
    main()
