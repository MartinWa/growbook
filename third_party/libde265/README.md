# libde265 1.1.3

HEVC decoding shared-library input for the production Docker image. This is a
separately licensed LGPLv3 dependency, not Growbook application code.

- Source: <https://github.com/strukturag/libde265/releases/download/v1.1.3/libde265-1.1.3.tar.gz>
- SHA-256: `554228bd17788c99a7e63b37ab5634722190e6e2bf60c1dcb01cef328e133905`
- Unmodified upstream license: [`COPYING`](COPYING).
- Build flags: repository `Dockerfile`; build the shared library without encoder,
  SDL/sample decoder executables, or development tools.
- Corresponding source retained in the built image's `/opt/native/share/sources`.

Verify the actual built artifacts, their notices, and replacement/relink behavior
before binary publication.
