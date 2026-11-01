"""Bootstrap SQLite before pytest or its plugins can import sqlite3."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# SQLite must be loaded before pytest or plugins can import its extension.
import growbook  # noqa: E402, F401, I001

import pytest  # noqa: E402
from compile_translations import compile_catalogs  # noqa: E402

if __name__ == "__main__":
    compile_catalogs()
    temporary = Path(__file__).resolve().parent.parent / "local" / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    for name in ("TMP", "TEMP", "TMPDIR"):
        os.environ[name] = str(temporary)
    arguments = sys.argv[1:]
    if not any(argument.startswith("--basetemp") for argument in arguments):
        arguments += [
            "--basetemp",
            str(Path(__file__).resolve().parent.parent / "local" / "pytest"),
        ]
    raise SystemExit(pytest.main(arguments))
