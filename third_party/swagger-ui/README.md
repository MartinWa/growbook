# Swagger UI 5.36.0

Vendored from the npm `swagger-ui-dist` package; distributed under the included
Apache-2.0 license.

- Package SHA-512: `sha512-Lt6/Kb0gXmLdiabLhvso4VdJP9V7qJWB0zh0gzeXUV3+CMOTzMmvpJRSXN7EAjfwixwmoi9ezDbLHiexC7lA9A==`
- `swagger-ui-bundle.js` SHA-256: `b164c5a22535e40dae08c00271674a67e437ec195098c55fcedf8ffac7ac3c88`
- `swagger-ui.css` SHA-256: `3032cce5cff8fe945fa1477d8b5d2e590187729edda2102823c941e345fb613b`
  (published file `7eb9f5a125e4bc9b3f0b71869b66d1a3d7c501f972f53d44ef91c2ae6c5bda5e`, less the
  trailing `sourceMappingURL` comment described below)
- Source: <https://github.com/swagger-api/swagger-ui>
- Runtime files: `static/vendor/swagger-ui-5.36.0/swagger-ui-bundle.js`, `static/vendor/swagger-ui-5.36.0/swagger-ui.css`
- Bundled third-party notices: [`swagger-ui-bundle.js.LICENSE.txt`](swagger-ui-bundle.js.LICENSE.txt)

No runtime CDN or npm/build step is required.

The stylesheet ships without its trailing `/*# sourceMappingURL=swagger-ui.css.map*/`
comment. The map is a debugging aid that this installation never serves, and leaving the
reference in place made `collectstatic` fail: WhiteNoise's manifest storage rewrites every
CSS reference and refuses one whose target does not exist. Everything else in both runtime
files is the published package byte for byte.
