"""Install a checksum-verified SQLite DLL locally; never modify global Python."""

import hashlib
import io
import os
import platform
import sys
import urllib.request
import zipfile
from pathlib import Path

URL = "https://www.sqlite.org/2026/sqlite-dll-win-x64-3510300.zip"
SHA256 = "ea004f52ba873cc54d1cb908c2f7cf48c3064061d9ee3ded6a309284da5ff411"


def main():
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise SystemExit(
            "This helper is for Windows x64. Use a fixed native SQLite on other hosts."
        )
    with urllib.request.urlopen(URL, timeout=60) as response:
        archive = response.read(4 * 1024 * 1024)
    if hashlib.sha256(archive).hexdigest() != SHA256:
        raise SystemExit("SQLite archive checksum mismatch; nothing was installed.")
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        library = package.read("sqlite3.dll")
    target = Path(__file__).resolve().parent.parent / "local" / "sqlite" / "sqlite3.dll"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(library)
    os.replace(temporary, target)
    print("Installed project-local SQLite 3.51.3. Restart existing application/test processes.")


if __name__ == "__main__":
    main()
