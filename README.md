# growbook

A private, self-hosted garden diary, planting planner and registers built with
Python and Django. Capture observations with text, photos, or both; plan annual
and persistent plantings; and maintain crops, varieties and growing areas in your
web browser.
The interface defaults to Swedish, with English available.

## Current features

- Administrator-managed accounts, login/logout and a shared garden; no public signup.
- Crop, variety and optionally nested growing-area registers, with compact tables,
  independently expandable details, editing and non-cascading archive.
- Unicode-aware scoped name uniqueness, cycle-safe area organization and confirmed
  deletion of records without protected references.
- Multiple private photos on varieties/areas, with editable captions/order and
  confirmed photo deletion.
- Early planting plans with required crop/start year, optional variety/area and
  independent batches; quantity distinguishes unknown from zero.
- Annual and persistent plantings, inclusive end years, year overviews and
  crop/variety/descendant-area histories, including archived associations.
- Repeated planting events with independent planned/actual dates across calendar
  years, simple cumulative evaluation text and private planting photos.
- Dated text-only, photo-only and text-plus-photo entries, with chronological paging,
  note search and combined year, area, crop, variety and planting filters.
- Explicit links from an entry to growing areas, crops, varieties or plantings.
  Linked records show their observations, and derived filters follow plantings and
  area descendants.
- Authors edit and delete their own entries; active staff administrators can edit and
  delete any entry. Photo captions, ordering, addition and removal are editable too.
- Inline diary quick-add and a dedicated capture page, with native photo selection,
  multiple photos, previews and upload feedback; native inputs remain without JavaScript.
- JPEG, PNG, WebP and HEIC validation, orientation correction and metadata-stripped
  WebP display images/thumbnails. Received originals are retained privately.
- Authenticated photo viewing in a CSP-safe HTML page and private original downloads;
  no public media directory.
- Retry-safe diary creation and register/planting/event saves, with visible errors
  and no partial photo saves.
- Operational database/photo backup, verification, clean-instance restore and orphan cleanup.

## Scope and limitations

- The garden is shared within an installation: every active account can read all
  entries, records, photos and original downloads, and maintain registers, plantings
  and events. Diary writes are narrower: the author or an active staff administrator
  may edit or delete an entry. There are no per-user gardens or read-only member roles.
- Diary links are chosen in the edit form. There is no capture-time link shortcut or
  automatic matching, and structured harvest accounting and portable data
  import/export are not provided.
- Entry deletion is permanent and removes the note, links and photos, including
  private originals; linked registers and plantings are kept. Creation receipts
  remain only to reject stale capture retries.
- Diary search matches the note text as a literal, Unicode casefolded substring.
  Diacritics remain significant (`Å` is not `A`, `å` is not `a`), and captions,
  author names and linked record names are not searched.
- Filters combine with AND. A planting match also matches its crop, variety and
  area; an area filter includes descendant areas. Archived records remain visible
  through existing links and filters, but cannot be chosen for a new link.
- Planting histories use current area assignments and nesting, not immutable past
  layouts or movement history. Evaluation is cumulative, not a per-year assessment.
- Uploads are limited to 10 photos per diary entry, variety, area or planting,
  20 MiB per photo, 80 MiB per upload and 50 million decoded pixels per photo.
  Apple HDR JPEGs with one auxiliary grayscale gain map use the primary photo
  for display; both images count toward the pixel limit. Other multi-image files
  and animated images are rejected.
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
The [interface foundation guide](docs/design.md) describes the shared styles,
tokens and capture form conventions.

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
accounts. There are no seeded credentials. Diary/photo/register/planting admin
screens are read-only; use the diary pages for observations, **Garden** for
garden records and **Plantings** for plans and milestones.

`manage.py` defaults to development settings with loopback-only allowed hosts,
HTTP cookies, a generated local secret and data in ignored `local/development/`.
Do not expose the development server or point it at production data. A local
`.env` file is **not automatically loaded**; local configuration overrides must
be set in the process environment.

### Garden

Open **Garden** for crop, variety and growing-area cards with active record
counts and creation links. Each register table has a labelled **Details** control:
several rows can stay expanded
at once, and ordinary record links work without JavaScript. On phones, secondary
columns are available in expanded details.

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
  Open **Archive options** and use **Include archived records** to find it.
  The checkbox applies on change with JavaScript; **Apply** is the fallback.
  **Hide archived records** returns to the default list.
- Edit an area or variety to add photos; edit photo details to change captions or
  ordering. Deletion requires confirmation. A crop with varieties or an area with
  children cannot be deleted; archive it or deliberately reorganize its dependents.
  Allowed owner deletion removes its photos, with coordinated file cleanup after
  the database commit. Referenced crops, varieties and areas cannot be deleted.
- A variety used by any planting cannot change crop through an ordinary edit,
  including when the planting has ended. Reconcile its planting references
  explicitly first; changing the variety does not silently reclassify plantings.

Register, planting and event forms reject stale edits instead of overwriting
another member's changes. On a failed or uncertain create/edit save, keep the form
open and retry the same content and save key; photos are not appended twice. With
JavaScript, validation updates feedback beside the fields while preserving fields
and selected files. Without it,
reselect photos as instructed before retrying. A genuinely changed form is a new
submission; do not reuse a confirmed key for different content.

Register and planting retries resolve matching immutable receipts before checking
current association eligibility. Identical retries return the saved record even
if the original crop/parent was archived or the record's associations changed
later. New submissions still validate current associations and edit versions.

Observation, variety, area and planting upload forms share **Add photos**, which
opens the native image picker directly with JavaScript. Camera/library options
and their labels depend on the browser and operating system. Selected-file previews
and removal controls stay in the form. With JavaScript,
successive choices accumulate where the browser supports assigning FileLists.
Otherwise, a new choice replaces that source and **Clear selected photos** empties
the selection.
Without JavaScript, both native inputs remain visible; choose all library photos at once.
Selected-photo counts show the number chosen out of the available capacity.
**Photo limits** opens the formats and byte limits. A count warning includes existing
photos and, when editing an observation, subtracts photos marked for removal; the
server enforces the limit. On phone-sized screens with sufficient height, full forms
keep Save and Cancel visible at the bottom while scrolling.

### Plantings and histories

Open **Plantings** for a growing-year table; filters use a sidebar at 900 px and
a drawer below that width. Changes apply automatically; Enter submits a typed year.
Without JavaScript, use **Apply**. Enter a year or use **Previous year**
and **Next year**. **All years** shows one row per planting across its full history.
Crop, variety and area histories default to all years; table links keep an
explicitly selected year.
Use **Details** to expand multiple rows independently and see events, notes,
evaluation and photos. Register details link to their **Planting history**.

- **New planting** requires only a crop and start year (1–9999). Variety and final
  growing area may be left unknown; no placeholder register records are created.
  **More details** groups variety, area, lifespan, end year, batch label and quantity;
  **Notes and photos** groups notes, evaluation and uploads. Populated fields and
  fields with validation errors keep their group open.
  Variety choices follow the crop without reloading and clear an incompatible
  selection. Without JavaScript, use **Update variety choices** before choosing a
  variety. The server also rejects mismatches and ineligible new associations.
- **Batch label** distinguishes similar rows. Several batches may have identical
  year/crop/variety/area values. Quantity is a nonnegative plant count: blank means
  unknown and zero is a known zero, not an omitted value.
- **Annual** is the default and belongs only to the start year, with no end year.
  With JavaScript, a blank end year is hidden for annual plantings; an entered end
  year is preserved for correction. **Persistent** belongs to every year from start
  through its optional end year,
  inclusive; a blank end year means ongoing. Set the end year to end a planting
  without deleting its events, evaluation or photos. Viewing another year never
  creates a copy or counts the original quantity as repeated production.
- Filter by crop, variety or area, or find rows with **Unassigned area only** or
  **Unknown variety only**. Area filters include the selected area's own plantings
  and all descendants, once per batch, showing the actual assigned place. Archived
  register items stay visible in existing histories and associations.
- **New planting event** supports sowing, transplanting, harvest start/end and
  other milestones. Planned and actual dates are separate; at least one is needed.
  Dates do not determine the growing year and may precede or follow it. Repeated
  event types are valid; use separate planting rows for independently counted or
  evaluated batches. Event edits and deletion use version checks and confirmation
  for deletion.
- Edit a planting to add cumulative evaluation text or photos. Photo captions,
  order, private viewing/downloads and confirmed deletion use the same controls as
  register photos. Planting deletion is permanent and removes its events/photos;
  ending a persistent planting is the history-preserving alternative.

Each planting has one current area. Correcting that assignment or reparenting an
area changes past-year results too. For an established plant that moves, prefer
ending the old planting and creating a new one at the destination. This preserves
year-level presence, not exact movement dates, lineage or historical layouts.

### Diary

Open **Diary** to add an observation directly or browse observations newest first.
Quick-add accepts a note, photos or both; **More options** opens the dedicated capture
page and does not carry the quick draft over. With JavaScript, a confirmation warns
before leaving a non-empty quick draft behind; a confirmed save clears quick-add,
shows a receipt and refreshes the first results page with the current filters.
Failed saves keep the draft and selected files in the open form; without JavaScript,
reselect photos as instructed before retrying with the same save key.

Search and filters use a sidebar on wide screens and a drawer on phones. With
JavaScript, search applies after a short pause or Enter, other filters apply on
change, and chips remove individual filters. Paging and Back preserve filter URLs;
updating results keeps an unfinished quick-add and its selected files. Without
JavaScript, open the filter disclosure and submit the form. The search field matches
the note text only; the year, area, crop, variety and planting controls combine in one
form. Clear all filters to return to every year. A planting filter also matches its
crop, variety and area; an area filter includes descendant areas. Results are
deduplicated, and paging preserves the active filters.

Open a photo to use **Photo options → Download original**. Original downloads are
kept out of the ordinary diary view; received originals remain private and are
included in operational backups.

The account menu contains Language, Log out and, for staff, Operator. Language
changes apply immediately with JavaScript and retain the current page and filters;
a submit button remains without JavaScript. The login page has its own selector.

- Search is a literal substring match after Unicode NFC normalization and casefolding,
  so uppercase and lowercase match but diacritics do not: `Ångström` finds
  `ångström`, while `Angstrom` does not. Photo captions and linked record names are
  not searched.
- Open an observation to read its note, links and photos. The author may edit or
  delete it; an active staff administrator may edit or delete any entry. Other active
  accounts can read it. The diary list shows **Edit and tag** on entries the signed-in
  account may change. A deactivated author cannot write, even while already signed in.
- Edit changes the date, note, garden links and photos. A planting link also matches
  its crop, variety and area. Each link points to exactly one area, crop, variety or
  planting, and the same record can be linked only once. New links must target active
  records; existing links to archived records remain selected and filterable. An
  entry with no links shows **Add garden links** on its page, and the edit form opens
  the link picker by default.
- Edits use the entry's version token. A stale form is rejected instead of
  overwriting a newer change; reload the observation to see the saved values. With
  JavaScript, only errors are replaced and the form stays open. Without it, the date,
  note and link selections are kept, but reselect photos before retrying.
- Delete requires confirmation and permanently removes the note, links and photos,
  including private originals. Linked registers, plantings and their photos are kept.
  A stale deletion is rejected and asks you to reload.
- On an entry's photo page, the author or an administrator can change the caption and
  order, or delete the photo with confirmation. A photo-only observation must keep at
  least one photo; add a note before removing its last one.
- Record details and histories show the latest linked observations with a count and a
  link to the filtered diary.

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
images, covering capture, observation editing and deletion, permissions, links and
derived filters, Unicode search, image failures, authorization, retries,
concurrency and clean-instance recovery. Browser tests are excluded from the default run.

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

[`.github/workflows/dependency-review.yml`](.github/workflows/dependency-review.yml)
runs on every pull request and fails when the change introduces a dependency with a
known high or critical vulnerability. It is a required status check on `main`.

[`.github/workflows/zizmor.yml`](.github/workflows/zizmor.yml) audits the workflow
files for Actions-specific security problems on pull requests, pushes to `main`,
manual dispatch and weekly. [`.github/workflows/scorecard.yml`](.github/workflows/scorecard.yml)
publishes an OpenSSF Scorecard and its SARIF results to code scanning on `main` and
weekly. These reports are advisory; they do not replace the build, test, audit and
image checks above.

### Dependency updates

[`.github/dependabot.yml`](.github/dependabot.yml) requests weekly updates for uv,
GitHub Actions and the Docker base image. Version updates use a seven-day cooldown so
a release can age before review; Dependabot security updates are not delayed. Patch
updates may be auto-merged by
[`.github/workflows/dependabot-auto-merge.yml`](.github/workflows/dependabot-auto-merge.yml)
after the required checks pass; minor and major updates still require review. Django
stays on 5.2 LTS and Python stays on the current minor series. Dependabot resolves
with its bundled Python 3.14.9; CI and containers use the pinned 3.14.10 runtime.
After a uv update, regenerate the Docker requirements on the PR branch before
merging:

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
git fetch origin --tags
git tag -a X.Y.Z -m "Release X.Y.Z"
git push origin X.Y.Z
```

Replace `X.Y.Z` with a new release version. From PowerShell, after the same
`main` checkout update, tag fetch and successful CI, use
[`tools/tag_release.ps1`](tools/tag_release.ps1):

```powershell
.\tools\tag_release.ps1 -WhatIf
.\tools\tag_release.ps1 -Push
```

The script requires a clean `main` checkout and increments the highest local
stable tag's patch number by default (`0.1.9` becomes `0.1.10`), including legacy
`v`-prefixed tags when comparing history. Use `-Version X.Y.Z` to choose a
version explicitly or create the first release. Without `-Push`, it only creates
the annotated tag locally; `-WhatIf` previews the operation. If a push fails, the
local tag remains and the error gives the command to retry that push.

No manual GitHub Release creation is needed; watch **Actions → Release image**
for completion. A manually published stable GitHub Release remains a supported
trigger. Tag deletion is ignored, and moving an existing tag is rejected; correct
failures by rerunning the failed workflow, not by force-pushing tags.

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
| `diary/` | Diary models, forms, views, filtering and validated create/edit/delete operations. |
| `garden/` | Registers, plantings/events, year/history queries, forms and validated writes. |
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
