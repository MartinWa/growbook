"""Compile maintained gettext catalogs without requiring system gettext on Windows."""

from pathlib import Path

import polib


def compile_catalogs():
    root = Path(__file__).resolve().parent.parent
    for source in (root / "locale").glob("*/LC_MESSAGES/*.po"):
        catalog = polib.pofile(str(source), check_for_duplicates=True)
        if any(entry.fuzzy or not entry.translated() for entry in catalog if not entry.obsolete):
            raise ValueError(f"The translation catalog is incomplete: {source.relative_to(root)}")
        catalog.save_as_mofile(str(source.with_suffix(".mo")))


if __name__ == "__main__":
    compile_catalogs()
    print("Translation catalogs compiled.")
