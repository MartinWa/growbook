"""One production WSGI process with explicit trusted-proxy configuration."""

import ipaddress
import os
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
        proxy = os.environ.get("GROWBOOK_TRUSTED_PROXY_IP", "")
        if not proxy or proxy == "*":
            raise ImproperlyConfigured("Set an explicit GROWBOOK_TRUSTED_PROXY_IP, not a wildcard.")
        try:
            ipaddress.ip_address(proxy)
        except ValueError as exc:
            raise ImproperlyConfigured("GROWBOOK_TRUSTED_PROXY_IP must be an IP address.") from exc
        options.update(
            trusted_proxy=proxy,
            trusted_proxy_count=1,
            trusted_proxy_headers={"x-forwarded-proto"},
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


def main():
    from .wsgi import application

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise ImproperlyConfigured("Run the production application as a non-root user.")
    if settings.DEBUG:
        raise ImproperlyConfigured("The production server must not use development settings.")
    assert_current_schema()
    serve(application, **server_options())


if __name__ == "__main__":
    main()
