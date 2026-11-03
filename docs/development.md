# Development, CI and releases

Canonical guide for local checks, browser tests, styles, translations, workflow audits,
dependency updates and release publishing. First-time setup is in the
[main README](../README.md#run-locally). Deployment procedures are in the
[deployment guide](deployment.md).

## Test and check

```sh
uv run python tools/run_tests.py -q
uv run ruff check .
uv run ruff format --check .
uv run python manage.py check --database default
uv run python manage.py makemigrations --check --dry-run
```

Use `tools/run_tests.py`, especially on Windows: it activates the fixed SQLite runtime
before test plugins import it, compiles translations and keeps temporary artifacts under
ignored `local/`. Tests use real file-backed SQLite/WAL and synthetic images, covering
capture, observation editing and deletion, permissions, links and derived filters,
Unicode search, image failures, authorization, retries, concurrency and clean-instance
recovery. Browser tests are excluded from the default run.

For UI-copy changes, update the English source messages and
`locale/sv/LC_MESSAGES/django.po`, then recompile with
`tools/compile_translations.py`. Compiled `.mo` files are generated; tests and Docker
compile them automatically.

## Browser tests

Install Playwright's Chromium, then run the browser suite separately:

```sh
uv run python -m playwright install chromium --no-shell
uv run python tools/run_tests.py -q -m browser
```

To use an installed Chrome instead:

```sh
# POSIX shell
GROWBOOK_BROWSER_CHANNEL=chrome uv run python tools/run_tests.py -q -m browser
```

```powershell
# PowerShell
$env:GROWBOOK_BROWSER_CHANNEL = "chrome"
uv run python tools/run_tests.py -q -m browser
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
dispatch and weekly. Windows jobs check locked dependencies, generated Docker
requirements, Ruff, translations, database and migration checks, and the application and
Chromium suites with the fixed SQLite helper. The Linux job validates workflow syntax,
builds the production image, and checks migrations, deployment settings and non-root WSGI
initialization with disposable storage.

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

[`dependabot.yml`](../.github/dependabot.yml) requests weekly updates for uv, GitHub
Actions and the Docker base image. Version updates use a seven-day cooldown so a release
can age before review; security updates are not delayed. Patch updates may be auto-merged
by [`dependabot-auto-merge.yml`](../.github/workflows/dependabot-auto-merge.yml) after
required checks pass; minor and major updates still require review. Django stays on 5.2
LTS and Python stays on the current minor series. Dependabot resolves with its bundled
Python 3.14.9; CI and containers use the pinned 3.14.10.

After a uv update, regenerate the Docker requirements on the PR branch before merging:

```sh
uv export --locked --no-dev --no-emit-project --format requirements-txt --output-file requirements.txt
```

Review `requirements-build.txt`, the pinned uv/audit tools, checksum-pinned native sources
and vendored HTMX manually. Base-image Python patch updates must stay aligned with
`.python-version` and `pyproject.toml`.

## Release images

[`release.yml`](../.github/workflows/release.yml) creates a GitHub Release and publishes a
Linux/amd64 image to `ghcr.io/martinwa/growbook` when an `X.Y.Z` tag is pushed. The Git tag
is the release version used for the image tag and OCI version label; no version bump in
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
