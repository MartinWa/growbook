# Development, CI and releases

Canonical guide for local checks, browser tests, styles, translations, workflow audits,
dependency updates and release publishing. The
[main README](../README.md#run-locally) links here for first-time setup. Deployment
procedures are in the [deployment guide](deployment.md).

## Container development

Install Docker Desktop on an x64 host, select Linux containers, and keep this checkout
on Windows. Use PowerShell from the repository root; neither a WSL checkout nor a WSL terminal is
required. Application execution and tests are supported in the Linux images, sharing
production's pinned Python, SQLite and decoder-only image libraries.

```powershell
docker version
docker compose -f compose.development.yaml build app
docker compose -f compose.development.yaml up -d app
docker compose -f compose.development.yaml logs --tail=50 app
docker compose -f compose.development.yaml exec app python manage.py createsuperuser
```

Open **http://127.0.0.1:8000/**. Startup compiles translations, applies migrations and
starts Django's development server. There are no seeded credentials. The service binds
port 8000 only to host loopback. `compose.development.yaml` is independent of the
production `compose.yaml`; it needs no production `.env`, proxy or private network.

The Windows checkout is mounted at `/app`. Save Python edits in Windows VS Code to
trigger Django's polling reloader; template and static edits also use the mounted
source immediately. Ordinary edits require no image rebuild. For translation edits,
run `python tools/compile_translations.py` in the container and restart the server.

The named `development-state` volume stores `/state/development` (database, photos
and generated secret), `/state/venv`, compiled translations and test/scratch files.
`editor-home` stores the container user's editor state. SQLite and photo files stay on
Docker's Linux filesystem, rather than on the Windows bind mount. Recreating containers
or running `docker compose -f compose.development.yaml down` preserves these volumes.
Do not use `down --volumes` unless all of this disposable state should be deleted.
Never mount production storage into this service.

The development virtualenv inherits production packages. Its separately exported dev
group installs without dependency resolution, preserving the source-built HEIC wrapper.
Startup checks the active Python, SQLite and codec inventory. After dependency or
Dockerfile changes, stop the service, rebuild, and recreate it with the commands above;
the virtualenv refreshes from the image while development records remain intact. Do not
run `uv sync` or install upstream HEIC wheels in this environment.

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

Test runs share a disposable test database under `/state/test-state`; run suites
sequentially within one Compose project. Temporary `compose run --rm` containers share
the development volumes but do not publish the service's HTTP port.

## Test and check

```powershell
docker compose -f compose.development.yaml run --rm app python tools/run_tests.py -q
docker compose -f compose.development.yaml run --rm app ruff check .
docker compose -f compose.development.yaml run --rm app ruff format --check .
docker compose -f compose.development.yaml run --rm app python manage.py check --database default
docker compose -f compose.development.yaml run --rm app python manage.py makemigrations --check --dry-run
```

Use `tools/run_tests.py` inside the container: it compiles translations and keeps
temporary artifacts in the Linux state volume. Tests use real file-backed SQLite/WAL and
synthetic images, covering
capture, observation editing and deletion, permissions, links and derived filters,
Unicode search, image failures, authorization, retries, concurrency and clean-instance
recovery. Browser tests are excluded from the default run.

For UI-copy changes, update the English source messages and
`locale/sv/LC_MESSAGES/django.po`, then recompile with
`tools/compile_translations.py`. Compiled `.mo` files are generated; tests and Docker
compile them automatically.

## Validated garden writes

Call `garden.services` operations with an active actor and outside an existing database
transaction. Every public garden edit and delete requires the keyword
`expected_updated_at`: a timezone-aware `datetime` containing the version the caller
read. Omitting it is a call error; `None`, strings, malformed values and timezone-naive
datetimes raise `ValidationError`. Forms preserve microseconds and require a timestamp
with an explicit timezone.

Register and planting edits/deletes use the target record's `updated_at`. Event
edits/deletes use the event's timestamp. `edit_register_photo` and
`delete_register_photo` use the photo owner's timestamp, including planting owners.
Photo and event writes also advance the owner's version. Read the saved state before
starting a subsequent independent edit; do not fetch a fresh timestamp merely to submit
values from a stale read. Creation needs no version token, and archive operations keep
their explicit desired-state contract.

For create/edit saves with submission receipts, retry the original payload, version
token and `submission_key`. Writers check the active actor and matching immutable
receipt before current association eligibility and stale-state checks within short
serialized writes. A matching retry returns the saved record; changed content or another
author conflicts. See [Saving changes and retries](usage.md#saving-changes-and-retries)
for the form workflow.

`diary.services.update_entry` follows the same receipt contract and returns the current
`DiaryEntry` on both a first save and an identical retry. Pass the original
`submission_key`, `expected_updated_at`, date, note, typed tag IDs, removal IDs and
uploads when retrying. Omitting a key generates a new submission for service callers;
browser edit forms require and preserve their key. Existing diary creation receipts
remain unchanged. Separate photo edits and deletions continue to use version checks.

## Browser tests

The development image includes Playwright's pinned Chromium and Linux browser libraries.
Run the full browser suite separately:

```powershell
docker compose -f compose.development.yaml run --rm app python tools/run_tests.py -q -m browser
```

The suite exercises Chromium and real form interaction. It does not verify a phone's
native camera/library picker or a deployment's private-network routing; check those on
the intended devices and installation.

## Styles

The committed `static/css/app.css` is built from `assets/app.css` with the pinned
Tailwind CSS and daisyUI versions in `package.json`. Node.js and pnpm are needed only
to rebuild styles; the application and container use the compiled file.

```sh
pnpm install --frozen-lockfile
pnpm run build:css
```

`pnpm run watch:css` rebuilds while editing templates. Commit the regenerated
`static/css/app.css` with the template or JavaScript class changes that require it.
CI verifies the pnpm archive checksum, installs from the frozen lockfile, rebuilds
the stylesheet and fails if the committed file differs.

## GitHub Actions

[`ci.yml`](../.github/workflows/ci.yml) runs on pull requests, pushes to `main`, manual
dispatch and weekly. Linux jobs check locked dependencies, both generated Docker
requirements files, Ruff, translations, database and migration checks, and the application
and Chromium suites in the development image. Development and production share the same
native builds and application dependencies. The container job validates workflow syntax,
builds the production image, and checks migrations, deployment settings and non-root WSGI
initialization with disposable storage.

The application check job is named **Checks (Linux)**. GitHub required status checks
match job names exactly; keep the `main` ruleset aligned when renaming a required job.

Weekly scheduled and manually dispatched CI runs build both images without the build
cache, refreshing Debian package upgrades and installed system dependencies. Pull
requests and pushes to `main` reuse the cache. A passing scheduled build refreshes CI
caches; it does not publish an image or update a running installation.

The stylesheet job verifies the generated CSS with the pinned frontend toolchain;
see [Styles](#styles).

Unexpected deployment-check issues fail CI; W005/W021 remain visible. Python audits check
locked application, development and build-only dependencies with `pip-audit`. Trivy
checks the final image and fails on fixable high or critical vulnerabilities. These checks
do not prove custom-built native libraries are vulnerability-free. The CI workflow does
not publish images, upload test state or deploy the application.

[`dependency-review.yml`](../.github/workflows/dependency-review.yml) runs on every pull
request and fails when a change adds a dependency with a known high or critical
vulnerability. It is a required status check on `main`.

[`zizmor.yml`](../.github/workflows/zizmor.yml) audits workflows for Actions-specific
security problems on pull requests, pushes to `main`, manual dispatch and weekly.
[`scorecard.yml`](../.github/workflows/scorecard.yml) publishes an OpenSSF Scorecard and
its SARIF results to code scanning on `main` and weekly. These reports are advisory; they
do not replace the build, test, audit and image checks above.

## Dependency updates

[`dependabot.yml`](../.github/dependabot.yml) checks uv and GitHub Actions weekly, with a
seven-day version-update cooldown so releases can age before review. Docker base-image
tags and digests are checked daily and excluded from cooldown because image rebuilds
can carry Debian security fixes. Dependabot does not update individual Debian packages;
the shared Docker base stage upgrades inherited packages within the pinned Debian
release. Dependabot security updates are not delayed by cooldown. Patch updates may be
auto-merged by [`dependabot-auto-merge.yml`](../.github/workflows/dependabot-auto-merge.yml) after
required checks pass; minor and major updates still require review. Django stays on 5.2
LTS and Python stays on the current minor series. Dependabot resolves with its bundled
Python 3.14.9; CI and containers use the pinned 3.14.10.

Debian repositories can change between builds from the same commit. The release image
digest and SBOM identify the resulting runtime; use the published digest for repeatable
deployments as described in [Release images](#release-images).

After a uv update, regenerate both Docker requirements files on the PR branch before
merging. Use uv 0.13.6 (also pinned in CI) for lockfile/export tooling; this can run on the
editing host without installing or running the application there:

```sh
uv export --locked --no-dev --no-emit-project --format requirements-txt --output-file requirements.txt
uv export --locked --only-group dev --no-emit-project --format requirements-txt --output-file requirements-dev.txt
```

Review `requirements-build.txt`, the pinned uv/audit tools, checksum-pinned native sources
and vendored HTMX manually. Base-image Python patch updates must stay aligned with
`.python-version` and `pyproject.toml`.

## Release images

[`release.yml`](../.github/workflows/release.yml) creates a GitHub Release and publishes a
Linux/amd64 image to `ghcr.io/martinwa/growbook` when an `X.Y.Z` tag is pushed. The Git tag
is the release version used for the image tag, OCI version label and application footer;
the workflow passes it as the Docker build argument `GROWBOOK_VERSION`. No version bump in
`pyproject.toml` or `uv.lock` is needed. The static project version is local tooling
metadata, not the container release version. Tags have no `v` prefix or prerelease suffix
and must point to a commit from `main` whose push CI run has passed. Merge the changes,
wait for that commit's CI, then tag it:

```sh
git switch main
git pull --ff-only
git fetch origin --tags
git tag -a X.Y.Z -m "Release X.Y.Z"
git push origin X.Y.Z
```

From PowerShell, after the same checkout update, tag fetch and successful CI, use
[`tools/tag_release.ps1`](../tools/tag_release.ps1):

```powershell
.\tools\tag_release.ps1 -WhatIf
.\tools\tag_release.ps1 -Push
```

The script requires a clean `main` checkout and increments the highest local stable tag's
patch number by default (`0.1.9` becomes `0.1.10`), including legacy `v`-prefixed tags when
comparing history. Use `-Version X.Y.Z` to choose a version explicitly or create the first
release. Without `-Push`, it creates the annotated tag locally; `-WhatIf` previews the
operation. If a push fails, the local tag remains and the error gives the command to retry
that push.

No manual GitHub Release creation is needed; watch **Actions → Release image** for
completion. A manually published stable GitHub Release remains a supported trigger. Tag
deletion is ignored, and moving an existing tag is rejected; correct failures by rerunning
the failed workflow, not by force-pushing tags.

The workflow rebuilds, smoke-checks and audits the image before pushing its `X.Y.Z` tag,
then attaches provenance and SBOM attestations. Only after those steps succeed does it
create or publish the GitHub Release with generated notes, preserving an existing release's
notes. If the version is the highest published stable version and GitHub identifies it as
the current release, `:latest` updates to the same image digest. Existing image version
tags are not overwritten; prereleases and older releases do not update `:latest`. A rerun
after the image has been pushed stops at the no-overwrite guard and requires investigation.
There is no automatic deployment; use version tags or digests for reproducible
deployments.

The server applies pending migrations at startup by default. Set
`GROWBOOK_MIGRATE_ON_START=false` to run migrations separately; see
[Upgrade an existing installation](deployment.md#upgrade-an-existing-installation).

Before recommending a published image, verify anonymous pulls and its attestation. Keep
deployment configuration private and pin the published digest rather than a moving tag.
`compose.yaml` continues to build locally unless deliberately overridden.

```sh
gh attestation verify oci://ghcr.io/martinwa/growbook:X.Y.Z --repo MartinWa/growbook
```

Review the [native distribution requirements](../third_party/README.md) before each
release. The SBOM and vulnerability scan are useful evidence, not a complete licensing or
security guarantee.
