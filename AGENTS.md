# growbook

Cross-agent instructions for contributors and coding assistants working in this
repository.

## Read first

- [`README.md`](README.md) — current features, limitations, quick start and documentation
  index.
- [`docs/usage.md`](docs/usage.md) — screen behavior, save retries and no-JavaScript
  fallbacks.
- [`docs/development.md`](docs/development.md) — tests, browser suite, translations, CI,
  dependency updates and release publishing.
- [`docs/deployment.md`](docs/deployment.md) — Docker, private configuration, upgrades and
  the SQLite runtime.
- [`docs/backup-and-recovery.md`](docs/backup-and-recovery.md) — backup, verification,
  clean-instance restore and orphan cleanup.
- [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE) — PolyForm Noncommercial 1.0.0, with the
  standard terms left unmodified.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`CLA.md`](CLA.md) — contribution workflow and
  the agreement to sign before a first merge.
- [`third_party/README.md`](third_party/README.md) — dependency licensing and the
  decoder-only HEIC packaging boundary.

## Scope and documentation ownership

The [README](README.md#current-features) is the source of truth for implemented
features and deliberate limitations; do not duplicate that inventory here.
Do not implement a feature outside the current scope without agreeing it with
the maintainer first. A planned feature is not an implemented capability.

Maintained documentation describes current behavior and how to run, test, build
and operate it. Keep each procedure in one canonical guide and link to it rather
than copying it. Do not add public roadmap documents, milestone identifiers,
changelogs or claims about verification that has not been performed.

Keep the next-step checklist in ignored private `planning/PLAN.md` when available.
Keep it concise and focused on remaining work; do not accumulate planning history
or verification logs there. Track bugs and user-testing feedback in issues, using
synthetic examples. Do not publish the planning workspace or copy its status into
maintained documentation.

## Language and publication

- Code, comments, identifiers, migrations, database table names and maintained
  documentation are in **English**.
- Swedish is the default interface locale; English is available. Use English source
  messages with the translation catalogs. Do not translate stored user-entered text.
- This repository is public. Keep personal garden records, exports, photos,
  credentials, hostnames, volume paths, proxy names and user/group IDs out of
  committed files. Use synthetic fixtures and generic examples.
- Never commit `.env` files, databases, media, backups, recovery configuration or
  local tool state. Live state belongs in ignored directories or private
  configuration repositories.
- Licensing is **PolyForm Noncommercial 1.0.0**. Describe the project as
  source-available, not OSI open source. Keep the standard license text intact,
  preserve dependency notices, and do not add commercial-use exceptions or
  relicense without an explicit decision. Contributions are accepted under
  [`CLA.md`](CLA.md), which grants the rights an additional licence would need.

## Database and integrity

- SQLite WAL is suitable for small, private installations **on a filesystem local
  to the application host**, not SMB/NFS storage mounted elsewhere.
- One application worker is the deployment default, not a SQLite correctness
  guarantee. Threads and management commands can contend too. Keep transactions
  short, keep the configured timeout, and test overlapping writes.
- Never back up a live SQLite database by copying its database file. Use the backup
  API or `VACUUM INTO`, and coordinate the snapshot with photo storage.
- Database changes use Django migrations, never hand-edited production schemas.
- Django `save()` does not run `full_clean()`, and bulk writes bypass hooks. Put
  validation in explicit write operations such as `diary/services.py` and
  revalidate shared state inside short serialized writes, after image processing
  outside transactions. The guard in `core/writes.py` only allows or rejects ORM
  writes; it does not validate data or replace database constraints.
- Photo owners use explicit foreign keys with **exactly one** target per row,
  enforced by a database check constraint. Extend that constraint deliberately when
  a new owner kind is added rather than relaxing it.

## UI, images and security

[Interface foundations](docs/design.md) documents the shared theme, tokens and form
conventions.

- Django forms/templates and HTMX are the primary UI. Use small external plain
  JavaScript modules for local state; do not add a SPA or frontend build pipeline
  without a demonstrated need.
- No runtime CDN dependencies. Pin and self-host frontend assets.
- Do not relax CSP with `unsafe-eval` or inline executable attributes. Configure
  HTMX accordingly.
- WhiteNoise serves application static assets, not uploaded media. Media routes and
  original downloads require authentication and authorization; originals are
  downloads, not inline content served from a public directory.
- Decode and re-encode permitted images; enforce upload count, byte and pixel
  limits. Retain originals privately and strip location/EXIF metadata from display
  derivatives. Process images outside write transactions.
- Keep the decoder-only HEIC source build; see the
  [packaging boundary](third_party/README.md#heic-packaging-boundary) before changing
  native dependencies or publishing images.
- Failed saves must be visible and retryable without silently dropping images or
  creating duplicate entries.

## Deployment

Follow the [deployment guide](docs/deployment.md) for builds, private configuration and
upgrades, and verify a backup and clean restore as described in the
[recovery guide](docs/backup-and-recovery.md) before real data use.

- Keep the installation private: HTTPS through an existing managed reverse proxy, with
  no published ports, public DNS or router forwarding.
- Run as a configured non-root user; `PUID`/`PGID` are not implemented. Keep hostnames,
  proxy and volume paths and user/group IDs in untracked configuration.
- Pin tested dependency versions and image inputs; limit log retention.
- Do not suppress the W005/W021 warnings from production `check --deploy`; see
  [Initialize and start](docs/deployment.md#initialize-and-start).

## Implementation conventions

Keep the application split into `core` (users, photos, settings, shared helpers and
operational commands), `diary` (entries and validated creation), and `garden`
(crop, variety and growing-area registers with validated maintenance). Add further
modules only when a feature needs them; do not add a generic repository, event
bus or separate API layer before it is needed.

Use `pytest-django` for domain and integration tests. Run the application and its
tests only in the Linux development container as described in
[Test and check](docs/development.md#test-and-check); the Windows host is for
editing and PowerShell launchers and never executes application code.
Version-pinned non-application tooling matching the container and CI (ruff, uv
lockfile operations, pnpm stylesheet builds) may run on the host. Tests use real
file-backed SQLite/WAL, not an in-memory substitute for contention.

Run the full [browser suite](docs/development.md#browser-tests) when requested, to confirm
Playwright works locally, or when interactive changes need regression coverage. Reuse
instructions already read in the current conversation. Cover the behaviors listed in
[Test and check](docs/development.md#test-and-check); browser tests exercise real form
interaction but are not evidence about device-native pickers or private-network routing.
Template styling does not need brittle snapshot tests, but interactive behavior and
accessibility do need checks.
