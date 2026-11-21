"""Verify a built production image from inside its container.

Run with the repository as the working directory, as the image's non-root user:

    python tools/check_image.py

`EXPECTED_VERSION` asserts the stamped release version. The check needs no operator
configuration: it starts one throwaway server against a temporary data directory under
`/tmp`, so the container must mount a writable `/tmp` (the shipped Compose file does).
"""

import importlib.util
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Running a script puts its own directory first, so add the repository root for Django.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

LISTEN_PORT = 8000
HEIC_FIXTURE = Path("tests/fixtures/synthetic.heic")
# This private installation deliberately leaves these two deployment warnings open.
ALLOWED_DEPLOYMENT_WARNINGS = {"security.W005", "security.W021"}
SYNTHETIC_SECRET = "synthetic-image-check-key-not-for-runtime-000000000000000000"
STARTUP_TIMEOUT_SECONDS = 300


def verify_identity():
    assert os.geteuid() != 0, "The production image must not run as root."
    present = [name for name in ("pip", "pytest", "playwright") if importlib.util.find_spec(name)]
    assert not present, f"Development tooling is installed: {present}"


def verify_version():
    version = os.environ.get("GROWBOOK_VERSION", "").strip()
    assert version, "GROWBOOK_VERSION is not set in the image."
    expected = os.environ.get("EXPECTED_VERSION", "").strip()
    if expected:
        assert version == expected, (version, expected)
    return version


def verify_heic_decoding():
    import pillow_heif
    from PIL import Image

    pillow_heif.register_heif_opener()
    with Image.open(HEIC_FIXTURE) as image:
        image.load()
        assert image.size == (80, 60), image.size


def verify_deployment_checks():
    import django

    django.setup()
    from django.core.checks import run_checks

    unexpected = {
        issue.id for issue in run_checks(include_deployment_checks=True, databases=["default"])
    }
    unexpected -= ALLOWED_DEPLOYMENT_WARNINGS
    assert not unexpected, sorted(unexpected)


def environment(**overrides):
    values = {key: value for key, value in os.environ.items() if not key.startswith("GROWBOOK_")}
    values["DJANGO_SETTINGS_MODULE"] = "growbook.settings.production"
    values.update(overrides)
    return values


def verify_refuses_incomplete_configuration():
    result = subprocess.run(
        [sys.executable, "-m", "growbook.server"],
        env=environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode != 0, "The server started without operator configuration."
    assert "GROWBOOK_SECRET_KEY" in result.stderr, result.stderr


def wait_for_listener(process, errors):
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            errors.seek(0)
            raise AssertionError(f"The server exited with {process.returncode}: {errors.read()}")
        try:
            with socket.create_connection(("127.0.0.1", LISTEN_PORT), timeout=1):
                return
        except OSError:
            time.sleep(0.5)
    raise AssertionError("The server did not accept connections.")


def verify_startup(data_directory):
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
        process = subprocess.Popen(
            [sys.executable, "-m", "growbook.server"],
            env=environment(
                GROWBOOK_DATA_DIR=str(data_directory),
                GROWBOOK_SECRET_KEY=SYNTHETIC_SECRET,
                GROWBOOK_ALLOWED_HOSTS="127.0.0.1",
                GROWBOOK_TRUST_PROXY="false",
                GROWBOOK_UPDATE_CHECK="false",
            ),
            stdout=subprocess.DEVNULL,
            stderr=errors,
        )
        try:
            wait_for_listener(process, errors)
        finally:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=30)
    database = data_directory / "db.sqlite3"
    assert database.is_file(), database
    with sqlite3.connect(database) as connection:
        migrations = connection.execute("SELECT COUNT(*) FROM django_migrations").fetchone()[0]
        journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert migrations > 0, migrations
    assert journal == "wal", journal
    return migrations


def main():
    verify_identity()
    version = verify_version()
    verify_heic_decoding()
    verify_refuses_incomplete_configuration()
    with tempfile.TemporaryDirectory(prefix="growbook-image-check-") as directory:
        state = Path(directory)
        # The in-process deployment checks need operator settings too.
        os.environ.update(
            GROWBOOK_DATA_DIR=str(state),
            GROWBOOK_SECRET_KEY=SYNTHETIC_SECRET,
            GROWBOOK_ALLOWED_HOSTS="127.0.0.1",
        )
        verify_deployment_checks()
        migrations = verify_startup(state)
    print(f"version {version}; {migrations} migrations; WAL; non-root; no development tooling")


if __name__ == "__main__":
    main()
