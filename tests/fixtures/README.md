# Synthetic image fixtures

These tiny HEIC files were generated specifically for growbook from solid-color
Pillow images; they contain no personal photos or metadata. They are project test
data under the project license, not copied supplier/demo pictures.

- `synthetic.heic`: one 80 × 60 green RGB image.
- `synthetic-multi.heic`: the green image and a second blue image, for rejection tests.
- `corrupt-heic-codestream.heic`: `synthetic.heic` with one byte changed so the
  container still opens while the image bitstream ends early, for decoder-error tests.

They were encoded once during fixture preparation. Test execution only decodes
them and does not need a HEIC encoder. JPEG/PNG/WebP test inputs are generated at
runtime. Do not replace these with private phone photos.
