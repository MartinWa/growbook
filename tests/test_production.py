import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from growbook.server import server_options


def environment(tmp_path):
    values = os.environ.copy()
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
assert settings.ALLOWED_HOSTS == ['garden.example.internal']
assert not hasattr(settings, 'SECURE_PROXY_SSL_HEADER') or settings.SECURE_PROXY_SSL_HEADER is None
print('secure settings')
""",
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "state").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX modes do not define Windows ACLs.")
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


def test_waitress_proxy_trust_is_off_by_default(monkeypatch):
    monkeypatch.delenv("GROWBOOK_TRUST_PROXY", raising=False)
    options = server_options()
    assert "trusted_proxy" not in options
    assert options["clear_untrusted_proxy_headers"] is True
    assert options["threads"] == 2
    assert options["connection_limit"] == 20


@pytest.mark.parametrize("address", ["", "*", "private-proxy-name"])
def test_waitress_requires_exact_proxy_ip(monkeypatch, address):
    monkeypatch.setenv("GROWBOOK_TRUST_PROXY", "true")
    monkeypatch.setenv("GROWBOOK_TRUSTED_PROXY_IP", address)
    with pytest.raises(ImproperlyConfigured):
        server_options()


def test_waitress_only_trusts_configured_proxy_scheme_header(monkeypatch):
    monkeypatch.setenv("GROWBOOK_TRUST_PROXY", "true")
    monkeypatch.setenv("GROWBOOK_TRUSTED_PROXY_IP", "192.0.2.10")
    options = server_options()
    assert options["trusted_proxy"] == "192.0.2.10"
    assert options["trusted_proxy_headers"] == {"x-forwarded-proto"}
    assert options["trusted_proxy_count"] == 1
