"""Application bootstrap, including a private patched SQLite DLL on Windows."""

from .runtime import activate_sqlite

activate_sqlite()
