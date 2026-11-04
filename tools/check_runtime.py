"""Assert the shared Linux runtime and decoder-only HEIC inventory."""

import platform
import sqlite3

import pillow_heif


def check_runtime():
    assert platform.system() == "Linux", "Run the application in the Linux container."
    assert platform.python_version() == "3.14.10", platform.python_version()
    assert sqlite3.sqlite_version_info == (3, 51, 3), sqlite3.sqlite_version
    info = pillow_heif.libheif_info()
    assert info["libheif"] == "1.23.6", info
    assert set(info["decoders"]) == {"libde265"}, info
    # libheif includes its built-in mask codec; no HEIC/AVIF encoders are shipped.
    assert set(info["encoders"]) == {"mask"}, info
    assert not info["HEIF"] and not info["AVIF"], info
    print(f"Python {platform.python_version()}, SQLite {sqlite3.sqlite_version}: {info}")


if __name__ == "__main__":
    check_runtime()
