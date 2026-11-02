# growbook

Cross-agent instructions for contributors and coding assistants working in this
repository.

## Read first

- [`README.md`](README.md) — current application behavior and run/test/build instructions.
- [`docs/backup-and-recovery.md`](docs/backup-and-recovery.md) — backup, verification,
  clean-instance restore and orphan cleanup.
- [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE) — PolyForm Noncommercial 1.0.0, with the
  standard terms left unmodified.
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

Keep local plans and work/verification logs in ignored private material. When the
private `planning/` workspace is available, start with `planning/README.md` and
update progress/evidence only in `planning/PLAN.md`; do not publish that workspace
or copy its status into the maintained documentation.

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
  relicense without an explicit decision.

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
- Review native dependency notices: upstream `pillow-heif` wheels include a GPL
  encoder; the production image uses the documented decoder-only source build.
- Failed saves must be visible and retryable without silently dropping images or
  creating duplicate entries.

## Deployment

- Keep the installation private: HTTPS through an existing managed reverse proxy,
  no published application ports, no public DNS exposure and no router port
  forwarding.
- Run as a configured non-root user and verify volume permissions. `PUID`/`PGID`
  environment variables are not implemented.
- Hostnames, proxy network/snippet names, volume paths and user/group IDs live in
  untracked configuration or a private operations repository.
- Pin tested dependency versions and image inputs; limit log retention.
- Production `check --deploy` deliberately reports W005/W021 because this private
  installation does not claim control of sibling domains or enroll in HSTS preload.
  Do not suppress those warnings.
- Backup and a clean-instance restore must be verified before real data use.

## Implementation conventions

Keep the application split into `core` (users, photos, settings, shared helpers and
operational commands), `diary` (entries and validated creation), and `garden`
(crop, variety and growing-area registers with validated maintenance). Add further
modules only when a feature needs them; do not add a generic repository, event
bus or separate API layer before it is needed.

Use `pytest-django` for domain and integration tests. On Windows run tests through
`uv run python tools/run_tests.py` so the fixed SQLite runtime is activated before
test plugins. Tests use real file-backed SQLite/WAL, not an in-memory substitute for
contention checks.

Cover at least: text-only, photo-only and multi-photo entries, photo failures and
retries, upload limits, authorization for pages/media/downloads, overlapping writes,
and clean-instance restore. Browser tests cover real form interaction; they do not
verify device-native photo pickers or a deployment's private-network routing, so do
not present them as device evidence. Template styling does not need brittle
snapshot tests, but interactive behavior and accessibility do need checks.
