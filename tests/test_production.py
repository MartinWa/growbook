import os
import subprocess
import sys
from pathlib import Path

import pytest

from growbook.server import server_options


def environment(tmp_path):
    values = os.environ.copy()
    values.pop("GROWBOOK_MIGRATE_ON_START", None)
    values.update(
        DJANGO_SETTINGS_MODULE="growbook.settings.production",
        GROWBOOK_SECRET_KEY="synthetic-production-test-key-" * 3,
        GROWBOOK_ALLOWED_HOSTS="garden.example.internal",
        GROWBOOK_DATA_DIR=str(tmp_path / "state"),
        GROWBOOK_TRUST_PROXY="false",
    )
    return values


def run_settings(tmp_path, script, **updates):
    env = environment(tmp_path)
    env.update(updates)
    return subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_production_settings_are_secure_and_do_not_create_state(tmp_path):
    result = run_settings(
        tmp_path,
        """
from django.conf import settings
assert settings.DEBUG is False
assert settings.SESSION_COOKIE_SECURE and settings.CSRF_COOKIE_SECURE
assert settings.SECURE_SSL_REDIRECT
assert settings.MIGRATE_ON_START is True
assert settings.ALLOWED_HOSTS == ['garden.example.internal']
assert not hasattr(settings, 'SECURE_PROXY_SSL_HEADER') or settings.SECURE_PROXY_SSL_HEADER is None
print('secure settings')
""",
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "state").exists()


def test_collected_static_assets_are_readable_without_relaxing_private_uploads(tmp_path):
    result = run_settings(
        tmp_path,
        """
from pathlib import Path
from stat import S_IMODE
import django
django.setup()
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from whitenoise.storage import CompressedManifestStaticFilesStorage

root = Path(settings.DATA_DIR).parent / 'collected-static'
storage = CompressedManifestStaticFilesStorage(
    location=root,
    **settings.STORAGES['staticfiles']['OPTIONS'],
)
name = storage.save('css/synthetic.css', ContentFile(b'body { color: green; }'))
assert storage.file_permissions_mode == 0o644
assert storage.directory_permissions_mode == 0o755
assert S_IMODE(root.stat().st_mode) == 0o755
assert S_IMODE((root / 'css').stat().st_mode) == 0o755
assert S_IMODE(Path(storage.path(name)).stat().st_mode) == 0o644
private = storages['default']
assert private.file_permissions_mode == 0o600
assert private.directory_permissions_mode == 0o700
assert not Path(settings.DATA_DIR).exists()
print('readable static assets; private upload modes unchanged')
""",
    )
    assert result.returncode == 0, result.stderr
    assert "private upload modes unchanged" in result.stdout


@pytest.mark.parametrize(
    "key,value",
    [
        ("GROWBOOK_SECRET_KEY", "short"),
        ("GROWBOOK_SECRET_KEY", "REPLACE_WITH_A_RANDOM_SECRET_AT_LEAST_50_CHARACTERS_LONG"),
        ("GROWBOOK_ALLOWED_HOSTS", "*"),
        ("GROWBOOK_ALLOWED_HOSTS", ""),
        ("GROWBOOK_DATA_DIR", ""),
    ],
)
def test_production_refuses_missing_or_placeholder_configuration(tmp_path, key, value):
    result = run_settings(
        tmp_path, "from django.conf import settings; print(settings.SECRET_KEY)", **{key: value}
    )
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr


def test_server_refuses_missing_or_outdated_database(tmp_path):
    check = """
import django
django.setup()
from growbook.server import assert_current_schema
assert_current_schema()
"""
    result = run_settings(tmp_path, check, GROWBOOK_MIGRATE_ON_START="false")
    assert result.returncode != 0
    assert "does not exist" in result.stderr
    assert "manage.py migrate" in result.stderr
    assert not (tmp_path / "state").exists()

    (tmp_path / "state").mkdir()
    outdated = """
import django
django.setup()
from django.core.management import call_command
call_command("migrate", "core", "0002", verbosity=0, interactive=False)
from growbook.server import assert_current_schema
try:
    assert_current_schema()
except Exception as error:
    print(type(error).__name__)
    print(error)
else:
    raise SystemExit("expected an outdated database to be refused")
"""
    result = run_settings(tmp_path, outdated, GROWBOOK_MIGRATE_ON_START="false")
    assert result.returncode == 0, result.stderr
    assert "ImproperlyConfigured" in result.stdout
    assert "core.0003_register_photo_owners" in result.stdout

    upgraded = """
import django
django.setup()
from django.core.management import call_command
call_command("migrate", verbosity=0, interactive=False)
from growbook.server import assert_current_schema
assert_current_schema()
print("schema current")
"""
    result = run_settings(tmp_path, upgraded, GROWBOOK_MIGRATE_ON_START="false")
    assert result.returncode == 0, result.stderr
    assert "schema current" in result.stdout


def test_startup_migration_can_be_disabled(tmp_path):
    result = run_settings(
        tmp_path,
        """
from django.conf import settings
assert settings.MIGRATE_ON_START is False
print("manual migrations")
""",
        GROWBOOK_MIGRATE_ON_START="false",
    )
    assert result.returncode == 0, result.stderr
    assert "manual migrations" in result.stdout


def test_server_applies_pending_migrations_by_default(tmp_path):
    (tmp_path / "state").mkdir()
    result = run_settings(
        tmp_path,
        """
import django
django.setup()
from django.core.management import call_command
call_command("migrate", "core", "0002", verbosity=0, interactive=False)
from django.db import connection
with connection.cursor() as cursor:
    cursor.execute("PRAGMA table_info(core_photo)")
    assert "area_id" not in [row[1] for row in cursor.fetchall()]
from growbook.server import assert_current_schema, migrate_on_start
migrate_on_start()
assert_current_schema()
with connection.cursor() as cursor:
    cursor.execute("PRAGMA table_info(core_photo)")
    assert "area_id" in [row[1] for row in cursor.fetchall()]
print("auto-migrated")
""",
    )
    assert result.returncode == 0, result.stderr
    assert "auto-migrated" in result.stdout


def test_server_checks_the_schema_before_serving(monkeypatch):
    import growbook.server as server

    calls = []
    monkeypatch.setattr(server.settings, "DEBUG", False)
    monkeypatch.setattr(server.settings, "MIGRATE_ON_START", False)
    monkeypatch.setattr(server, "assert_current_schema", lambda: calls.append("schema"))
    monkeypatch.setattr(server, "serve", lambda application, **options: calls.append("serve"))

    server.main()

    assert calls == ["schema", "serve"]


def test_server_migrates_before_serving_by_default(monkeypatch):
    import growbook.server as server

    calls = []
    monkeypatch.setattr(server.settings, "DEBUG", False)
    monkeypatch.setattr(server.settings, "MIGRATE_ON_START", True)
    monkeypatch.setattr(server, "migrate_on_start", lambda: calls.append("migrate"))
    monkeypatch.setattr(server, "serve", lambda application, **options: calls.append("serve"))

    server.main()

    assert calls == ["migrate", "serve"]


def test_waitress_proxy_trust_is_off_by_default(monkeypatch):
    monkeypatch.delenv("GROWBOOK_TRUST_PROXY", raising=False)
    monkeypatch.setenv("GROWBOOK_TRUSTED_PROXY_IP", "192.0.2.10")
    options = server_options()
    assert "trusted_proxy" not in options
    assert options["clear_untrusted_proxy_headers"] is True
    assert options["threads"] == 2
    assert options["connection_limit"] == 20


def test_waitress_only_trusts_proxy_scheme_header_without_a_proxy_ip(monkeypatch):
    monkeypatch.setenv("GROWBOOK_TRUST_PROXY", "true")
    monkeypatch.delenv("GROWBOOK_TRUSTED_PROXY_IP", raising=False)
    options = server_options()
    assert options["trusted_proxy"] == "*"
    assert options["trusted_proxy_headers"] == {"x-forwarded-proto"}
    assert options["trusted_proxy_count"] == 1
    assert options["clear_untrusted_proxy_headers"] is True


@pytest.mark.parametrize("trust_proxy", ["false", "true"])
def test_production_proxy_requests_with_changing_peers(tmp_path, trust_proxy):
    result = run_settings(
        tmp_path,
        """
import json
from http.client import HTTPConnection
from threading import Thread

import django
django.setup()
from django.conf import settings
from django.core.handlers.wsgi import WSGIHandler
from django.http import JsonResponse
from django.urls import path
from waitress.server import create_server
from growbook.server import server_options

# Exercise production HTTPS settings with a view that needs no database or static assets.
settings.MIDDLEWARE = ['django.middleware.security.SecurityMiddleware']
settings.ROOT_URLCONF = '__main__'

def probe(request):
    return JsonResponse({
        'secure': request.is_secure(),
        'host': request.get_host(),
        'peer': request.META['REMOTE_ADDR'],
        'forwarding': {
            name: value for name, value in request.META.items()
            if name.startswith('HTTP_X_FORWARDED_') or name == 'HTTP_FORWARDED'
        },
    })

urlpatterns = [path('probe/', probe)]
options = server_options()
options.update(listen='127.0.0.1:0', asyncore_loop_timeout=0.05)
server = create_server(WSGIHandler(), **options)
thread = Thread(target=server.run, daemon=True)
thread.start()
try:
    # Linux loopback supplies two real peer addresses without external network access.
    for peer in ('127.0.0.1', '127.0.0.2'):
        for proto in (None, 'http', 'https'):
            headers = {
                'Host': 'garden.example.internal',
                'Connection': 'close',
                'Forwarded': 'for=203.0.113.50;host=untrusted.example.invalid;proto=https',
                'X-Forwarded-For': '203.0.113.50',
                'X-Forwarded-Host': 'untrusted.example.invalid',
                'X-Forwarded-Port': '444',
                'X-Forwarded-By': '203.0.113.51',
            }
            if proto is not None:
                headers['X-Forwarded-Proto'] = proto
            connection = HTTPConnection(
                '127.0.0.1', int(server.effective_port), timeout=5,
                source_address=(peer, 0),
            )
            try:
                connection.request('GET', '/probe/', headers=headers)
                response = connection.getresponse()
                body = response.read()
                if settings.SECURE_PROXY_SSL_HEADER and proto == 'https':
                    assert response.status == 200, (peer, proto, response.status, body)
                    assert response.getheader('Location') is None
                    assert json.loads(body) == {
                        'secure': True,
                        'host': 'garden.example.internal',
                        'peer': peer,
                        'forwarding': {'HTTP_X_FORWARDED_PROTO': 'https'},
                    }
                    assert response.getheader('Strict-Transport-Security')
                else:
                    assert response.status == 301, (peer, proto, response.status, body)
                    assert response.getheader('Location') == 'https://garden.example.internal/probe/'
            finally:
                connection.close()
finally:
    server.task_dispatcher.shutdown()
    server.close()
    thread.join(timeout=5)
    assert not thread.is_alive()
print('production proxy requests verified')
""",
        GROWBOOK_TRUST_PROXY=trust_proxy,
        GROWBOOK_TRUSTED_PROXY_IP="192.0.2.10",
    )
    assert result.returncode == 0, result.stderr
    assert "production proxy requests verified" in result.stdout
