"""Load the project-local SQLite DLL before Python imports its SQLite extension."""

import ctypes
import os
import sys
from pathlib import Path

_dll_directory = None
_sqlite_library = None


def activate_sqlite():
    global _dll_directory, _sqlite_library
    if sys.platform != "win32" or _sqlite_library is not None:
        return
    path = Path(__file__).resolve().parent.parent / "local" / "sqlite" / "sqlite3.dll"
    if not path.is_file():
        return  # The database system check reports an unsafe system runtime.
    if "_sqlite3" in sys.modules:
        import sqlite3

        if not wal_runtime_is_fixed(sqlite3.sqlite_version_info):
            raise RuntimeError("SQLite was imported too early. Run tools/run_tests.py instead.")
        return
    _dll_directory = os.add_dll_directory(str(path.parent))
    _sqlite_library = ctypes.WinDLL(str(path))


def wal_runtime_is_fixed(version):
    """Upstream versions/backports containing the WAL-reset fix."""
    return (
        version >= (3, 51, 3)
        or (3, 50, 7) <= version < (3, 51, 0)
        or ((3, 44, 6) <= version < (3, 45, 0))
    )
