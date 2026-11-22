# Contributing

Thanks for considering a contribution. growbook is a private, self-hosted garden
diary; keep changes inside the scope documented in the [user guides](README.md#documentation)
and agree on a new feature with the maintainer before implementing it. A planned
feature is not an implemented capability.

## Before you start

- [README](README.md) — purpose and documentation index.
- [`docs/getting-started.md`](docs/getting-started.md) — shared screen behavior and limits;
  the feature guides document the rest.
- [`docs/development.md`](docs/development.md) — tests, browser suite, styles,
  translations, interface foundations, workflows and release images.
- [`AGENTS.md`](AGENTS.md) — the conventions this repository holds itself to.

## Ground rules

- Code, comments, identifiers, migrations and maintained documentation are in English.
  Interface source messages are English with Swedish translations in the catalogs;
  stored user-entered text is never translated.
- Do not include personal garden records, exports, photos, credentials, hostnames,
  volume paths, proxy names or user/group IDs. Use synthetic fixtures and generic
  examples, and never commit an `.env` file, database, media or backup.
- Keep procedures in their canonical guide and link to it instead of duplicating it.
- No CDN dependencies and no relaxed CSP. Pin and self-host frontend assets.
- Database changes use Django migrations. Validation belongs in explicit write
  operations, not in `save()`.
- Failed saves must stay visible and retryable without dropping photos or creating
  duplicate entries.
- Mention in the pull request which checks you ran and what you could not verify.

## Checks

Run tests and application checks only in the Linux development container, as described
in [Test and check](docs/development.md#test-and-check). The Windows host is for
editing and PowerShell launchers and never executes application code. Run the
[browser suite](docs/development.md#browser-tests) when a change affects interactive
behavior.

```sh
docker compose -f compose.development.yaml run --rm app python tools/run_tests.py -q
docker compose -f compose.development.yaml run --rm app ruff check .
docker compose -f compose.development.yaml run --rm app ruff format --check .
docker compose -f compose.development.yaml run --rm app python manage.py makemigrations --check --dry-run
```

## Contributor agreement

Before your first pull request can be merged, sign the [Contributor License
Agreement](CLA.md). An app comments on your pull request with a signing link; click it
once and the signature covers your future contributions too. You keep the copyright in
your contribution.

Signing matters because the project may be offered under terms other than its current
[PolyForm Noncommercial 1.0.0](LICENSE) licence, and the agreement is what lets a
contribution be part of both. If you contribute in the course of employment, read
section 6 of the agreement first: your employer may own the work, and an authorised
person has to sign for the entity.

The project does not use a Developer Certificate of Origin (`Signed-off-by`), because a
DCO certifies that a contribution may be distributed under the project's current
licence and does not permit relicensing it.

### For maintainers

Signatures are collected by [CLA Assistant](https://cla-assistant.io), a free GitHub
app, not by a workflow in this repository:

1. Create a public gist containing `CLA.md` and copy its URL.
2. Sign in at <https://cla-assistant.io> with GitHub, add this repository, and paste the
   gist URL.
3. Make **CLA Assistant** a required status check in the branch protection rules for
   `main`.

Signatures are stored in that gist, so contributor accounts never enter this
repository. An earlier draft used the `contributor-assistant/github-action` action; it
was archived upstream in 2026 and has no maintained release, so it is not used.

## License

Contributions are licensed under the PolyForm Noncommercial License 1.0.0 (see
[`LICENSE`](LICENSE) and [`NOTICE`](NOTICE)), and under the terms of the Contributor
License Agreement.
