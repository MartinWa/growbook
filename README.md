# growbook

[![CI](https://github.com/MartinWa/growbook/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/MartinWa/growbook/actions/workflows/ci.yml)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/MartinWa/growbook/badge)](https://securityscorecards.dev/viewer/?uri=github.com/MartinWa/growbook)
[![Latest release](https://img.shields.io/github/v/release/MartinWa/growbook?display_name=tag&sort=semver)](https://github.com/MartinWa/growbook/releases/latest)
[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue)](LICENSE)
[![Python 3.14.10](https://img.shields.io/badge/python-3.14.10-3776AB?logo=python&logoColor=white)](.python-version)
[![Django 5.2 LTS](https://img.shields.io/badge/django-5.2%20LTS-092E20?logo=django&logoColor=white)](https://docs.djangoproject.com/en/5.2/)

A private, self-hosted garden diary, planting planner and registers built with Python
and Django. Record observations with text, photos or both, plan annual and persistent
plantings, and maintain crops, varieties and growing areas in the browser. The
interface defaults to Swedish, with English available.

## Current features

- Administrator-managed accounts and a shared garden; no public signup.
- Four seasonal colour schemes with matching diary illustrations, selected per browser
  or changed automatically with the garden's local date.
- Discreet footer links to GitHub and releases, the deployed release version, and an
  orange indicator when a newer stable release is available.
- A combined crop and variety register with searchable crop groups, and a growing-area
  card overview with child-area detail pages, editing and non-cascading archive.
- A permanent crop-category overview with an uncategorized view and shared,
  user-defined categories; crops can belong to multiple categories. An optional
  private category image, including transparency, is reused on its card and heading.
- Unicode-aware scoped name uniqueness, cycle-safe area organization and confirmed
  deletion of records without protected references.
- Multiple private photos on varieties, areas and plantings, with editable captions and
  order, confirmed deletion, protected viewing and private original downloads.
- Early planting plans with crop and start year, optional variety or area, independent
  batches, and quantity that distinguishes unknown from zero.
- Annual and persistent plantings grouped by area, inclusive end years, year overviews, and
  crop/variety/descendant-area histories that include archived associations.
- Repeated planting events with independent planned and actual dates, plus cumulative
  evaluation text and private photos.
- Dated text-only, photo-only and text-plus-photo entries with optional titles,
  chronological paging, title and note search, and combined year, area, crop, variety
  and planting filters.
- Optional tags during capture and editing, with one searchable picker for plantings,
  growing areas, crops and varieties; linked records
  show their observations, and derived filters follow plantings and area descendants.
- Authors edit and delete their own entries; active staff administrators can change any
  entry. Photo captions, ordering, addition and removal are editable.
- Inline diary capture with native photo selection,
  multiple photos, previews and upload feedback; native inputs also work without
  JavaScript.
- JPEG, PNG, WebP and HEIC validation, orientation correction and metadata-stripped WebP
  display images and thumbnails. Originals are retained privately and served only through
  authenticated downloads; there is no public media directory.
- Retry-safe diary, register, planting and event saves with visible errors and no partial
  photo saves.
- Operational database/photo backup, verification, clean-instance restore and orphan
  cleanup.

## Scope and limitations

- The garden is shared within an installation: every active account can read all
  entries, records, photos and original downloads, and maintain registers, plantings and
  events. Only an entry's author or an active staff administrator may edit or delete
  that entry. There are no per-user gardens or read-only member roles.
- Diary tags are chosen explicitly; there is no automatic matching.
  Structured harvest accounting and portable data import/export are
  not provided.
- Entry deletion permanently removes the title, note, links and photos, including
  private originals; linked registers and plantings remain. Creation and edit receipts
  are kept to reject retries against deleted entries.
- Diary search is a literal, Unicode-casefolded substring of titles and notes.
  Diacritics remain significant (`Å` is not `A`, `å` is not `a`), and captions,
  author names and linked record names are not searched.
- Filters combine with AND. A planting match also matches its crop, variety and area; an
  area filter includes descendant areas. Archived records stay visible through existing
  links and filters but cannot be chosen for a new link.
- Planting histories use current area assignments and nesting, not immutable past
  layouts or movement history. Evaluation is cumulative, not per-year.
- Uploads are limited to 10 photos per diary entry, variety, area or planting, 20 MiB per
  photo, 80 MiB per upload and 50 million decoded pixels per photo. Apple HDR JPEGs with
  one auxiliary grayscale gain map use the primary photo; both images count toward the
  pixel limit. Other multi-image and animated files are rejected.
  A crop category has at most one image, with the same file and decoded-pixel limits.
- Deployments are intended for private LAN/VPN access over HTTPS, not public Internet
  exposure. Verify backup and clean-instance recovery before recording real data.

## Requirements

- Docker Desktop with Linux containers and Compose v2 or newer for development.
  Keep the checkout on Windows and edit in VS Code; application execution and tests
  run in Linux containers. Native Windows application execution is not supported.
- The images pin Python 3.14.10 and use `uv.lock` dependencies including Django 5.2 LTS.
  `requires-python` allows 3.14.9 for dependency tooling only.
- A local filesystem for SQLite and photos, with the fixed SQLite runtime described in
  the [deployment guide](docs/deployment.md#sqlite-runtime). SMB/NFS-mounted databases
  are not supported.
- Docker with Linux containers and Compose for
  [production deployment](docs/deployment.md#build-and-run-with-docker).

Run commands from the repository root. The compiled CSS, HTMX and JavaScript are
self-hosted. Rebuilding the committed stylesheet needs Node.js and pnpm only during
development; see [Styles](docs/development.md#styles). The
[interface foundation guide](docs/design.md) describes the shared theme and form
conventions.

## Run locally

Follow [Container development](docs/development.md#container-development) for setup,
startup, automatic reload, VS Code debugging and persistent development storage.

Open **http://127.0.0.1:8000/** and use **`/operator/`** to manage other accounts. There
are no seeded credentials. Diary, photo, register and planting admin screens are
read-only; use the diary pages for observations, **Growing areas** and
**Crops and varieties** for records, and **Plantings** for plans and milestones.

The separate development Compose configuration publishes HTTP only on host loopback.
Its database, photos, generated secret, Linux virtualenv and test scratch files live in
Docker volumes. Do not expose the development server or point it at production data.

## Using growbook

- **Growing areas** and **Crops and varieties** open directly from the main menu.
  Browse top-level areas and their child cards, choose a crop category, expand crop
  groups, read information and photos, search crop and variety names, and maintain records.
- **Plantings** shows growing-year results grouped by area, with filters and expandable
  batches. Create annual or persistent plantings, record events, and open crop, variety and area
  histories.
- **Diary** holds observations. Quick-add a note, photos or both; search and filter by
  year, area, crop, variety or planting; open linked records from an entry.
- **Photos** use the native image picker where available, with previews, captions,
  ordering and private original downloads.

The [usage guide](docs/usage.md) covers each screen, save retries and no-JavaScript
behavior.

## Test and check

The [development guide](docs/development.md#test-and-check) lists the test, lint,
migration and deployment-check commands. Run `tools/run_tests.py` inside the development
container: it compiles translations and keeps artifacts on Linux storage. Tests use real
file-backed SQLite/WAL and synthetic images; browser tests are excluded. The guide also
covers browser tests, styles, translations, CI checks and dependency updates.

## Run with Docker

The Dockerfile builds a Linux production image with Waitress. The supplied Compose file
runs one application process with two threads behind an existing private HTTPS reverse
proxy. The [deployment guide](docs/deployment.md) covers building, configuration,
initialization and upgrades.

## Backup and recovery

Use the [operational recovery guide](docs/backup-and-recovery.md) for backup,
verification, clean-instance restore and orphan cleanup. Never copy a live SQLite
database file as a backup. Protect backups as sensitive data, keep an independent copy,
and verify a restore into a new, nonexistent destination before real use. This is
operational recovery, not portable import/export.

## Documentation

| Guide | Contents |
|---|---|
| [Using growbook](docs/usage.md) | Registers, plantings, diary, photos and retries |
| [Development, CI and releases](docs/development.md) | Tests, browser suite, styles, translations, workflows, dependency updates and release images |
| [Deployment](docs/deployment.md) | Docker, private configuration, upgrades and SQLite runtime |
| [Backup and recovery](docs/backup-and-recovery.md) | Backup, verification, restore and orphan cleanup |
| [Interface foundations](docs/design.md) | Shared styles, tokens and capture form conventions |
| [Third-party notices](third_party/README.md) | Dependency licensing and native build provenance |

## Project layout

| Path | Purpose |
|---|---|
| `growbook/` | Project settings, routing, SQLite safety checks and WSGI server. |
| `core/` | Users, shared form and photo-storage helpers, security, SQLite configuration and operational commands. |
| `diary/` | Diary models, forms, views, filtering and validated create/edit/delete operations. |
| `garden/` | Models, forms and year/history queries; `views/` and `services/` separate registers, photos, plantings and events. |
| `templates/`, `static/` | Server-rendered HTML and self-hosted CSS/JavaScript. |
| `assets/` | Stylesheet source compiled into the committed application CSS. |
| `locale/` | Maintained translation catalogs; compiled `.mo` files are generated. |
| `tests/` | Domain, integration and browser tests with synthetic fixtures. |
| `tools/` | Container startup/checks, translation compilation and the test entry point. |
| `docs/` | Usage, development, deployment, design and recovery guides. |
| `third_party/` | Dependency licenses, notices and native build provenance. |

`.venv/`, `local/`, `staticfiles/`, `.env`, databases and live media/backups are ignored
by Git. Private material and local tool/browser state are excluded from Git and container
build contexts. Migrations, dependency locks, source translations and synthetic fixtures
belong in source control.

## Security

Report vulnerabilities through the private channel in [SECURITY.md](SECURITY.md). Use
synthetic examples in public issues and pull requests; never attach real photos,
backups, credentials or private configuration.

## License

Growbook is **source-available under the
[PolyForm Noncommercial License 1.0.0](LICENSE)**, not OSI open-source software. The
license text governs permitted use, modification and distribution. Separately licensed
dependencies retain their own terms; the project license does not claim ownership of
user-entered garden records or photos.

For engineering conventions and expectations for contributors and coding assistants,
see [AGENTS.md](AGENTS.md).