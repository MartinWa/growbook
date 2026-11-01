"""One production WSGI process with explicit trusted-proxy configuration."""

import ipaddress
import os

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


def main():
    from .wsgi import application

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise ImproperlyConfigured("Run the production application as a non-root user.")
    if settings.DEBUG:
        raise ImproperlyConfigured("The production server must not use development settings.")
    serve(application, **server_options())


if __name__ == "__main__":
    main()
