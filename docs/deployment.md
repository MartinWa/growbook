# Deploying growbook

Canonical guide for container builds, private installation configuration and upgrades.
The [main README](../README.md#quick-start) has the short version; keep deployment
procedures here.

## SQLite runtime

Development, tests and production run in Linux containers with checksum-pinned SQLite
3.51.3 built from source. Application checks reject runtimes without the WAL-reset fix;
recognized fixed backports are 3.50.7 and 3.44.6 within their release series. Installing
a newer Python package alone does not establish SQLite safety. Native Windows execution
is unsupported; see [Container development](development.md#container-development).

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

The multi-stage Dockerfile pins image inputs by digest, verifies native source
checksums, installs hash-pinned Python dependencies exported from `uv.lock`, compiles
translations and collects static assets. It builds SQLite and a decoder-only HEIC library
chain; pip is removed from the final runtime image. Build-time checks initialize WSGI
as the default non-root user to catch unreadable collected assets. No local databases,
photos, `.env` files or virtual environments enter the build context.

See [Dependency updates](development.md#dependency-updates) for lockfile maintenance.

**HEIC packaging:** the images use a decoder-only source build. The GPL-encoder wheel
boundary and release checks are documented in the
[dependency notices](../third_party/README.md#heic-packaging-boundary).

### Application version and update check

[Application version](usage.md#application-version) describes the footer. This section
covers how the version is stamped and the update check configured. Published images
contain their release tag in `GROWBOOK_VERSION`. Local builds default to **Development**
and skip update checks. When building an unmodified release checkout locally, stamp its
actual tag explicitly:

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
| `GROWBOOK_SECRET_KEY_FILE` | Read the secret from a mounted file instead, for Docker or Podman secrets. The environment value wins when both are set. |
| `GROWBOOK_ALLOWED_HOSTS` | Comma-separated exact hostnames, without schemes; no wildcard. |
| `GROWBOOK_CSRF_TRUSTED_ORIGINS` | Comma-separated trusted HTTPS origins. |
| `GROWBOOK_TIME_ZONE` | Garden timezone as an IANA name; defaults to `Europe/Stockholm`. An invalid name stops startup. |
| `GROWBOOK_UPDATE_CHECK` | Check public stable release metadata; defaults to `true`. Set `false` to disable outbound update checks. |
| `GROWBOOK_MIGRATE_ON_START` | Apply pending migrations at startup; defaults to `true`. Set `false` for a separate migration step. |
| `GROWBOOK_TRUST_PROXY` | Set `true` behind a trusted HTTPS reverse proxy that replaces `X-Forwarded-Proto`; defaults to `false`. No fixed proxy IP is needed. |
| `APP_UID`, `APP_GID` | Non-root numeric process IDs. |
| `APP_DATA_PATH` | Absolute private directory on the application host's local filesystem. |
| `PROXY_NETWORK` | Existing private Docker network used by the managed reverse proxy. |
| `GROWBOOK_HOST_PORT` | Loopback port for a proxy running on the host; used only with `compose.host-port.yaml`. |

Create the data directory and give the configured UID/GID the necessary access **before**
starting containers. The image does not implement `PUID`/`PGID`. Compose mounts that
directory at `/data`, sets production settings, uses a read-only root filesystem and
temporary scratch storage, and limits log retention. Startup stops with the directory
path and the ownership fix when it is not writable.

Configure the existing reverse proxy to reach the application on container port `8000`
over the private network. When the proxy terminates HTTPS and forwards HTTP, set
`GROWBOOK_TRUST_PROXY=true` so the application recognizes the external HTTPS scheme.
The proxy must discard any visitor-supplied `X-Forwarded-Proto` value and set it to the
original connection's scheme (`https` for HTTPS). Only this forwarding header is trusted;
client address, host and port forwarding headers are removed.

Proxy trust applies to every peer that can reach the application port. Keep the port
private and limit access to the trusted proxy and trusted network peers; use a dedicated
proxy-to-application network when other containers are untrusted. With proxy trust
disabled, forwarding headers are removed and cannot mark an HTTP request as HTTPS.
Secure cookies and HTTPS redirects remain enabled in either mode.

There are **no published host ports or bundled proxy** in `compose.yaml`. Keep access
private through LAN/VPN and HTTPS; do not add public DNS exposure or router port
forwarding. Two patterns cover a proxy in a container and a proxy on the host itself;
both need `GROWBOOK_TRUST_PROXY=true` and nothing else is proxy-specific.

#### Proxy in a container (default)

Compose attaches the application to the existing private proxy network named by
`PROXY_NETWORK`, so the proxy container forwards to the `growbook` service on port
`8000`:

```nginx
location / {
    proxy_pass http://growbook:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
}
```

#### Proxy on the host

For a proxy installed on the host, such as a NAS proxy package or a native nginx or
Caddy, publish the port on loopback with the optional override:

```sh
APP_UID=1000 APP_GID=1000 APP_DATA_PATH=/srv/growbook-data PROXY_NETWORK=proxy \
  docker compose -f compose.yaml -f compose.host-port.yaml up -d
```

The override binds `127.0.0.1:${GROWBOOK_HOST_PORT:-8000}` and never `0.0.0.0`. Point the
proxy at `http://127.0.0.1:8000` and set `GROWBOOK_TRUST_PROXY=true` as for the container
pattern. A Caddy site block for the container pattern is:

```caddyfile
garden.example.internal {
    reverse_proxy growbook:8000
}
```

A host-run Caddy uses `reverse_proxy 127.0.0.1:8000` instead; nginx and Traefik need
`X-Forwarded-Proto` set explicitly, while Caddy forwards it on its own.

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

The crop-category migration removes the former free-text **Category** field on varieties
and discards its values without creating crop categories. Preserve any values you need
before upgrading, and keep the verified pre-upgrade backup.

Migrated cycles get an initial status that can be corrected through **Edit cycle**. The
migration files under each app's `migrations/` directory describe specific schema changes.

```sh
docker compose build growbook
docker compose stop growbook
# Create and verify a backup with docs/backup-and-recovery.md before continuing.
docker compose up -d growbook
docker compose logs --tail=100 growbook
docker compose run --rm growbook python manage.py migrate --check
docker compose run --rm growbook python manage.py showmigrations core diary garden economy
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
