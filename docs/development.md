# Development, CI and releases

Developer guide: local checks, browser tests, styles, interface foundations, workflow
audits, dependency updates and release publishing. Deployment procedures are in the
[deployment guide](deployment.md); user-facing behavior is documented in
[Getting started](getting-started.md) and the feature guides.

## Container development

Install Docker Desktop on an x64 host, select Linux containers, and keep this checkout on
Windows. Use PowerShell from the repository root; neither a WSL checkout nor a WSL terminal
is required. Application execution and tests are supported in the Linux images, sharing
production's pinned Python, SQLite and decoder-only image libraries.

```powershell
docker version
docker compose -f compose.development.yaml build app
docker compose -f compose.development.yaml up -d app
docker compose -f compose.development.yaml logs --follow --tail=50 app
```

Wait until the logs show **Starting development server at** before creating the first
account; detached startup returns before migrations finish. Press Ctrl+C to stop following
the logs (the container keeps running), then run:

```powershell
docker compose -f compose.development.yaml exec app python manage.py createsuperuser
```

Open **http://127.0.0.1:8000/**. Startup compiles translations, applies migrations and
starts Django's development server. There are no seeded credentials. The service binds port
8000 only to host loopback. `compose.development.yaml` is independent of the production
`compose.yaml`; it needs no production `.env`, proxy or private network.

The Windows checkout is mounted at `/app`. Save Python edits in Windows VS Code to trigger
Django's polling reloader; template and static edits also use the mounted source
immediately. Ordinary edits require no image rebuild. For translation edits, run
`python tools/compile_translations.py` in the container and restart the server.

The named `development-state` volume stores `/state/development` (database, photos and
generated secret), `/state/venv`, compiled translations and test/scratch files.
`editor-home` stores the container user's editor state. SQLite and photo files stay on
Docker's Linux filesystem rather than the Windows bind mount. Recreating containers or
running `docker compose -f compose.development.yaml down` preserves these volumes. Do not
use `down --volumes` unless all of this disposable state should be deleted. Never mount
production storage into this service.

The development virtualenv inherits production packages. Its separately exported dev group
installs without dependency resolution, preserving the source-built HEIC wrapper. Startup
checks the active Python, SQLite and codec inventory. After dependency or Dockerfile
changes, stop the service, rebuild and recreate it; the virtualenv refreshes from the image
while development records remain intact. Do not run `uv sync` or install upstream HEIC
wheels in this environment.

PowerShell Core is included only in the development image to exercise host-side release
launcher logic on Linux. These checks do not claim Windows application runtime support.

For a Linux terminal without rebuilding:

```powershell
docker compose -f compose.development.yaml exec app bash
```

Alternatively install VS Code's **Dev Containers** extension and choose **Dev Containers:
Reopen in Container**. It attaches to the same service and Windows source mount, with
`/state/venv/bin/python` as interpreter. Run checks directly with `python` or `ruff` in
that terminal. The supplied Django debug configuration starts a second server on container
loopback port 8001 without the reloader; forward that port to local loopback in VS Code's
Ports panel. The normal server on port 8000 continues to reload on saves.

Test invocations share disposable Linux state; run separate invocations sequentially within
one Compose project. Temporary `compose run --rm` containers share the development volumes
but do not publish the service's HTTP port.

## Project layout

| Path | Purpose |
|---|---|
| `growbook/` | Project settings, routing, SQLite safety checks and WSGI server. |
| `core/` | Users, shared form and photo-storage helpers, security, SQLite configuration and operational commands. |
| `diary/` | Diary models, forms, views, filtering and validated create/edit/delete operations. |
| `garden/` | Models, forms and year/history queries; `views/` and `services/` separate registers, photos, plantings and events. |
| `economy/` | Validated purchases, reserved cycle allocations, plant sales and current cost estimates. |
| `planner/` | Read-only calendar month of planting events and chosen diary observations. |
| `templates/`, `static/` | Server-rendered HTML and self-hosted CSS/JavaScript. |
| `assets/` | Stylesheet source compiled into the committed application CSS. |
| `locale/` | Maintained translation catalogs; compiled `.mo` files are generated. |
| `tests/` | Domain, integration and browser tests with synthetic fixtures. |
| `tools/` | Container startup/checks, translation compilation and the test entry point. |
| `docs/` | User, administration, deployment and development guides. |
| `third_party/` | Dependency licenses, notices and native build provenance. |

`.venv/`, `local/`, `staticfiles/`, `.env`, databases and live media/backups are ignored by
Git. Private material and local tool/browser state are excluded from Git and container
build contexts. Migrations, dependency locks, source translations and synthetic fixtures
belong in source control.

## Test and check

```powershell
docker compose -f compose.development.yaml run --rm app python tools/run_tests.py -q
docker compose -f compose.development.yaml run --rm app ruff check .
docker compose -f compose.development.yaml run --rm app ruff format --check .
docker compose -f compose.development.yaml run --rm app python tools/check_no_private_data.py
docker compose -f compose.development.yaml run --rm app python manage.py check --database default
docker compose -f compose.development.yaml run --rm app python manage.py makemigrations --check --dry-run
```

`tools/check_no_private_data.py` fails when a tracked file name or a tracked file's text
looks like private data: a `planning/` or `reference/` directory, dumps, databases, exports,
archives and `.pdf` files by name, and private keys, classic and fine-grained GitHub tokens,
assigned secrets, email addresses, absolute host paths and image metadata by content. Tracked
content is read from the git index, so the check inspects what a commit would contain instead
of the working tree, and a symbolic link is reported instead of followed. The content scan
skips only the two vendored files that `tests/test_assets.py` hashes and the generated
`*.LICENSE.txt` notice bundles. Text and PNG/JPEG metadata are read as a stream, so a large
file is never loaded whole; WebP and HEIC metadata is not inspected, which is the format of
the illustrations and image fixtures shipped here. A line that has to look like private data
can carry `hygiene: allow` in its comment, and no tracked line needs that today. The check
needs the repository's git work tree and stops with a message outside one. Add `--untracked`
to also scan the files git reports as untracked and not ignored, which catches a new private
file before it is added to the index:

```powershell
docker compose -f compose.development.yaml run --rm app python tools/check_no_private_data.py --untracked
```

The two Ruff invocations may also run on the Windows editing host when the same pinned
version is installed there (for example with `uv tool install ruff==0.17.4`); Ruff is
self-contained and produces identical results. Container and CI runs stay authoritative
whenever versions differ, and tests and application checks never leave the container.

Use `tools/run_tests.py` inside the container: it compiles translations and keeps temporary
artifacts in the Linux state volume. Tests use real file-backed SQLite/WAL and synthetic
images, covering text-only, photo-only and multi-photo entries, photo failures and retries,
upload limits, capture, observation editing and deletion, permissions, links and derived
filters, Unicode search, authorization for pages, media and downloads, economy spending and
growing-year summaries, exact allocation rounding, pending groups, equipment schedules,
plant sales, overlapping writes, clean-instance restore, API token storage and revocation,
and the JSON API with its authentication failures and keep-in-sync OpenAPI
document. Browser tests are excluded from the default run.

For UI-copy changes, update the English source messages and
`locale/sv/LC_MESSAGES/django.po`, then recompile with `tools/compile_translations.py`.
Compiled `.mo` files are generated; tests and Docker compile them automatically.

## Validated garden writes

Call `garden.services` operations with an active actor and outside an existing database
transaction. Every public edit and delete takes a timezone-aware `datetime`
`expected_updated_at` token read from the target record, event, photo owner, cycle or
category; omitting it or passing a naive/invalid value is an error. Member changes advance
the cycle version, and category deletion advances linked cycles. Read saved state again
before a dependent edit rather than reusing a stale token.

Archive operations (`archive_crop`, `archive_variety` and `archive_area`) take the explicit
desired `archived` state without an `expected_updated_at` token. The register create and
edit operations instead take an optional `archived` value, which is validated, versioned and
retried with the rest of the write rather than applied afterwards.

Create/edit saves with submission receipts retry the original payload, version token and
`submission_key`. Writers check the active actor and matching receipt before current
association eligibility and stale-state checks in short serialized writes; a matching retry
returns the saved record, changed content conflicts. Cycle completion and corrections
optionally accept `evaluations` keyed by member ID with the full `expected_member_versions`
snapshot, preserving omitted text.

`diary.services.create_entry` runs one typed `diary.actions` action and separate
`planting_uploads` under the diary actor checks and receipt, so an identical replay does not
rerun the action. Creation without uploads skips media locking and staging; saves with
uploads, edits and deletions coordinate media storage. `update_entry` follows the same retry
contract. Diary edits and deletion never undo an action.

Economy writes use `economy.services` with the same active-actor, outside-transaction and
timezone-aware `expected_updated_at` rules. Allocation create/edit/delete also requires the
expense's `expected_expense_updated_at`; allocation changes advance that expense version.
Matching immutable submission receipts precede current budget and association checks, and
deleted targets retain receipt tombstones. Budget validation and financial-reference checks
run inside short serialized writes. Money is stored as integer cents because SQLite decimal
arithmetic is not exact; percentage/quantity choices become fixed monetary reservations.
Current allocation splits are calculated from a consistent snapshot without modifying or
consuming those reservations.

## Browser tests

The development image includes Playwright's pinned Chromium and Linux browser libraries.
The command below uses pytest-xdist to run four workers, each with its own file-backed
SQLite/WAL database and static directory under `/state/test-state/gw*`. Pytest isolates
temporary files and media per test in the Linux state volume. Each worker reuses one
Chromium process through a loopback-only Playwright browser server, using the driver
bundled with the pinned Python package. Each test has a fresh browser context and Python
connection; Django's async safety checks remain enabled. Contexts close on test failures,
and the servers stop when the suite finishes or is interrupted. Run the full browser suite
separately:

```powershell
docker compose -f compose.development.yaml run --rm app python tools/run_tests.py -q -m browser -n 4 --dist worksteal
```

Use `-n 2` on machines with limited resources, or omit `-n` for serial debugging. File
paths, `-k` filters and `--junitxml` also work with multiple workers. Browser setup and
login helpers are shared; tests still use real login forms and fresh contexts. Image waits
bring lazy images into view and fail after eight seconds.

Add `--durations=20` to either suite to report its slowest setup, call and teardown steps.
CI includes this report in both test jobs.

The suite exercises Chromium and real form interaction. It does not verify a phone's native
camera/library picker or a deployment's private-network routing; check those on the
intended devices and installation.

## Styles

The committed `static/css/app.css` is built from `assets/app.css` with the pinned Tailwind
CSS and daisyUI versions in `package.json`. Node.js and pnpm are needed only to rebuild
styles; the application and container use the compiled file.

Automatic source detection is disabled: `assets/app.css` names `templates/` and `static/js/`
as its only sources, so a word in a guide, a test fixture or a Python identifier cannot add
or remove shipped CSS. CI rebuilds the stylesheet and fails when the committed file differs.

```sh
pnpm install --frozen-lockfile
pnpm run build:css
```

`pnpm run watch:css` rebuilds while editing templates. Commit the regenerated
`static/css/app.css` with the template or JavaScript class changes that require it. CI
verifies the pnpm archive checksum, installs from the frozen lockfile, rebuilds the
stylesheet and fails if the committed file differs.

The build scans `templates/` and explicitly excludes `docs/` with `@source not`, so
documentation prose can never add or remove shipped CSS. Other source files still take part
in Tailwind's automatic detection; keep class names out of Markdown when that matters.

## Interface foundations

The interface uses the committed, self-hosted stylesheet, system body fonts, a self-hosted
Barlow Condensed heading font and server-rendered Django templates.

**Tokens.** Colour roles map to daisyUI theme variables: `--color-base-100`,
`--color-base-200`, `--color-base-300`, `--color-base-content`, `--color-primary`,
`--color-secondary`, `--color-accent`, `--color-neutral`, `--color-info`,
`--color-success`, `--color-warning`, `--color-error` and `--color-focus`. Warning,
success and error surfaces have their own roles and content colours; keep status meaning in
text as well as colour. The `growbook` theme in `assets/app.css` supplies shared controls
and status colours, and four palettes selected through the root's `data-season` attribute
change surfaces, text, borders, actions, accents, muted text and focus colour. Spring uses
sage and cream, summer deeper leaf green and warm ivory, autumn earth brown and parchment,
winter blue green and cool pale surfaces. Status roles keep their meaning in every season.
Secondary controls use `--color-control-border` and `--color-control-hover`;
`--color-shadow-ink` supplies the notebook and floating-menu shadows.

Page headings use regular, upright Barlow Condensed, uppercase presentation and responsive
sizing from roughly 36 to 56 px at the default text size. The
[font's license and pinned source](../third_party/barlow-condensed/README.md) are retained
with the application. Spacing uses Tailwind's quarter-rem steps. `--radius-selector`,
`--radius-field` and `--radius-box` set the small, medium and large radii. The base font is
106.25% of the browser default with a 1.6 line height, and `--size-field` gives a 44 px
control target at that size. Main pages share a 1200 px content limit, horizontal padding
aligned with the header, and illustrated headings of equal height.

**Components.** Buttons and links use daisyUI's `btn`, with `btn-primary` for the primary
action, `btn-outline` for secondary actions and `btn-error` for destructive ones. Use one
primary action in a form; disabled controls keep their label, and an uploading form uses
`aria-busy` with a visible status line. `.field` places a label above its control with
`.field-help` and `.field-errors` underneath; create/edit forms mark required and optional
fields in words and associate help and errors through `aria-describedby`, and error
summaries link to fields. `.panel` uses a surface, hairline border, large radius and soft
seasonal shadow. Login and `.empty-state` panels reuse the decorative favicon mark, and an
empty state has an explanation plus one action. `.save-confirmation`, `.error-summary` and
`.archive-label` use the shared status colours and keep live-region semantics. `.icon`
renders the inline [Lucide subset](../third_party/lucide/README.md) with a two-pixel stroke,
hidden from assistive technology; a control's accessible name comes from its text.
`.disclosure-menu` and `.filter-disclosure` use native `details`/`summary`, open as
nonmodal surfaces, and return focus to their summary on Escape.

**Capture.** The diary has a decorative, text-free seasonal tree header above collapsed
filters, with four optimized self-hosted WebP illustrations. One inline composer holds the
date, optional title, three-row note and the action row: **Add tags** and **Add photos** on
the left, **Save observation** on the right, wrapping on narrow screens. Editing also shows
saved photos and Cancel. Notes grow with their content where `field-sizing` is supported.
Cards put photos beside the note on wide screens and above it on narrow ones; captions
appear below their photos. Full-form actions stick to the bottom below 600 px when the
viewport is at least 600 px tall. Quick-add keeps its normal layout. Diary results, counts,
filter chips and pagination share one server-rendered partial; `filters.js` replaces list
results and updates filter URLs without caching private HTML in history or browser
storage, and never clears the composer or its FileLists. Keyboard focus uses a visible
three-pixel outline, transitions respect `prefers-reduced-motion`, and the strict content
security policy stays unchanged. With JavaScript the main header sticks to the top and
keeps anchor targets clear; it returns to normal flow when it consumes more than a quarter
of the viewport, and stays in normal flow without JavaScript.

**Registers.** Main navigation links to Diary, Growing areas, Crops and varieties,
Plantings and Economy; `/garden/` redirects to Growing areas. Crop headings use native
disclosures and variety names select their information independently of them. Name lists
use `.record-list`, `.record-name` and `.record-info`, with underlined names, the selected
name highlighted and archived records labelled. Crop/variety and planting results share
`.record-workspace`: a list plus one selected details panel, side by side at 900 px and
wider and stacked below, with a centred inset prompt before a selection. Selections bring
off-screen details into view and focus their labelled region; **Back to list** returns to
the selected name and reveals its ancestor groups. Without JavaScript the panel stays
hidden and names link to full records. Browser history restores disclosure states, the
selected details and scroll after returning from editing; record content is reloaded from
authenticated endpoints and never cached in history. Category grids and cards, seasonal
botanical placeholders and quiet footer actions are shared by areas, crop categories and
planting categories. Nested event tables keep their own horizontal scrolling.

## GitHub Actions

[`ci.yml`](../.github/workflows/ci.yml) runs on pull requests, pushes to `main`, manual
dispatch and weekly. Linux jobs check lockfile consistency, Ruff, translations, database and
migration checks, the private-data hygiene check, and the application and Chromium suites in
the development image.
Development and production share the same native builds and application dependencies. The
container job validates workflow syntax and the Compose files (the default file, the
optional host-port override and the development file), builds the production image, and
runs [`tools/check_image.py`](../tools/check_image.py) with disposable storage: migrations,
deployment settings, non-root execution, refusal without operator configuration, and the
persisted WAL journal mode. A second container job does the same build, runtime check and
Trivy audit on an arm64 runner (named **Container (Linux arm64)**, `ubuntu-24.04-arm`), so
aarch64 is exercised on every run instead of only at release time, and its cache
(`ci-production-arm64`) is what the arm64 release build restores from `main`. Add
**Container (Linux arm64)** to the `main` ruleset so an aarch64-only break cannot reach a
tag; the release guard waits for the whole CI run, so a skipped or failing arm64 leg blocks
publishing either way.

The application check job is named **Checks (Linux)**. GitHub required status checks match
job names exactly; keep the `main` ruleset aligned when renaming a required job.

Weekly scheduled and manually dispatched CI runs build every image variant without the
build cache, refreshing Debian package upgrades and installed system dependencies. They
write the production caches and publish no image. Translation compilation uses a separate
production build stage, so catalog changes preserve cached application dependencies and
Chromium installation layers.

Only those runs and pushes to `main` write the production caches; a pull request restores
them and writes nothing of its own. It therefore builds what `main` last built instead of
keeping a package layer that the refresh cannot replace. A security update that lands
between refreshes leaves both container jobs failing the Trivy audit until the next
refresh, and dispatching CI on `main` is what refreshes the cache the pull requests read.

Unexpected deployment-check issues fail CI; W005/W021 remain visible. Python audits check
locked application, development and build-only dependencies with `pip-audit`. Trivy checks
the final image and fails on fixable high or critical vulnerabilities. These checks do not
prove custom-built native libraries are vulnerability-free. The CI workflow does not publish
images, upload test state or deploy the application.

[`dependency-review.yml`](../.github/workflows/dependency-review.yml) runs on every pull
request and fails when a change adds a dependency with a known high or critical
vulnerability. It is a required status check on `main`.
[`zizmor.yml`](../.github/workflows/zizmor.yml) audits workflows for Actions-specific
security problems on pull requests, pushes to `main`, manual dispatch and weekly.
[`scorecard.yml`](../.github/workflows/scorecard.yml) publishes an OpenSSF Scorecard and its
SARIF results to code scanning on `main` and weekly. These reports are advisory; they do not
replace the build, test, audit and image checks above.

## Dependency updates

[`dependabot.yml`](../.github/dependabot.yml) checks uv and GitHub Actions weekly, with a
seven-day version-update cooldown so releases can age before review. Docker base-image tags
and digests are checked daily and excluded from cooldown because image rebuilds can carry
Debian security fixes. Dependabot does not update individual Debian packages; the shared
Docker base stage upgrades inherited packages within the pinned Debian release. Dependabot
security updates are not delayed by cooldown. Patch updates may be auto-merged by
[`dependabot-auto-merge.yml`](../.github/workflows/dependabot-auto-merge.yml) after required
checks pass; minor and major updates still require review. The helper skips automatic merge
setup when commits are unsigned or were added by a maintainer; it keeps Dependabot's
signature verification enabled. Django stays on 5.2 LTS and Python stays on the current
minor series. Dependabot resolves with its bundled Python 3.14.9; CI and containers use the
pinned 3.14.10.

Debian repositories can change between builds from the same commit. The release image
digest and SBOM identify the resulting runtime; use the published digest for repeatable
deployments as described in [Release images](#release-images).

Python dependency updates change `pyproject.toml` and `uv.lock`. Every Docker build exports
hash-pinned runtime and development requirements from the lockfile in a build-only stage
using `uv export --locked`. A missing or outdated lockfile fails the build. The exports are
not tracked in the checkout, so Dependabot PRs need no manual export commit.

Use uv 0.13.8 for lockfile tooling, matching CI and the digest-pinned Docker export image.
This tooling can run on the editing host without installing or running the application
there. Update the export-tool image manually together with CI's `UV_VERSION`.

Review `requirements-build.txt`, the pinned uv/audit tools, checksum-pinned native sources
and vendored HTMX manually. Base-image Python patch updates must stay aligned with
`.python-version` and `pyproject.toml`.

## Release images

[`release.yml`](../.github/workflows/release.yml) creates a GitHub Release and publishes
Linux images for `linux/amd64` and `linux/arm64` to `ghcr.io/martinwa/growbook` when an
`X.Y.Z` tag is pushed. The Git tag is the release version used for the image tag, OCI
version label and application footer; the workflow passes it as the Docker build argument
`GROWBOOK_VERSION`. No version bump in `pyproject.toml` or `uv.lock` is needed. The static
project version is local tooling metadata, not the container release version. Tags have no
`v` prefix or prerelease suffix and must point to a commit from `main` whose push or
manually dispatched CI run has passed. The latest push run or the latest manually dispatched
run must have succeeded for that exact commit; pull-request and scheduled runs do not
qualify. Merge the changes, wait for that commit's CI, then tag it:

```sh
git switch main
git pull --ff-only
git fetch origin --tags
git tag -a X.Y.Z -m "Release X.Y.Z"
git push origin X.Y.Z
```

Dependabot auto-merges use `GITHUB_TOKEN`, so GitHub does not trigger push CI for the merge
commit. After such a merge, open **Actions → CI → Run workflow**, select `main`, and wait for
all checks to pass before tagging. The CLI equivalent is `gh workflow run ci.yml --ref main`.
Confirm the successful run tested the commit you will tag; if `main` advances, run CI again
for the new commit before tagging it.

From PowerShell, after the same checkout update, tag fetch and successful CI, use
[`tools/tag_release.ps1`](../tools/tag_release.ps1):

```powershell
.\tools\tag_release.ps1 -WhatIf
.\tools\tag_release.ps1 -Push
```

The script requires a clean `main` checkout and increments the highest local stable tag's
patch number by default (`0.1.9` becomes `0.1.10`). Use `-Version X.Y.Z` to choose a version
explicitly or create the first release. Without `-Push`, it creates the annotated tag
locally; `-WhatIf` previews the operation. If a push fails, the local tag remains and the
error gives the command to retry that push.

No manual GitHub Release creation is needed; watch **Actions → Release image** for
completion. A manually published stable GitHub Release remains a supported trigger. Tag
deletion is ignored, and moving an existing tag is rejected; correct failures by rerunning
the failed workflow, not by force-pushing tags.

Tagging immediately after a merge is fine: the guard waits up to fifteen minutes for the CI
run on the tagged commit to conclude instead of failing when that run is still going. CI
that fails, or a wait that expires, still refuses to publish, and rerunning the workflow
afterwards is safe because an existing image version is never overwritten. A transient
GitHub API error during the wait is retried; a lookup that keeps failing stops the run
before anything is published.

The workflow builds each platform separately by digest on a runner of that architecture
(`ubuntu-24.04` for amd64, `ubuntu-24.04-arm` for arm64), then runs
[`tools/check_release_image.sh`](../tools/check_release_image.sh) per platform: it pulls the
digest, runs [`tools/check_image.py`](../tools/check_image.py) inside the container (non-root
runtime, no development tooling, stamped version, HEIC decoding, deployment checks, refusal
without operator configuration, startup migration and the persisted WAL journal mode) and
audits the platform with [`tools/audit_image.sh`](../tools/audit_image.sh) while writing its
SBOM.

The two platform builds run in parallel and hand their digest and SBOM to a publishing job
through a short-lived workflow artifact.

The release builds ignore the build cache: each step that builds a release image sets
`no-cache: true`, so the published image installs the Debian packages that are current at
release time instead of reusing an OS layer that an earlier run cached. Every release leg is
therefore a full compile of the native layers, and a Debian security update that landed after
CI last ran still reaches the released image. The CI container jobs on `main` continue to
build the production image for both architectures and write `ci-production-amd64` and
`ci-production-arm64` with `mode=max`, and the release steps declare those scopes as before,
but a release no longer restores layers from them. An arm64 image is never built under
emulation, in CI or in a release.

The publishing job then composes the `X.Y.Z` index from the two checked digests, attests
provenance against the index and each platform SBOM against its own manifest digest, and
only then creates or publishes the GitHub Release with generated notes, preserving an
existing release's notes. If the version is the highest published stable version and GitHub
identifies it as the current release, `:latest` moves to the tested index. Existing image
version tags are not overwritten; prereleases and older releases do not update `:latest`.
A rerun after the image has been pushed stops at the no-overwrite guard and requires
investigation. There is no automatic deployment; use version tags or digests for
reproducible deployments.

The server applies pending migrations at startup by default. Set
`GROWBOOK_MIGRATE_ON_START=false` to run migrations separately; see
[Upgrade an existing installation](deployment.md#upgrade-an-existing-installation).

GHCR creates a container package private even for a public repository, and container package
visibility cannot be changed through the REST API: it exposes only read, delete and restore
for packages. Set the package visibility to public once under the repository's package
settings before the first release is recommended to anyone; later releases inherit it. The
workflow's last step requests an anonymous pull token and emits a warning naming that manual
step, so a release never claims public pullability it does not have.

The arm64 image is built, checked and audited natively on an arm64 runner, in CI and in the
release workflow, so a released digest reflects a real aarch64 build. Verify an actual arm64
host boots a released digest before relying on it.

Before recommending a published image, verify anonymous pulls and its attestation. Keep
deployment configuration private and pin the published digest rather than a moving tag.
`compose.yaml` continues to build locally unless deliberately overridden.

```sh
gh attestation verify oci://ghcr.io/martinwa/growbook:X.Y.Z --repo MartinWa/growbook
```

Review the [native distribution requirements](../third_party/README.md) before each release.
The SBOM and vulnerability scan are useful evidence, not a complete licensing or security
guarantee.
