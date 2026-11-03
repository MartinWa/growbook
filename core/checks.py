import sqlite3

from django.core.checks import Error, Tags, register

from growbook.runtime import wal_runtime_is_fixed


@register(Tags.database)
def sqlite_runtime_check(app_configs, **kwargs):
    if wal_runtime_is_fixed(sqlite3.sqlite_version_info):
        return []
    return [
        Error(
            "The SQLite runtime lacks the WAL-reset fix.",
            hint="Use SQLite 3.51.3+ or a fixed backport. See docs/deployment.md#sqlite-runtime.",
            id="growbook.E001",
        )
    ]
