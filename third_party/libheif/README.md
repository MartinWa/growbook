# libheif 1.23.6

Decoder-only shared-library input for the production Docker image. This is a
separately licensed LGPLv3 dependency, not Growbook application code.

- Source: <https://github.com/strukturag/libheif/releases/download/v1.23.6/libheif-1.23.6.tar.gz>
- SHA-256: `4484346dc5995319dbc11e3a1c35d0a2ec46511ce370900869337fd2c7033125`
- Unmodified upstream license: [`COPYING`](COPYING).
- Build flags: repository `Dockerfile`, with libde265 decoding only, no external
  encoders/plugin loading, and no example programs.
- Corresponding source retained in the built image's `/opt/native/share/sources`.

Verify the actual built artifacts, their notices, and replacement/relink behavior
before binary publication.
