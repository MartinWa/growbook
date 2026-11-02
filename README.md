# growbook

A private, self-hosted garden diary and registers built with Python and Django.
Capture an observation with text, photos, or both, and maintain crops, varieties
and growing areas in your web browser.
The interface defaults to Swedish, with English available.

## Current features

- Administrator-managed accounts, login/logout and a shared garden; no public signup.
- Crop, variety and optionally nested growing-area registers, with compact tables,
  independently expandable details, editing and non-cascading archive.
- Unicode-aware scoped name uniqueness, cycle-safe area organization and confirmed
  deletion of records without protected references.
- Multiple private photos on varieties/areas, with editable captions/order and
  confirmed photo deletion.
- Dated text-only, photo-only and text-plus-photo entries, with chronological paging.
- Separate camera and photo-library inputs, multiple photos, previews and upload feedback.
- JPEG, PNG, WebP and HEIC validation, orientation correction and metadata-stripped
  WebP display images/thumbnails. Received originals are retained privately.
- Authenticated photo viewing in a CSP-safe HTML page and private original downloads;
  no public media directory.
- Retry-safe diary creation and register saves, with visible errors and no partial
  photo saves.
- Operational database/photo backup, verification, clean-instance restore and orphan cleanup.

## Scope and limitations

- The garden is shared within an installation: every active account can read all
  records, photos and original downloads, and maintain the registers. There are
  no per-user gardens or read-only member roles.
- Diary entries can be created and read, but not edited or deleted. Planting plans,
  year histories, diary links and portable data import/export are not provided.
- Uploads are limited to 10 photos per entry or register record, 20 MiB per photo,
  80 MiB per upload and 50 million decoded pixels per photo. Animated and
  multi-image files are rejected.
- Deployments are intended for private LAN/VPN access over HTTPS, not public
  Internet exposure. Verify backup and clean-instance recovery before recording
  real data.

## Requirements

- [uv](https://docs.astral.sh/uv/) for dependency management. `.python-version`
  pins Python 3.14.10; `uv.lock` pins the dependency set, including Django 5.2 LTS.
  Use uv 0.13.6 or newer to obtain this Python runtime; CI pins uv 0.13.6.
  `requires-python` allows 3.14.9 for dependency tooling compatibility, but the
  development and production runtime pins remain 3.14.10 for its security fixes.
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
accounts. There are no seeded credentials. Diary/photo/register admin screens are
read-only; use the capture page for observations and **Registers** for garden
records.

`manage.py` defaults to development settings with loopback-only allowed hosts,
HTTP cookies, a generated local secret and data in ignored `local/development/`.
Do not expose the development server or point it at production data. A local
`.env` file is **not automatically loaded**; local configuration overrides must
be set in the process environment.

### Registers

Open **Registers** to switch between growing areas, crops and varieties. Each
compact table has a labelled **Details** control: several rows can stay expanded
at once, and ordinary record links work without JavaScript.

- Crop names are unique globally; variety names are unique within a crop. Area
  names are unique among top-level areas or among siblings with the same parent.
  Comparison keys use Unicode NFC, collapsed whitespace and casefolding; display
  names and user-entered notes are not translated. Archived records still reserve
  their names.
- Areas may have a parent, arbitrary descriptive type and independently optional
  positive length/width in centimetres. A parent cannot be itself or a descendant;
  dimensions do not enforce a rectangular layout or capacity.
- Archive hides a record from default lists and new associations, not from direct
  links or existing associations. It does not archive child areas or varieties.
  Use **Include archived records** to find and restore it to active selections.
- Edit an area or variety to add photos; edit photo details to change captions or
  ordering. Deletion requires confirmation. A crop with varieties or an area with
  children cannot be deleted; archive it or deliberately reorganize its dependents.
  Allowed owner deletion removes its photos, with coordinated file cleanup after
  the database commit.

Register forms reject stale edits instead of overwriting another member's changes.
On a failed or uncertain create/edit save, keep the form open and retry the same
content and save key; photos are not appended twice. With JavaScript, validation
replaces only the error summary, preserving fields and selected files. Without it,
reselect photos as instructed before retrying. A genuinely changed form is a new
submission; do not reuse a confirmed key for different content.

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

For UI-copy changes, update the English source messages and
`locale/sv/LC_MESSAGES/django.po`, then recompile with
`tools/compile_translations.py`. Compiled `.mo` files are generated; tests and
Docker compile them automatically.

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
pushes to `main`, manual dispatch, and weekly. Windows jobs check locked dependencies,
the generated Docker requirements, Ruff, translations, database/migration checks,
and the application and Chromium suites using the fixed SQLite helper. The Linux
job validates workflow syntax, builds the production image, and checks migrations,
deployment settings and non-root WSGI initialization with disposable storage.
Unexpected deployment-check issues fail CI; W005/W021 remain visible. Python audits
check all locked application/development and build-only dependencies with `pip-audit`.
Trivy checks the final image and fails on fixable high/critical vulnerabilities.
These checks do not prove that custom-built native libraries are vulnerability-free.
CI does not publish images, upload test state or deploy the application.

### Dependency updates

[`.github/dependabot.yml`](.github/dependabot.yml) requests weekly updates for uv,
GitHub Actions and the Docker base image. Updates require review and passing CI;
Django stays on 5.2 LTS and Python stays on the current minor series. Dependabot
resolves with its bundled Python 3.14.9; CI and containers use the pinned 3.14.10
runtime. After a uv update, regenerate the Docker requirements on the PR branch
before merging:

```sh
uv export --locked --no-dev --no-emit-project --format requirements-txt --output-file requirements.txt
```

Review `requirements-build.txt`, the pinned uv/audit tools, checksum-pinned native
sources and vendored HTMX manually. Base-image Python patch updates must also stay
aligned with `.python-version` and `pyproject.toml`.

### Release images

[`.github/workflows/release.yml`](.github/workflows/release.yml) creates a GitHub
Release and publishes a Linux/amd64 image to `ghcr.io/martinwa/growbook` when a new
`X.Y.Z` tag is pushed. The Git tag is the release version, used for the image tag
and OCI version label; no version bump in `pyproject.toml` or `uv.lock` is needed.
The static project version is local tooling metadata, not the container release
version. Tags have no `v` prefix or prerelease suffix and must point to a commit
from `main` whose push CI run has passed. Merge the changes, wait for that
commit's CI, then tag it:

```sh
git switch main
git pull --ff-only
git tag -a X.Y.Z -m "Release X.Y.Z"
git push origin X.Y.Z
```

Replace `X.Y.Z` with a new release version. No manual GitHub Release creation is
needed; watch **Actions → Release image** for completion. A manually published
stable GitHub Release remains a supported trigger. Tag deletion is ignored, and
moving an existing tag is rejected; correct failures by rerunning the failed
workflow, not by force-pushing tags.

The workflow rebuilds, smoke-checks and audits the image before pushing its `X.Y.Z`
tag, then attaches provenance and SBOM attestations. Only after those steps succeed
does it create or publish the GitHub Release with generated notes, preserving an
existing release's notes. If the version is the highest published stable version
and GitHub identifies it as the current release, `:latest` updates to the same
image digest. Existing image version tags are not overwritten; prereleases and
older releases do not update `:latest`. A rerun after the image has been pushed
stops at the no-overwrite guard and requires investigation. There is no automatic
deployment. Use version tags or digests when you need reproducible deployments.
The server applies pending migrations at startup by default. Set
`GROWBOOK_MIGRATE_ON_START=false` to run migrations as a separate deployment step
instead; see [Upgrade an existing installation](#upgrade-an-existing-installation).

Before recommending a published image, verify anonymous pulls and its attestation.
Keep deployment configuration private and pin the published digest rather than a
moving tag. `compose.yaml` continues to build locally unless deliberately overridden.

```sh
gh attestation verify oci://ghcr.io/martinwa/growbook:X.Y.Z --repo MartinWa/growbook
```

Review the [native distribution requirements](third_party/README.md) before each
release. The SBOM and vulnerability scan are useful evidence, not a complete
licensing or security guarantee.

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
chain; pip is removed from the final runtime image. Build-time checks also initialize
WSGI as the default non-root user to catch unreadable collected assets. No local
databases, photos, `.env` files or virtual environments enter the build context.

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
| `GROWBOOK_MIGRATE_ON_START` | Apply pending schema migrations when the server starts; defaults to `true`. Set `false` for a separate migration step. |
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

The explicit `migrate` initializes the schema before `createsuperuser`. Later starts
apply pending migrations automatically when `GROWBOOK_MIGRATE_ON_START` is enabled.

Production settings deliberately leave HSTS include-subdomains/preload disabled;
`check --deploy` reports W005/W021 because this private installation does not claim
control of sibling domains or enroll in public preload. Other security warnings
need investigation.

Before recording real data, verify login/logout, text/photo capture and denial of
anonymous access through the configured HTTPS hostname on the intended devices.
Create a backup, keep an independent copy and verify a clean-instance restore.
Repeat these checks after changes to routing, storage or media handling.

### Upgrade an existing installation

The server applies pending migrations to the mounted database when it starts. That
is the default for this single-container image, so an image update does not require a
separate `migrate` command. Create and verify a backup before changing the schema,
and keep the application stopped while the backup runs.

```sh
docker compose build growbook
docker compose stop growbook
# Create and verify a backup with docs/backup-and-recovery.md before continuing.
docker compose up -d growbook
docker compose logs --tail=100 growbook
docker compose run --rm growbook python manage.py migrate --check
docker compose run --rm growbook python manage.py showmigrations core diary garden
```

Use `docker compose pull growbook` instead of `build` when the service is configured
to use the published image. The startup migration is serialized with a lock in
`GROWBOOK_DATA_DIR`. If it fails, the server does not start; inspect the logs before
retrying. `showmigrations` should show `[X]` for every migration, and `migrate --check`
must succeed.

For deployments that must run migrations separately (for example, several replicas
or an external migration job), set `GROWBOOK_MIGRATE_ON_START=false`. Then stop the
application, run the migration step explicitly, verify it, and start the new image:

```sh
docker compose stop growbook
docker compose run --rm growbook python manage.py migrate --noinput
docker compose run --rm growbook python manage.py migrate --check
docker compose up -d growbook
```

With startup migration disabled, the server refuses to start while migrations are
pending. Do not start or restart the application while a backup is running; a
startup migration is a schema change.

If a page returns a 500 after an upgrade and the log reports a missing column or
table, the image and the mounted database disagree. Confirm that both commands use
the intended data directory, apply the migrations and restart. Do not delete the
database, use `--fake`, or edit the schema by hand.

## Backup and recovery

Use the [operational recovery guide](docs/backup-and-recovery.md) for the
backup, verification, clean-instance restore and orphan-cleanup commands.
It is the canonical procedure, including local and container configuration notes.

Never copy a live SQLite database file to back it up. The supported backup
coordinates a database snapshot with private originals and recovery configuration.
Protect backups as sensitive data, keep an independent copy, and verify restore
into a **new, nonexistent destination** before real use. This is operational
recovery, not portable data import/export.

## Project layout

| Path | Purpose |
|---|---|
| `growbook/` | Project settings, routing, runtime bootstrap and WSGI server. |
| `core/` | Users, private photos, security, SQLite configuration and operational commands. |
| `diary/` | Diary models, forms, views and validated creation operations. |
| `garden/` | Crop, variety and growing-area registers, forms and validated writes. |
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

## Security

Report vulnerabilities through the private channel described in
[SECURITY.md](SECURITY.md). Use synthetic examples in public issues and PRs;
never attach real photos, backups, credentials or private configuration.

## License

Growbook is **source-available under the
[PolyForm Noncommercial License 1.0.0](LICENSE)**, not OSI open-source software.
The license text governs permitted use, modification and distribution. Separately
licensed dependencies retain their own terms; the project license does not claim
ownership of user-entered garden records or photos.

For engineering conventions and expectations for contributors and coding
assistants, see [AGENTS.md](AGENTS.md).
