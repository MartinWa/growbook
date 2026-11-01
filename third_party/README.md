# Dependency notices and distribution checks

Growbook's license does not replace dependency licenses. Preserve the licenses in
installed Python distributions and the original notice for vendored HTMX.

| Dependency | License / packaging note |
|---|---|
| Django, asgiref, sqlparse | BSD licenses; retain installed license files. Django admin also bundles independently licensed frontend assets. |
| Pillow | MIT-CMU and bundled codec notices in its distribution. |
| pillow-heif source | BSD-3-Clause wrapper. **Prebuilt wheels bundle x265 (GPLv2)** and must not be redistributed as the project's production dependency bundle. |
| libheif, libde265 | LGPLv3 native libraries, dynamically linked in the decoder-only image. Included corresponding source archives and original licenses remain separately licensed. |
| WhiteNoise, filelock | MIT. |
| tzdata | Apache-2.0 and timezone-data notices in its distribution. |
| Waitress | Zope Public License 2.1. |
| HTMX | 0BSD, original license in [`htmx/LICENSE`](htmx/LICENSE). |
| SQLite | Public domain; source/runtime inputs are checksum-pinned. |

## HEIC packaging boundary

Local `uv sync` installs an upstream pillow-heif wheel for private development.
That wheel's full bundled license is authoritative, not its BSD source-package
metadata. Do not publish `.venv`, copy its binaries into the application, or build
production deployment from that wheel. Private use does not establish permission
to distribute the combined application under incompatible terms.

The Docker definition builds the BSD wrapper from source against decoder-only
libheif/libde265 shared libraries. It disables x265, x264, other external codecs
and plugin loading; build-time assertions check HEIC decoding and absence of x265.
The native LGPL sources are retained in `/opt/native/share/sources` and original
licenses in `third_party/libheif` and `third_party/libde265`. Users must remain able
to replace/relink those libraries; do not apply Growbook's noncommercial terms to
them or restrict reverse engineering required for debugging their modifications.

The Linux/amd64 image has now passed a local build and runtime trial.
Linked-library inspection confirmed that the source-built wrapper uses
libheif/libde265 without x265. Before publishing any binaries/images, still
verify the complete linked-library inventory, notices/corresponding sources,
and LGPL compliance. This checklist is not a legal opinion or a claim that
source metadata or a successful build alone proves compatibility.
