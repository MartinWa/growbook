"""Safety checks for the SQLite library supplied by the Linux runtime."""


def wal_runtime_is_fixed(version):
    """Upstream versions/backports containing the WAL-reset fix."""
    return (
        version >= (3, 51, 3)
        or (3, 50, 7) <= version < (3, 51, 0)
        or ((3, 44, 6) <= version < (3, 45, 0))
    )
