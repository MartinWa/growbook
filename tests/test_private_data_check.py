"""The private-data hygiene scanner keeps its name and content rules.

The scanner also runs in CI, so these tests pin the rules that detect private data and the
deliberate exemptions: the two checksum-pinned vendored files, generated license bundles,
synthetic placeholder secrets and the ``hygiene: allow`` marker.
"""

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from tools import check_no_private_data as hygiene

ROOT = Path(__file__).resolve().parent.parent
GITHUB_TOKEN = "ghp_" + "0123456789abcdefghij" + "0123456789abcdef"
SYNTHETIC_SECRET = "synthetic-test-secret-not-for-real-installations-"
# Built at runtime so this module holds no literal private-looking address.
PRIVATE_ADDRESS = f"gardener@{'private-garden'}.se"


@pytest.mark.parametrize(
    "path",
    [
        "planning/PLAN.md",
        "reference/notes.txt",
        ".env",
        ".env.production",
        "db.sqlite3",
        "db.sqlite3-wal",
        "state.sqlite3-shm",
        "fixtures/records.csv",
        "exports/garden.zip",
        "invoices/2026-05.pdf",
    ],
)
def test_private_file_names_are_violations(path):
    assert hygiene.is_violation(path)


@pytest.mark.parametrize(
    "path",
    [".env.example", "docs/deployment.md", "economy/references.py", "tests/test_release.py"],
)
def test_ordinary_file_names_are_not_violations(path):
    assert not hygiene.is_violation(path)


def write(tmp_path, monkeypatch, name, content):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return hygiene.working_tree_sources([name])


def png_with_metadata(chunk_type):
    def block(kind, payload):
        return len(payload).to_bytes(4, "big") + kind + payload + b"\0\0\0\0"

    return (
        hygiene.PNG_SIGNATURE
        + block(b"IHDR", b"\0" * 13)
        + block(chunk_type, b"Comment\0synthetic")
        + block(b"IEND", b"")
    )


def test_a_real_email_address_is_reported_and_the_reserved_examples_are_not(tmp_path, monkeypatch):
    assert hygiene.content_violations(
        write(tmp_path, monkeypatch, "notes.md", f"write to {PRIVATE_ADDRESS}\n")
    ) == ["notes.md:1: email address"]

    for address in ("release@example.invalid", "someone@example.com", "someone@example.org"):
        findings = hygiene.content_violations(
            write(tmp_path, monkeypatch, "notes.md", f"contact {address}\n")
        )
        assert findings == [], address


def test_a_vendor_token_and_an_assigned_secret_are_reported(tmp_path, monkeypatch):
    findings = hygiene.content_violations(
        write(tmp_path, monkeypatch, "deploy.md", f"token: {GITHUB_TOKEN}\n")
    )
    assert findings == ["deploy.md:1: GitHub token"]

    findings = hygiene.content_violations(
        write(tmp_path, monkeypatch, "deploy.md", f"SECRET_KEY = '{'a' * 48}'\n")
    )
    assert findings == ["deploy.md:1: assigned secret"]


@pytest.mark.parametrize(
    "marker",
    ["synthetic", "test-only", "replace-with", "not-for-real"],
)
def test_an_assigned_placeholder_secret_is_not_reported(tmp_path, monkeypatch, marker):
    value = f"{marker}-{'b' * 40}"
    findings = hygiene.content_violations(
        write(tmp_path, monkeypatch, "settings.py", f"SECRET_KEY = '{value}'\n")
    )
    assert findings == []


def test_the_repository_test_settings_build_a_synthetic_secret(tmp_path, monkeypatch):
    source = (ROOT / "growbook" / "settings" / "test.py").read_text(encoding="utf-8")
    assert SYNTHETIC_SECRET in source

    findings = hygiene.content_violations(
        write(tmp_path, monkeypatch, "settings.py", f'SECRET_KEY = "{SYNTHETIC_SECRET}" * 2\n')
    )
    assert findings == []


def test_png_and_jpeg_image_metadata_are_reported(tmp_path, monkeypatch):
    findings = hygiene.content_violations(
        write(tmp_path, monkeypatch, "screenshot.png", png_with_metadata(b"tEXt"))
    )
    assert findings == ["screenshot.png: PNG tEXt metadata chunk"]

    findings = hygiene.content_violations(
        write(tmp_path, monkeypatch, "photo.jpg", b"\xff\xd8\xff\xe1Exif\x00\x00rest")
    )
    assert findings == ["photo.jpg: JPEG Exif metadata"]


def test_a_clean_png_is_not_reported(tmp_path, monkeypatch):
    findings = hygiene.content_violations(
        write(tmp_path, monkeypatch, "screenshot.png", png_with_metadata(b"IDAT"))
    )
    assert findings == []


def host_path(*parts):
    """Join a host path at runtime so this module holds no literal one."""
    return "/" + "/".join(parts)


def test_an_absolute_host_path_is_reported(tmp_path, monkeypatch):
    for path in (
        host_path("root", ".ssh", "id_ed25519"),
        host_path("srv", "growbook-data"),
        host_path("mnt", "private"),
        host_path("home", "anna", "x"),
    ):
        findings = hygiene.content_violations(
            write(tmp_path, monkeypatch, "notes.md", f"stored at {path}\n")
        )
        assert findings == ["notes.md:1: absolute host path"], path


def test_the_container_developer_home_is_not_reported(tmp_path, monkeypatch):
    findings = hygiene.content_violations(
        write(tmp_path, monkeypatch, "notes.md", "copied from /home/developer/app\n")
    )
    assert findings == []


def test_the_allow_marker_exempts_one_deliberate_line(tmp_path, monkeypatch):
    findings = hygiene.content_violations(
        write(
            tmp_path,
            monkeypatch,
            "docs.md",
            f"Report privately to {PRIVATE_ADDRESS}  <!-- {hygiene.ALLOW_MARKER} -->\n"
            f"then write to {PRIVATE_ADDRESS}\n",
        )
    )
    assert findings == ["docs.md:2: email address"]


def test_only_the_checksum_pinned_vendored_files_skip_the_content_scan(tmp_path, monkeypatch):
    for name in (
        "static/vendor/swagger-ui-5.36.0/swagger-ui-bundle.js",
        "static/vendor/swagger-ui-5.36.0/swagger-ui.css",
    ):
        findings = hygiene.content_violations(
            write(tmp_path, monkeypatch, name, f"maintainer at {PRIVATE_ADDRESS}\n")
        )
        assert findings == [], name

    for name in (
        "static/vendor/swagger-ui-5.36.0/swagger-ui-bundle.js.map",
        "third_party/htmx/notes.md",
    ):
        findings = hygiene.content_violations(
            write(tmp_path, monkeypatch, name, f"contact {PRIVATE_ADDRESS}\n")
        )
        assert findings == [f"{name}:1: email address"], name


def test_a_large_png_still_reports_its_metadata(tmp_path, monkeypatch):
    name = "screenshot.png"
    oversized = png_with_metadata(b"tEXt") + b"\0" * hygiene.MAX_SCAN_BYTES
    write(tmp_path, monkeypatch, name, oversized)

    assert hygiene.content_violations(hygiene.working_tree_sources([name])) == [
        "screenshot.png: PNG tEXt metadata chunk"
    ]


def test_a_large_jpeg_is_scanned_in_blocks(tmp_path, monkeypatch):
    name = "photo.jpg"
    oversized = b"\xff\xd8\xff" + b"\0" * hygiene.MAX_SCAN_BYTES + b"\xff\xe1Exif\0\0" + b"rest"
    write(tmp_path, monkeypatch, name, oversized)

    def refuse(self):
        raise AssertionError("the file must be streamed, not read whole")

    monkeypatch.setattr(Path, "read_bytes", refuse)

    assert hygiene.content_violations(hygiene.working_tree_sources([name])) == [
        "photo.jpg: JPEG Exif metadata"
    ]


def test_only_the_pinned_notice_bundle_skips_the_content_scan(tmp_path, monkeypatch):
    pinned = write(
        tmp_path,
        monkeypatch,
        "third_party/swagger-ui/swagger-ui-bundle.js.LICENSE.txt",
        f"author <{PRIVATE_ADDRESS}>\n",
    )
    assert hygiene.content_violations(pinned) == []

    others = write(
        tmp_path, monkeypatch, "vendor/bundle.js.LICENSE.txt", f"author <{PRIVATE_ADDRESS}>\n"
    )
    assert hygiene.content_violations(others) == ["vendor/bundle.js.LICENSE.txt:1: email address"]


def test_a_force_added_pdf_is_refused_by_name(tmp_path):
    invoice = tmp_path / "invoices" / "2026-05.pdf"
    invoice.parent.mkdir()
    invoice.write_bytes(b"%PDF-1.7\n")

    assert hygiene.find_violations(["invoices/2026-05.pdf"]) == ["invoices/2026-05.pdf"]


def test_the_untracked_mode_lists_unignored_files_and_reuses_the_name_rules(tmp_path, monkeypatch):
    if not shutil.which("git"):
        pytest.skip("The untracked check needs Git.")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=30)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gitignore").write_text("local/\n", encoding="utf-8")
    (tmp_path / "local").mkdir()
    (tmp_path / "local" / "state.sqlite3-wal").write_text("", encoding="utf-8")
    (tmp_path / "notes.md").write_text("synthetic note\n", encoding="utf-8")
    (tmp_path / "export.sqlite3").write_text("", encoding="utf-8")

    assert hygiene.tracked_files() == []
    untracked = hygiene.untracked_files()

    # The ignored local directory stays out; the unignored database file is reported.
    assert sorted(untracked) == [".gitignore", "export.sqlite3", "notes.md"]
    assert hygiene.find_violations(untracked) == ["export.sqlite3"]


def test_the_tracked_files_of_this_repository_are_clean(monkeypatch):
    if not shutil.which("git"):
        pytest.skip("The hygiene check needs Git.")
    monkeypatch.chdir(ROOT)
    files = hygiene.tracked_files()
    reader = hygiene.IndexReader()
    try:
        sources = hygiene.index_sources(files, reader)
        findings = hygiene.content_violations(sources)
    finally:
        reader.close()

    assert hygiene.find_violations(files) == []
    assert findings == []


def test_the_check_fails_clearly_outside_a_git_work_tree(tmp_path, monkeypatch, capsys):
    if not shutil.which("git"):
        pytest.skip("The hygiene check needs Git.")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))

    assert hygiene.main([]) == 2
    output = capsys.readouterr().out
    assert "git work tree" in output
    assert "Traceback" not in output


def test_a_staged_file_deleted_from_the_working_tree_is_still_scanned(tmp_path, monkeypatch):
    if not shutil.which("git"):
        pytest.skip("The hygiene check needs Git.")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=30)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.md").write_text(f"contact {PRIVATE_ADDRESS}\n", encoding="utf-8")
    subprocess.run(["git", "add", "notes.md"], check=True, timeout=30)
    (tmp_path / "notes.md").unlink()

    reader = hygiene.IndexReader()
    try:
        findings = hygiene.content_violations(hygiene.index_sources(["notes.md"], reader))
    finally:
        reader.close()

    assert findings == ["notes.md:1: email address"]


def test_a_symlink_is_reported_and_never_read(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "private.md").write_text(f"contact {PRIVATE_ADDRESS}\n", encoding="utf-8")
    (tmp_path / "link.md").symlink_to(tmp_path / "private.md")

    findings = hygiene.content_violations(hygiene.working_tree_sources(["link.md"]))

    assert findings == ["link.md: symbolic link not inspected"]


def test_a_fine_grained_github_token_is_reported(tmp_path, monkeypatch):
    token = "github" + "_pat_" + "1" * 22 + "_" + "a" * 59
    findings = hygiene.content_violations(
        write(tmp_path, monkeypatch, "deploy.md", f"token: {token}\n")
    )

    assert findings == ["deploy.md:1: GitHub token"]


def test_exemptions_match_exact_paths_only(tmp_path, monkeypatch):
    nested = write(
        tmp_path,
        monkeypatch,
        "docs/static/vendor/swagger-ui-5.36.0/swagger-ui.css",
        f"maintainer at {PRIVATE_ADDRESS}\n",
    )

    assert hygiene.content_violations(nested) == [
        "docs/static/vendor/swagger-ui-5.36.0/swagger-ui.css:1: email address"
    ]


def test_a_symlink_at_an_exempt_path_is_still_reported(tmp_path, monkeypatch, capsys):
    if not shutil.which("git"):
        pytest.skip("The hygiene check needs Git.")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=30)
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "private.md"
    target.write_text(f"contact {PRIVATE_ADDRESS}\n", encoding="utf-8")
    pinned = tmp_path / "static" / "vendor" / "swagger-ui-5.36.0"
    pinned.mkdir(parents=True)
    (pinned / "swagger-ui.css").symlink_to(target)
    subprocess.run(["git", "add", "static"], check=True, timeout=30)

    assert hygiene.main([]) == 1
    output = capsys.readouterr().out
    assert "swagger-ui.css: symbolic link not inspected" in output
    assert PRIVATE_ADDRESS not in output


def test_a_working_tree_change_is_scanned_alongside_the_index(tmp_path, monkeypatch, capsys):
    if not shutil.which("git"):
        pytest.skip("The hygiene check needs Git.")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=30)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.md").write_text("synthetic note\n", encoding="utf-8")
    subprocess.run(["git", "add", "notes.md"], check=True, timeout=30)
    (tmp_path / "notes.md").write_text(f"contact {PRIVATE_ADDRESS}\n", encoding="utf-8")

    assert hygiene.main([]) == 1
    assert "notes.md:1: email address" in capsys.readouterr().out


def test_a_line_above_the_scan_limit_is_reported_as_unscannable(tmp_path, monkeypatch):
    oversized = hygiene.MAX_SCAN_BYTES + hygiene.SCAN_BLOCK_BYTES + 1
    content = "a" * oversized + f" {PRIVATE_ADDRESS}\n"

    findings = hygiene.content_violations(write(tmp_path, monkeypatch, "notes.md", content))

    assert findings == [f"notes.md:1: line longer than {hygiene.MAX_SCAN_BYTES} bytes"]


def test_content_after_a_long_line_is_still_scanned(tmp_path, monkeypatch):
    oversized = hygiene.MAX_SCAN_BYTES + hygiene.SCAN_BLOCK_BYTES + 1
    content = "a" * oversized + f"\ncontact {PRIVATE_ADDRESS}\n"

    findings = hygiene.content_violations(write(tmp_path, monkeypatch, "notes.md", content))

    assert findings == [
        f"notes.md:1: line longer than {hygiene.MAX_SCAN_BYTES} bytes",
        "notes.md:2: email address",
    ]


def test_a_long_line_below_the_limit_is_fully_scanned(tmp_path, monkeypatch):
    content = "a" * (4 * hygiene.SCAN_BLOCK_BYTES) + f" contact {PRIVATE_ADDRESS}\n"

    findings = hygiene.content_violations(write(tmp_path, monkeypatch, "notes.md", content))

    assert findings == ["notes.md:1: email address"]


def test_an_unreadable_object_fails_the_check_instead_of_passing(monkeypatch):
    if not shutil.which("git"):
        pytest.skip("The hygiene check needs Git.")
    monkeypatch.chdir(ROOT)
    reader = hygiene.IndexReader()
    try:
        with pytest.raises(hygiene.GitWorkTreeError):
            reader.open("0" * 40)
    finally:
        reader.close()


def test_an_assigned_secret_spanning_a_window_is_reported(tmp_path, monkeypatch):
    prefix = "p" * (hygiene.PATTERN_WINDOW_CHARS + 200)
    line = f"{prefix} token = '{'s' * 200}'\n"

    findings = hygiene.content_violations(write(tmp_path, monkeypatch, "settings.md", line))

    assert findings == ["settings.md:1: assigned secret"]


def test_a_long_line_with_one_stray_at_sign_scans_quickly(tmp_path, monkeypatch):
    line = "a" * (hygiene.MAX_SCAN_BYTES - 100) + " @ stray\n"

    started = time.monotonic()
    findings = hygiene.content_violations(write(tmp_path, monkeypatch, "notes.md", line))
    elapsed = time.monotonic() - started

    assert findings == []
    assert elapsed < 5, elapsed


def test_a_dangling_symlink_at_an_exempt_path_is_reported(tmp_path, monkeypatch, capsys):
    if not shutil.which("git"):
        pytest.skip("The hygiene check needs Git.")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=30)
    monkeypatch.chdir(tmp_path)
    pinned = tmp_path / "static" / "vendor" / "swagger-ui-5.36.0" / "swagger-ui.css"
    pinned.parent.mkdir(parents=True)
    pinned.write_text("body { color: green; }\n", encoding="utf-8")
    subprocess.run(["git", "add", "static"], check=True, timeout=30)
    pinned.unlink()
    pinned.symlink_to(tmp_path / "missing-target.css")

    assert hygiene.main([]) == 1
    assert "swagger-ui.css: symbolic link not inspected" in capsys.readouterr().out


def test_a_renamed_file_is_scanned_after_it_is_edited(tmp_path, monkeypatch, capsys):
    if not shutil.which("git"):
        pytest.skip("The hygiene check needs Git.")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=30)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.md").write_text("synthetic note\n", encoding="utf-8")
    subprocess.run(["git", "add", "notes.md"], check=True, timeout=30)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit",
            "-m",
            "synthetic baseline",
        ],
        check=True,
        timeout=30,
    )
    subprocess.run(["git", "mv", "notes.md", "renamed.md"], check=True, timeout=30)
    (tmp_path / "renamed.md").write_text(f"contact {PRIVATE_ADDRESS}\n", encoding="utf-8")

    assert hygiene.main([]) == 1
    assert "renamed.md:1: email address" in capsys.readouterr().out
