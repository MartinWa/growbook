"""Wait for a development server and check its authenticated-page boundary."""

import time
from urllib.error import URLError
from urllib.request import urlopen

for attempt in range(60):
    try:
        with urlopen("http://127.0.0.1:8000/", timeout=2) as response:
            assert response.status == 200
            assert "/login/" in response.url, response.url
        break
    except URLError, TimeoutError:
        if attempt == 59:
            raise
        time.sleep(1)
else:
    raise RuntimeError("Development server did not become ready.")
print("Development HTTP startup and anonymous redirect passed.")
