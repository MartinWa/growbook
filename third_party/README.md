# Dependency notices and distribution checks

Growbook's license does not replace dependency licenses. Preserve the licenses in
installed Python distributions and the original notice for vendored HTMX.

The committed stylesheet includes Tailwind CSS and daisyUI. Their original MIT
licenses are retained in this directory, which Docker copies alongside the
application and compiled CSS.

Pillow's installed `pillow-*.dist-info/licenses/LICENSE` retains the codec notices.
This software is based in part on the work of the Independent JPEG Group and the
FreeType Team. The bundled FreeType component uses the FreeType License, whose
text is included in that installed license file.

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
| Tailwind CSS, 4.3.4 | MIT, original copyright and permission notice in [`tailwindcss/LICENSE`](tailwindcss/LICENSE). |
| daisyUI, 5.7.50 | MIT, original copyright and permission notice in [`daisyui/LICENSE`](daisyui/LICENSE). |
| Lucide icon subset, 0.468.0 | Free ISC-licensed icons; original SVGs and attribution in [`lucide/README.md`](lucide/README.md) and [`lucide/LICENSE`](lucide/LICENSE). |
| Barlow Condensed, regular heading font | SIL Open Font License 1.1; pinned source and checksum in [`barlow-condensed/README.md`](barlow-condensed/README.md), original license in [`barlow-condensed/LICENSE`](barlow-condensed/LICENSE). |
| SQLite | Public domain; source/runtime inputs are checksum-pinned. |
| PowerShell Core | MIT with bundled .NET/dependency notices under `/opt/powershell`; included only in the development/test image for launcher checks. |
| pytest-xdist, execnet | MIT; development/test image only, with installed license files retained. |

## HEIC packaging boundary

Development, test and production images share the source-built pillow-heif wrapper.
The development virtualenv installs only the separately exported development group,
without dependency resolution, so it does not replace that wrapper with an upstream wheel.
Do not run `uv sync` in the container or copy host virtualenv binaries into an image.

The Docker definition builds the BSD wrapper from source against decoder-only
libheif/libde265 shared libraries. It disables x265, x264, other external codecs
and plugin loading; build-time assertions check HEIC decoding and absence of x265.
The native LGPL sources are retained in `/opt/native/share/sources` and original
licenses in `third_party/libheif` and `third_party/libde265`. Users must remain able
to replace/relink those libraries; do not apply Growbook's noncommercial terms to
them or restrict reverse engineering required for debugging their modifications.

Before publishing binaries/images, verify the complete linked-library inventory,
notices/corresponding sources, library replacement/relinking behavior and LGPL
compliance. This checklist is not a legal opinion or a claim that source metadata
or a successful build alone proves compatibility.

## Access and replacement

The image retains upstream licenses at `/app/third_party/libheif/COPYING` and
`/app/third_party/libde265/COPYING`, and the corresponding native source archives
at `/opt/native/share/sources/`. The `COPYING` files include both LGPLv3 and GPLv3
texts. Build commands and flags are in the repository's `Dockerfile`; source
archives are readable without root access. For an already-built image:

```sh
container=$(docker create growbook:local)
docker cp "$container:/opt/native/share/sources" ./native-sources
docker rm "$container"
```

libheif/libde265 are loaded dynamically from `/opt/native/lib`. Recipients can
use a derived image to replace the libraries with interface-compatible versions,
or set `LD_LIBRARY_PATH` to a directory containing their replacements. Such a
directory must permit shared-library loading; a `noexec` mount will not work.
Preserve the original library SONAMEs and provide dependencies needed by the
replacement build. The project does not impose its noncommercial restrictions on
these separately licensed libraries or restrict debugging their modifications.

The upstream `pillow-heif` source package also retains its generic wheel notice,
which mentions x265. That notice describes upstream binary wheels, not this source
build; inspect the actual linked libraries rather than infer them from metadata.
Review the complete linked inventory and applicable license terms before each
binary release. The engineering checks are not a legal compliance opinion.
