import sqlite3

from django.core.exceptions import ImproperlyConfigured
from django.db.backends.sqlite3.base import DatabaseWrapper as SQLiteDatabaseWrapper

from growbook.runtime import wal_runtime_is_fixed


class DatabaseWrapper(SQLiteDatabaseWrapper):
    def get_new_connection(self, conn_params):
        if not wal_runtime_is_fixed(sqlite3.sqlite_version_info):
            raise ImproperlyConfigured(
                "SQLite lacks the WAL-reset fix. "
                "See docs/deployment.md#sqlite-runtime before using this database."
            )
        connection = super().get_new_connection(conn_params)
        mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if mode not in {"wal", "memory"}:
            connection.close()
            raise ImproperlyConfigured("SQLite could not enable WAL on this filesystem.")
        connection.execute("PRAGMA synchronous=FULL")
        return connection
