"""Initialize disposable development storage and start Django's polling reloader."""

import os
import subprocess
import sys

if __name__ == "__main__":
    subprocess.run([sys.executable, "tools/compile_translations.py"], check=True)
    subprocess.run([sys.executable, "manage.py", "migrate", "--noinput"], check=True)
    # --nostatic: /static/ goes through WhiteNoise (DEBUG autorefresh) instead of the
    # runserver handler, so stylesheets get an explicit revalidate cache header and a
    # rebuilt app.css is never answered from a heuristic browser cache.
    os.execv(
        sys.executable,
        [sys.executable, "manage.py", "runserver", "0.0.0.0:8000", "--nostatic"],
    )
