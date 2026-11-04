"""Compile translations and keep file-backed tests in isolated Linux storage."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from compile_translations import compile_catalogs  # noqa: E402

if __name__ == "__main__":
    compile_catalogs()
    state = Path(os.environ.get("GROWBOOK_STATE_DIR", "/state"))
    temporary = state / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    for name in ("TMP", "TEMP", "TMPDIR"):
        os.environ[name] = str(temporary)
    arguments = sys.argv[1:]
    if not any(argument.startswith("--basetemp") for argument in arguments):
        arguments += [
            "--basetemp",
            str(state / "pytest"),
        ]
    raise SystemExit(pytest.main(arguments))
