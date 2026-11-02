import ast
import hashlib
import re
from pathlib import Path

import polib

ROOT = Path(__file__).resolve().parent.parent


def test_every_marked_source_message_has_a_swedish_translation():
    messages = set()
    for folder in ("core", "diary", "garden", "growbook"):
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
        messages.update(re.findall(r"{%\s*translate\s+'([^']+)'\s*%}", source))
    catalog = polib.pofile(str(ROOT / "locale" / "sv" / "LC_MESSAGES" / "django.po"))
    translated = {entry.msgid for entry in catalog if entry.translated()}
    assert messages <= translated, f"Missing Swedish translations: {messages - translated}"


def test_vendored_htmx_matches_its_documented_checksum():
    notice = (ROOT / "third_party" / "htmx" / "README.md").read_text(encoding="utf-8")
    expected = re.search(r"Script SHA-256: `([0-9a-f]{64})`", notice).group(1)
    actual = hashlib.sha256(
        (ROOT / "static" / "js" / "htmx-2.0.11.min.js").read_bytes()
    ).hexdigest()
    assert actual == expected
    assert (ROOT / "third_party" / "htmx" / "LICENSE").exists()
