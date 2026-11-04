"""Initialize disposable development storage and start Django's polling reloader."""

import os
import subprocess
import sys

if __name__ == "__main__":
    subprocess.run([sys.executable, "tools/compile_translations.py"], check=True)
    subprocess.run([sys.executable, "manage.py", "migrate", "--noinput"], check=True)
    os.execv(sys.executable, [sys.executable, "manage.py", "runserver", "0.0.0.0:8000"])
