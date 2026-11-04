# Deploying growbook

Canonical guide for container builds, private installation configuration and upgrades.
The [main README](../README.md#run-with-docker) has the short version; keep deployment
procedures here.

## SQLite runtime

Development, tests and production run in Linux containers with checksum-pinned SQLite
3.51.3 built from source. Application checks reject runtimes without the WAL-reset fix;
recognized fixed backports are 3.50.7 and 3.44.6 within their release series. Installing
a newer Python package alone does not establish SQLite safety. Native Windows execution
and the former project-local DLL installer are unsupported; see
[Container development](development.md#container-development).

Connections use WAL, `synchronous=FULL`, a 10-second busy timeout and `IMMEDIATE` for
explicit transactions. Image processing and file copying happen outside short database
writes.

## Build and run with Docker

The Dockerfile builds a Linux production image with Waitress. Compose runs one application
process with two threads behind an existing private HTTPS reverse proxy. A running Docker
daemon is required; verify the build and deployment on the target platform.

### Build an image

```sh
docker build --tag growbook:local .
```

The multi-stage Dockerfile pins the Python base image by digest, verifies native source
checksums, installs hash-pinned Python dependencies, compiles translations and collects
static assets. It builds SQLite and a decoder-only HEIC library chain; pip is removed from
the final runtime image. Build-time checks initialize WSGI as the default non-root user to
catch unreadable collected assets. No local databases, photos, `.env` files or virtual
environments enter the build context.

**HEIC packaging:** upstream `pillow-heif` wheels bundle the GPL x265 encoder.
Development and production images share the BSD wrapper built from source against
dynamically linked LGPL decoder libraries without x265. Review the [dependency notices](../third_party/README.md) before
publishing binaries or images.

### Application version and update check

Published images contain their release tag in `GROWBOOK_VERSION`, which the footer
displays. Local builds default to **Development** and skip update checks. When building
an unmodified release checkout locally, stamp its actual tag explicitly:

```sh
docker build --build-arg GROWBOOK_VERSION=X.Y.Z --tag growbook:X.Y.Z .
```

Local development runs detect the checkout's Git version at startup when
`GROWBOOK_VERSION` is unset. A clean release checkout shows its tag; tracked local changes
append `-dirty` to the exact identifier (for example, `0.1.8-dirty`), and commits after a
tag include their distance and commit hash. The footer shows the base version and
retains the exact identifier in the version tooltip.
These development versions skip update checks. Without Git metadata,
the label remains **Development**. Restart the server after changing the checkout to
refresh its version. The static package version is not a deployed release version.

On signed-in page loads, JavaScript requests release status from the application.
The server contacts GitHub's public latest-release API without credentials or garden
data and caches valid metadata for six hours per application process. Network errors,
rate limits and invalid responses are cached for one hour; the footer keeps its links
and leaves the indicator hidden while status is unknown. Checks use a three-second
network timeout, happen separately from page rendering, and compare stable numeric
versions. Development builds skip them. Set `GROWBOOK_UPDATE_CHECK=false` to disable
outbound checks; version and GitHub links still appear. Updates are installed manually
using the [upgrade procedure](#upgrade-an-existing-installation).

### Configure the private installation

Copy `.env.example` to an untracked `.env` (`cp .env.example .env`, or
`Copy-Item .env.example .env` in PowerShell). Replace every placeholder; the example is
deliberately not runnable production configuration.

Generate a secret, for example:

```sh
docker run --rm growbook:local python -c "import secrets; print(secrets.token_urlsafe(64))"
```

| Setting | Meaning |
|---|---|
| `GROWBOOK_SECRET_KEY` | Random secret, at least 50 characters; keep private and preserve for recovery. |
| `GROWBOOK_ALLOWED_HOSTS` | Comma-separated exact hostnames, without schemes; no wildcard. |
| `GROWBOOK_CSRF_TRUSTED_ORIGINS` | Comma-separated trusted HTTPS origins. |
| `GROWBOOK_TIME_ZONE` | Garden timezone; defaults to `Europe/Stockholm`. |
| `GROWBOOK_UPDATE_CHECK` | Check public stable release metadata; defaults to `true`. Set `false` to disable outbound update checks. |
| `GROWBOOK_MIGRATE_ON_START` | Apply pending migrations at startup; defaults to `true`. Set `false` for a separate migration step. |
| `GROWBOOK_TRUST_PROXY` | Set `true` only for a trusted TLS-terminating proxy that strips or replaces forwarding headers. |
| `GROWBOOK_TRUSTED_PROXY_IP` | Exact proxy IP when trust is enabled; wildcard trust is rejected. |
| `APP_UID`, `APP_GID` | Non-root numeric process IDs. |
| `APP_DATA_PATH` | Absolute private directory on the application host's local filesystem. |
| `PROXY_NETWORK` | Existing private Docker network used by the managed reverse proxy. |

Create the data directory and give the configured UID/GID the necessary access **before**
starting containers. The image does not implement `PUID`/`PGID`. Compose mounts that
directory at `/data`, sets production settings, uses a read-only root filesystem and
temporary scratch storage, and limits log retention.

Configure the existing reverse proxy to reach the application on container port `8000`
over the private network. When the proxy terminates HTTPS and forwards HTTP, set proxy
trust and its exact IP so the application recognizes the external HTTPS scheme. Do not
relax secure cookies or HTTPS redirects to work around proxy errors.

There are **no published host ports or bundled proxy** in `compose.yaml`. Keep access
private through LAN/VPN and HTTPS; do not add public DNS exposure or router port
forwarding.

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

The explicit `migrate` initializes the schema before `createsuperuser`. Later starts apply
pending migrations automatically when `GROWBOOK_MIGRATE_ON_START` is enabled.

Production settings deliberately leave HSTS include-subdomains and preload disabled;
`check --deploy` reports W005/W021 because this private installation does not claim control
of sibling domains or enroll in public preload. Other security warnings need
investigation.

Before recording real data, verify login/logout, text/photo capture and denial of anonymous
access through the configured HTTPS hostname on the intended devices. Create a backup,
keep an independent copy and verify a clean-instance restore. Repeat these checks after
changes to routing, storage or media handling.

### Upgrade an existing installation

The server applies pending migrations to the mounted database at startup. That is the
default for this single-container image, so an image update does not require a separate
`migrate` command. Create and verify a backup before changing the schema, and keep the
application stopped while the backup runs.

```sh
docker compose build growbook
docker compose stop growbook
# Create and verify a backup with docs/backup-and-recovery.md before continuing.
docker compose up -d growbook
docker compose logs --tail=100 growbook
docker compose run --rm growbook python manage.py migrate --check
docker compose run --rm growbook python manage.py showmigrations core diary garden
```

Use `docker compose pull growbook` instead of `build` when the service uses the published
image. Startup migration is serialized with a lock in `GROWBOOK_DATA_DIR`. If it fails,
the server does not start; inspect the logs before retrying. `showmigrations` must show
`[X]` for every migration, and `migrate --check` must succeed.

To run migrations separately (for example, with several replicas or an external migration
job), set `GROWBOOK_MIGRATE_ON_START=false`. Then stop the application, run and verify the
migration step, and start the new image:

```sh
docker compose stop growbook
docker compose run --rm growbook python manage.py migrate --noinput
docker compose run --rm growbook python manage.py migrate --check
docker compose up -d growbook
```

With startup migration disabled, the server refuses to start while migrations are pending.
Do not start or restart the application while a backup is running; startup migration is a
schema change.

If a page returns a 500 after an upgrade and the log reports a missing column or table, the
image and the mounted database disagree. Confirm both commands use the intended data
directory, apply the migrations and restart. Do not delete the database, use `--fake` or
edit the schema by hand.

To publish a release, see [Release images](development.md#release-images).
