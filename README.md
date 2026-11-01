# growbook

A private, self-hosted garden diary built with Python and Django. Capture an
observation with text, photos, or both, then browse it in your web browser.
The interface defaults to Swedish, with English available.

## Current features

- Administrator-managed accounts, login/logout and a shared diary; no public signup.
- Dated text-only, photo-only and text-plus-photo entries, with chronological paging.
- Separate camera and photo-library inputs, multiple photos, previews and upload feedback.
- JPEG, PNG, WebP and HEIC validation, orientation correction and metadata-stripped
  WebP display images/thumbnails. Received originals are retained privately.
- Authenticated photo viewing in a CSP-safe HTML page and private original downloads;
  no public media directory.
- Retry-safe creation and visible errors without silently accepting partial photo saves.
- Operational database/photo backup, verification, clean-instance restore and orphan cleanup.

## Scope and limitations

- The diary is shared within an installation: every active account can read all
  entries, photos and original downloads. There are no per-user diaries.
- Entries can be created and read, but not edited or deleted. Growing-area, crop
  and variety registers, planting plans, diary links and portable data import/export
  are not provided.
- Uploads are limited to 10 photos per entry, 20 MiB per photo, 80 MiB in total and
  50 million decoded pixels per photo. Animated and multi-image files are rejected.
- Deployments are intended for private LAN/VPN access over HTTPS, not public
  Internet exposure. Verify backup and clean-instance recovery before recording
  real data.

## Requirements

- [uv](https://docs.astral.sh/uv/) for dependency management. `.python-version`
  pins Python 3.14.4; `uv.lock` pins the dependency set, including Django 5.2 LTS.
- A local filesystem for SQLite and photos, with a fixed SQLite runtime as
  described [below](#sqlite-runtime). SMB/NFS-mounted databases are not supported.
- Docker with Linux-container support and Compose v2 for the optional
  [container workflow](#build-and-run-with-docker).

Run all commands from the repository root. There is no frontend build step or
runtime CDN dependency: CSS, HTMX and the small JavaScript modules are self-hosted.

## Run locally

```sh
uv sync --locked
```

On **Windows x64**, install the project-local, patched SQLite DLL before running
Django or tests:

```sh
uv run python tools/bootstrap_sqlite.py
```

On Linux/macOS, provide a fixed native SQLite runtime instead; the Windows helper
is not supported on those platforms.

Then compile translations, create the database and your administrator account:

```sh
uv run python tools/compile_translations.py
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver 127.0.0.1:8000
```

Open **http://127.0.0.1:8000/**. Use **`/operator/`** to create or deactivate other
accounts. There are no seeded credentials. Diary/photo admin screens are
read-only; entries are created through the capture page.

`manage.py` defaults to development settings with loopback-only allowed hosts,
HTTP cookies, a generated local secret and data in ignored `local/development/`.
Do not expose the development server or point it at production data. A local
`.env` file is **not automatically loaded**; local configuration overrides must
be set in the process environment.

### SQLite runtime

The available Windows Python runtimes bundle SQLite 3.50.4, which lacks the
upstream WAL-reset fix. The bootstrap helper downloads SQLite 3.51.3, verifies
its checksum and writes only `local/sqlite/sqlite3.dll`. It does not modify the
global Python installation. Restart existing application/test processes after
updating that DLL.

Other hosts need SQLite 3.51.3+ or a supported fixed backport (3.50.7 or 3.44.6
within their respective release series). Application checks reject unfixed
runtimes; installing a newer Python package alone does not establish SQLite safety.
The Dockerfile builds a pinned, fixed SQLite from source.

SQLite connections use WAL, `synchronous=FULL`, a 10-second busy timeout and
`IMMEDIATE` for explicit transactions. Image processing and file copying happen
outside short database writes.

## Test and check

```sh
uv run python tools/run_tests.py -q
uv run ruff check .
uv run ruff format --check .
uv run python manage.py check --database default
uv run python manage.py makemigrations --check --dry-run
```

Use `tools/run_tests.py`, especially on Windows: it activates SQLite before test
plugins can import it, compiles translations and keeps temporary artifacts under
ignored `local/`. Tests use a real file-backed SQLite/WAL database and synthetic
images, covering capture, image failures, authorization, retries, concurrency
and clean-instance recovery. Browser tests are excluded from the default run.

### Browser tests

Install Playwright's Chromium, then run the browser suite separately:

```sh
uv run python -m playwright install chromium --no-shell
uv run python tools/run_tests.py -q -m browser
```

To explicitly use an already-installed Chrome instead:

```sh
# POSIX shell
GROWBOOK_BROWSER_CHANNEL=chrome uv run python tools/run_tests.py -q -m browser
```

```powershell
# PowerShell
$env:GROWBOOK_BROWSER_CHANNEL = "chrome"
uv run python tools/run_tests.py -q -m browser
```

These tests exercise Chromium and real browser/form interaction. They do not
verify a phone's native camera/library picker or a deployment's private-network
routing; check those on the intended devices and installation.

### GitHub Actions

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on pull requests,
pushes to `main`, and manual dispatch. Windows jobs check locked dependencies,
the generated Docker requirements, Ruff, translations, database/migration checks,
and the application and Chromium suites using the fixed SQLite helper. The Linux
job validates workflow syntax, builds the production image, and checks migrations,
deployment settings and non-root WSGI initialization with disposable storage.
Unexpected deployment-check issues fail CI; W005/W021 remain visible. CI does not
publish images, upload test state or deploy the application.

## Build and run with Docker

The Dockerfile builds a Linux production image with Waitress. The supplied Compose
configuration runs one application process with two threads behind an existing
private HTTPS reverse proxy. A running Docker daemon is required; verify the build
and deployment on your target platform.

### Build an image

```sh
docker build --tag growbook:local .
```

The multi-stage Dockerfile pins the Python base image by digest, verifies native
source checksums, installs hash-pinned Python dependencies, compiles translations
and collects static assets. It builds SQLite and a decoder-only HEIC library
chain. Build-time checks also initialize WSGI as the default non-root user to
catch unreadable collected assets. No local databases, photos, `.env` files or
virtual environments enter the build context.

**HEIC packaging:** upstream `pillow-heif` development wheels bundle the GPL x265
encoder. The production image instead builds its BSD wrapper from source against
dynamically linked LGPL decoder libraries without x265. Do not copy development
wheel binaries into a distributable image. Review the
[dependency notices](third_party/README.md) before publishing binaries/images.

### Configure the private installation

Copy `.env.example` to an untracked `.env` (`cp .env.example .env` on a POSIX
shell, or `Copy-Item .env.example .env` in PowerShell). Replace every placeholder;
the example is deliberately not runnable production configuration.

Generate a secret, for example:

```sh
uv run python -c "import secrets; print(secrets.token_urlsafe(64))"
```

| Setting | Meaning |
|---|---|
| `GROWBOOK_SECRET_KEY` | Random secret, at least 50 characters; keep private and preserve for recovery. |
| `GROWBOOK_ALLOWED_HOSTS` | Comma-separated exact hostnames, without schemes; no wildcard. |
| `GROWBOOK_CSRF_TRUSTED_ORIGINS` | Comma-separated trusted HTTPS origins. |
| `GROWBOOK_TIME_ZONE` | Garden timezone; defaults to `Europe/Stockholm`. |
| `GROWBOOK_TRUST_PROXY` | Set `true` only for a trusted TLS-terminating proxy that strips/replaces forwarding headers. |
| `GROWBOOK_TRUSTED_PROXY_IP` | Exact proxy IP when trust is enabled; wildcard trust is rejected. |
| `APP_UID`, `APP_GID` | Non-root numeric process IDs. |
| `APP_DATA_PATH` | Absolute private directory on the application host's local filesystem. |
| `PROXY_NETWORK` | Existing private Docker network used by your managed reverse proxy. |

Create the data directory and give the configured UID/GID the necessary access
**before** starting containers. The image does not implement `PUID`/`PGID`.
Compose mounts that directory at `/data`, sets production settings, uses a
read-only root filesystem and temporary scratch storage, and limits log retention.

Configure the existing reverse proxy to reach the application on container port
`8000` over the private network. When the proxy terminates HTTPS and forwards HTTP,
set proxy trust and its exact IP so the application recognizes the external HTTPS
scheme. Do not relax secure cookies or HTTPS redirects to work around proxy errors.

There are **no published host ports or bundled proxy** in `compose.yaml`. Keep
access private through LAN/VPN and HTTPS; do not add public DNS exposure or router
port forwarding.

### Initialize and start

After configuration, let Compose build the image and run the setup commands:

```sh
docker compose config --quiet
docker compose build growbook
docker compose run --rm growbook python manage.py migrate --noinput
docker compose run --rm growbook python manage.py createsuperuser
docker compose run --rm growbook python manage.py check --deploy --database default
docker compose up -d growbook
docker compose logs --tail=100 growbook
```

Production settings deliberately leave HSTS include-subdomains/preload disabled;
`check --deploy` reports W005/W021 because this private installation does not claim
control of sibling domains or enroll in public preload. Other security warnings
need investigation.

Before recording real data, verify login/logout, text/photo capture and denial of
anonymous access through the configured HTTPS hostname on the intended devices.
Create a backup, keep an independent copy and verify a clean-instance restore.
Repeat these checks after changes to routing, storage or media handling.

## Backup and recovery

Never back up a live SQLite installation by copying its database file. The
implemented commands use SQLite's backup API and coordinate the snapshot with
immutable original photos.

For a disposable local instance after the setup above:

```sh
uv run python manage.py backup_garden local/backups/example-001 --config local/development/.development-secret
uv run python manage.py verify_backup local/backups/example-001
uv run python manage.py restore_garden local/backups/example-001 local/restored-example
```

Supply every private configuration file needed for recovery using repeated
`--config` arguments; the local example includes only the generated development
secret. Container operations must use explicitly mounted backup/configuration
paths; Compose does not mount a backup destination or its `.env` file by default.

Backups include accounts/password hashes, database records, originals and a
checksum manifest: protect them as sensitive data and keep an independent copy.
Restore requires a **new, nonexistent destination**, regenerates display images,
and places recovered configuration in `recovered-configuration/` for deliberate
review rather than applying it automatically. It is not a portable data importer.
See the [recovery notes](docs/backup-and-recovery.md) for the complete procedure.

Orphan cleanup is a dry run unless explicitly applied:

```sh
uv run python manage.py cleanup_media
uv run python manage.py cleanup_media --apply
```

It is age/count bounded, coordinates with in-flight saves and never scans backup
storage. Keep backups outside live media storage.

## Project layout

| Path | Purpose |
|---|---|
| `growbook/` | Project settings, routing, runtime bootstrap and WSGI server. |
| `core/` | Users, private photos, security, SQLite configuration and operational commands. |
| `diary/` | Diary models, forms, views and validated creation operations. |
| `templates/`, `static/` | Server-rendered HTML and self-hosted CSS/JavaScript. |
| `locale/` | Maintained translation catalogs; compiled `.mo` files are generated. |
| `tests/` | Domain/integration/browser tests and synthetic fixtures. |
| `tools/` | SQLite setup, translation compilation and test entry point. |
| `docs/` | Detailed operational notes that do not belong in this README. |
| `third_party/` | Dependency licenses, notices and native build provenance. |

`.venv/`, `local/`, `staticfiles/`, `.env`, databases and live media/backups are
ignored by Git. Private material and local tool/browser state are excluded from
Git and container build contexts. Migrations, dependency locks, source translations
and synthetic fixtures belong in source control.

## License

Growbook is **source-available under the
[PolyForm Noncommercial License 1.0.0](LICENSE)**, not OSI open-source software.
The license text governs permitted use, modification and distribution. Separately
licensed dependencies retain their own terms; the project license does not claim
ownership of user-entered garden records or photos.

For engineering conventions and expectations for contributors and coding
assistants, see [AGENTS.md](AGENTS.md).
