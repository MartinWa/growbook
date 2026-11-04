"""Validate and compile maintained gettext catalogs."""

import os
from pathlib import Path

import polib


def compile_catalogs():
    root = Path(__file__).resolve().parent.parent
    output = Path(os.environ.get("GROWBOOK_LOCALE_DIR", root / "locale"))
    for source in (root / "locale").glob("*/LC_MESSAGES/*.po"):
        catalog = polib.pofile(str(source), check_for_duplicates=True)
        if any(entry.fuzzy or not entry.translated() for entry in catalog if not entry.obsolete):
            raise ValueError(f"The translation catalog is incomplete: {source.relative_to(root)}")
        destination = output / source.relative_to(root / "locale").with_suffix(".mo")
        destination.parent.mkdir(parents=True, exist_ok=True)
        catalog.save_as_mofile(str(destination))


if __name__ == "__main__":
    compile_catalogs()
    print("Translation catalogs compiled.")
