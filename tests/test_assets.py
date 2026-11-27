import ast
import hashlib
import re
from pathlib import Path

import polib
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def test_every_marked_source_message_has_a_swedish_translation():
    messages = set()
    for folder in ("core", "diary", "garden", "economy", "growbook", "planner"):
        for path in (ROOT / folder).rglob("*.py"):
            if "migrations" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                ):
                    messages.add(node.args[0].value)
    for path in (ROOT / "templates").rglob("*.html"):
        source = path.read_text(encoding="utf-8")
        messages.update(re.findall(r"{%\s*translate\s+'([^']+)'(?:\s+as\s+\w+)?\s*%}", source))
    catalog = polib.pofile(str(ROOT / "locale" / "sv" / "LC_MESSAGES" / "django.po"))
    translated = {entry.msgid for entry in catalog if entry.translated()}
    assert messages <= translated, f"Missing Swedish translations: {messages - translated}"


def test_month_vignettes_are_committed_webp_within_budget():
    """One 3:2 picture per month, small enough to stay decorative."""
    names = [
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    ]
    folder = ROOT / "static" / "images" / "calendar"
    for name in names:
        path = folder / f"{name}.webp"
        assert path.exists(), name
        assert path.stat().st_size <= 120 * 1024, name
        with Image.open(path) as image:
            assert image.format == "WEBP", name
            assert image.size == (720, 480), name


def test_vendored_htmx_matches_its_documented_checksum():
    notice = (ROOT / "third_party" / "htmx" / "README.md").read_text(encoding="utf-8")
    expected = re.search(r"Script SHA-256: `([0-9a-f]{64})`", notice).group(1)
    actual = hashlib.sha256(
        (ROOT / "static" / "js" / "htmx-2.0.11.min.js").read_bytes()
    ).hexdigest()
    assert actual == expected
    assert (ROOT / "third_party" / "htmx" / "LICENSE").exists()


SWAGGER_DIR = "vendor/swagger-ui-5.36.0"
SWAGGER_FILES = ("swagger-ui-bundle.js", "swagger-ui.css")


def test_vendored_swagger_ui_matches_its_documented_checksums():
    notice = (ROOT / "third_party" / "swagger-ui" / "README.md").read_text(encoding="utf-8")
    for name in SWAGGER_FILES:
        expected = re.search(rf"`{name}` SHA-256: `([0-9a-f]{{64}})`", notice).group(1)
        actual = hashlib.sha256((ROOT / "static" / SWAGGER_DIR / name).read_bytes()).hexdigest()
        assert actual == expected, name
    assert (ROOT / "third_party" / "swagger-ui" / "LICENSE").exists()


def test_vendored_swagger_ui_references_only_files_it_ships():
    """A missing target makes collectstatic fail once WhiteNoise rewrites the reference."""
    folder = ROOT / "static" / SWAGGER_DIR
    for name in SWAGGER_FILES:
        source = (folder / name).read_text(encoding="utf-8")
        for reference in re.findall(r"sourceMappingURL=([^\s*]+)", source):
            assert (folder / reference).exists(), f"{name} references {reference}"


def test_the_stylesheet_is_generated_from_frontend_sources_only():
    """A word in a guide, test or Python identifier must never change shipped CSS."""
    source = (ROOT / "assets" / "app.css").read_text(encoding="utf-8")

    assert '@import "tailwindcss" source(none);' in source
    declared = re.findall(r'@source\s+"([^"]+)"', source)
    assert declared
    assert all(entry.startswith(("../templates/", "../static/")) for entry in declared), declared
    assert "@source not" not in source
