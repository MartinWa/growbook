"""Fail when private data is tracked by git, by name or by content.

A line that legitimately needs to look like private data can opt out of the content scan by
carrying the marker ``hygiene: allow``; no tracked line needs it today.

Tracked files are scanned by default, and their content is read from the git index, so the
check inspects what a commit would contain rather than the working tree. ``--untracked``
additionally scans the files that git reports as untracked and not ignored, reading them from
disk, which catches private data before it is added to the index. The check needs the
repository's git work tree and stops with a clear message when it runs outside one.

The two vendored files that ``tests/test_assets.py`` hashes by checksum are skipped by the
content scan, and so are generated ``*.LICENSE.txt`` notice bundles, whose upstream authors'
addresses must stay unedited. Text and PNG/JPEG metadata are inspected in a streamed read, so
a large file is never loaded whole; WebP and HEIC files are not inspected at all, so metadata
in those formats, which is this project's own asset format, is not detected here.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from collections.abc import Callable, Iterable
from contextlib import closing
from dataclasses import dataclass
from functools import partial
from pathlib import Path, PurePosixPath
from typing import BinaryIO

BAD_SUFFIXES = (
    ".7z",
    ".bak",
    ".backup",
    ".csv",
    ".db",
    ".dump",
    ".env",
    ".gz",
    ".pdf",
    ".sql",
    ".sqlite",
    ".sqlite3",
    ".tar",
    ".tgz",
    ".zip",
)
BAD_DIRECTORIES = frozenset({"planning", "reference"})
# The checksum-pinned vendored files and nothing else in that tree.
SKIP_CONTENT_PATHS = (
    "static/vendor/swagger-ui-5.36.0/swagger-ui-bundle.js",
    "static/vendor/swagger-ui-5.36.0/swagger-ui.css",
)
# Generated license bundles quote upstream notices verbatim.
SKIP_CONTENT_SUFFIXES = (".license.txt",)
SYMLINK_MODE = "120000"
# One block of streamed input, and the longest line inspected across blocks.
SCAN_BLOCK_BYTES = 64 * 1024
MAX_SCAN_BYTES = 2 * 1024 * 1024
ALLOW_MARKER = "hygiene: allow"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_METADATA_CHUNKS = frozenset({b"tEXt", b"iTXt", b"zTXt", b"eXIf"})
JPEG_EXIF_MARKER = b"Exif\x00\x00"
ASSIGNED_SECRET = re.compile(
    r"(?i)\b(secret[_-]?key|api[_-]?key|token|password)\b"
    r"\s*[:=]\s*[\"']([A-Za-z0-9_\-/+]{32,})[\"']"
)
# A deliberately fake value names itself as fake instead of being exempted by file.
PLACEHOLDER_SECRET_MARKERS = ("synthetic", "test-only", "replace-with", "not-for-real")

CONTENT_PATTERNS = (
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub token", re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("assigned secret", ASSIGNED_SECRET),
    (
        "email address",
        re.compile(
            r"[A-Za-z0-9._%+-]+@(?!example\.(?:com|org|net|invalid)\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
        ),
    ),
    (
        "absolute host path",
        re.compile(
            r"(?:[A-Za-z]:\\Users\\|/Users/[A-Za-z]|/home/(?!developer\b)[A-Za-z]|/(?:root|srv|mnt)/)"
        ),
    ),
)
UNTRACKED_HELP = "also scan files that are untracked but not ignored by .gitignore"


class GitWorkTreeError(RuntimeError):
    """The check cannot read the repository's git work tree."""


@dataclass(frozen=True)
class Source:
    """One file to inspect; a source without ``open`` is reported, never read."""

    path: str
    open: Callable[[], BinaryIO] | None = None
    note: str = ""


class BlobReader:
    """A bounded reader over one object that ``git cat-file --batch`` streams."""

    def __init__(self, stream: BinaryIO, size: int) -> None:
        self.stream = stream
        self.remaining = size
        self.finished = False

    def read(self, size: int = -1) -> bytes:
        if self.finished:
            return b""
        wanted = self.remaining if size is None or size < 0 else min(size, self.remaining)
        blocks = []
        while wanted > 0:
            block = self.stream.read(wanted)
            if not block:
                break
            blocks.append(block)
            wanted -= len(block)
            self.remaining -= len(block)
        if self.remaining == 0:
            self.finished = True
            self.stream.read(1)
        return b"".join(blocks)

    def close(self) -> None:
        """Consume nothing: the shared git process owns the stream."""


class IndexReader:
    """Stream the staged content of tracked files, one blob at a time."""

    def __init__(self) -> None:
        self.process = subprocess.Popen(
            ["git", "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def open(self, object_id: str) -> BinaryIO:
        self.process.stdin.write(object_id.encode() + b"\n")
        self.process.stdin.flush()
        header = self.process.stdout.readline().split()
        if len(header) != 3 or header[1] != b"blob":
            raise GitWorkTreeError(f"cannot read git object {object_id}")
        return BlobReader(self.process.stdout, int(header[2]))

    def close(self) -> None:
        self.process.stdin.close()
        self.process.wait(timeout=60)


def is_placeholder_secret(match: re.Match[str]) -> bool:
    """Return whether an assigned value names itself as a synthetic placeholder."""
    value = match.group(2).lower()
    return any(marker in value for marker in PLACEHOLDER_SECRET_MARKERS)


# Only these labels report a match conditionally; every other label reports on any match.
PLACEHOLDER_CHECKS = {"assigned secret": is_placeholder_secret}


def is_violation(path: str) -> bool:
    candidate = PurePosixPath(path)
    if any(part in BAD_DIRECTORIES for part in candidate.parts):
        return True
    name = candidate.name.lower()
    if name.startswith(".env") and name != ".env.example":
        return True
    if ".db-" in name or ".sqlite-" in name or ".sqlite3-" in name:
        return True
    return name.endswith(BAD_SUFFIXES)


def find_violations(paths: Iterable[str]) -> list[str]:
    return sorted(path for path in paths if is_violation(path))


def read_exactly(stream: BinaryIO, size: int) -> bytes:
    """Read ``size`` bytes unless the stream ends first."""
    blocks = []
    while size > 0:
        block = stream.read(size)
        if not block:
            break
        blocks.append(block)
        size -= len(block)
    return b"".join(blocks)


def discard(stream: BinaryIO, size: int) -> None:
    """Skip ``size`` bytes without buffering them."""
    remaining = size
    while remaining > 0:
        block = stream.read(min(remaining, SCAN_BLOCK_BYTES))
        if not block:
            return
        remaining -= len(block)


def png_metadata_chunks(stream: BinaryIO) -> list[str]:
    """Walk the PNG chunk headers, reading metadata payloads and skipping the rest."""
    if read_exactly(stream, len(PNG_SIGNATURE)) != PNG_SIGNATURE:
        return []
    chunks = []
    while header := read_exactly(stream, 8):
        if len(header) < 8:
            break
        length = int.from_bytes(header[:4], "big")
        chunk_type = header[4:8]
        if chunk_type in PNG_METADATA_CHUNKS:
            chunks.append(chunk_type.decode("latin-1"))
        discard(stream, length + 4)
        if chunk_type == b"IEND":
            break
    return chunks


def jpeg_has_exif(stream: BinaryIO) -> bool:
    """Scan the file in blocks for the Exif marker instead of reading it whole."""
    overlap = b""
    while block := stream.read(SCAN_BLOCK_BYTES):
        data = overlap + block
        if JPEG_EXIF_MARKER in data:
            return True
        overlap = data[-len(JPEG_EXIF_MARKER) :]
    return False


def scan_line(path: str, number: int, line: str) -> list[str]:
    if ALLOW_MARKER in line:
        return []
    findings = []
    for label, pattern in CONTENT_PATTERNS:
        match = pattern.search(line)
        if match and not PLACEHOLDER_CHECKS.get(label, lambda _match: False)(match):
            findings.append(f"{path}:{number}: {label}")
    return findings


def scan_text(path: str, stream: BinaryIO) -> list[str]:
    """Inspect a streamed text file line by line, holding at most one block plus one line."""
    findings = []
    number = 0
    remainder = ""
    block = stream.read(SCAN_BLOCK_BYTES)
    if b"\0" in block:
        # Binary content is out of scope for the text rules; only PNG/JPEG are inspected.
        return []
    while block:
        lines = (remainder + block.decode("utf-8", errors="ignore")).split("\n")
        remainder = lines.pop()
        for line in lines:
            number += 1
            findings.extend(scan_line(path, number, line))
        remainder = remainder[:MAX_SCAN_BYTES]
        block = stream.read(SCAN_BLOCK_BYTES)
    if remainder:
        findings.extend(scan_line(path, number + 1, remainder))
    return findings


def scan_source(path: str, stream: BinaryIO) -> list[str]:
    """Inspect one open stream with the rule that fits its file type."""
    name = PurePosixPath(path).name.lower()
    if name.endswith(".png"):
        return [f"{path}: PNG {chunk} metadata chunk" for chunk in png_metadata_chunks(stream)]
    if name.endswith((".jpg", ".jpeg")):
        return [f"{path}: JPEG Exif metadata"] if jpeg_has_exif(stream) else []
    return scan_text(path, stream)


def content_violations(sources: Iterable[Source]) -> list[str]:
    findings = []
    for source in sources:
        if source.path.endswith(SKIP_CONTENT_PATHS):
            continue
        if source.path.lower().endswith(SKIP_CONTENT_SUFFIXES):
            continue
        if source.open is None:
            findings.append(f"{source.path}: {source.note}")
            continue
        try:
            with closing(source.open()) as stream:
                findings.extend(scan_source(source.path, stream))
                # A streamed git object must be consumed before the next one is read.
                while stream.read(SCAN_BLOCK_BYTES):
                    pass
        except (OSError, GitWorkTreeError) as error:
            findings.append(f"{source.path}: could not be inspected ({error})")
    return findings


def git_files(*arguments: str) -> list[str]:
    """Return the NUL-separated entries reported by a git listing command."""
    try:
        result = subprocess.run(["git", *arguments], capture_output=True, check=False)
    except OSError as error:
        raise GitWorkTreeError(f"cannot run git: {error}") from error
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise GitWorkTreeError(message[-1] if message else "git reported no reason")
    return [entry.decode() for entry in result.stdout.split(b"\0") if entry]


def tracked_files() -> list[str]:
    return git_files("ls-files", "-z")


def untracked_files() -> list[str]:
    return git_files("ls-files", "-z", "--others", "--exclude-standard")


def candidate_files(*, untracked: bool = False) -> list[str]:
    """Return the files to scan, refusing to guess outside a git work tree."""
    files = tracked_files()
    if untracked:
        files += untracked_files()
    return files


def tracked_entries() -> dict[str, tuple[str, str]]:
    """Map each tracked path to its index mode and blob id."""
    entries = {}
    for entry in git_files("ls-files", "-s", "-z"):
        metadata, _tab, path = entry.partition("\t")
        mode, object_id, _stage = metadata.split()
        entries[path] = (mode, object_id)
    return entries


def index_sources(paths: Iterable[str], reader: IndexReader) -> list[Source]:
    """Read tracked content from the index, so a staged deletion cannot hide it."""
    entries = tracked_entries()
    sources = []
    for path in paths:
        mode, object_id = entries.get(path, ("", ""))
        if mode == SYMLINK_MODE:
            sources.append(Source(path, note="symbolic link not followed"))
        elif not object_id:
            sources.append(Source(path, note="not in the index"))
        else:
            sources.append(Source(path, partial(reader.open, object_id)))
    return sources


def working_tree_sources(paths: Iterable[str]) -> list[Source]:
    """Read untracked content from disk, refusing to follow a symbolic link."""
    sources = []
    for path in paths:
        candidate = Path(path)
        if candidate.is_symlink():
            sources.append(Source(path, note="symbolic link not followed"))
        else:
            sources.append(Source(path, partial(candidate.open, "rb")))
    return sources


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--untracked", action="store_true", help=UNTRACKED_HELP)
    options = parser.parse_args(argv)
    reader = IndexReader()
    try:
        files = candidate_files(untracked=options.untracked)
        tracked = tracked_files()
        sources = index_sources(tracked, reader)
        if options.untracked:
            sources += working_tree_sources(untracked_files())
        violations = find_violations(files)
        findings = content_violations(sources)
    except (GitWorkTreeError, OSError) as error:
        print(f"Hygiene check needs the repository's git work tree: {error}")
        return 2
    finally:
        reader.close()
    scope = "Tracked or untracked" if options.untracked else "Tracked"
    if violations:
        print(f"{scope} private-data files found:")
        for path in violations:
            print(f"  {path}")
    if findings:
        print(f"Private data found in {scope.lower()} file contents:")
        for finding in findings:
            print(f"  {finding}")
    if violations or findings:
        return 1
    if options.untracked:
        print("Hygiene check passed: no private-data files or content are tracked or untracked.")
    else:
        print("Hygiene check passed: no private-data files or content are tracked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
