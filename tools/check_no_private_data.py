"""Fail when private data is tracked by git, by name or by content.

A line that legitimately needs to look like private data can opt out of the content scan by
carrying the marker ``hygiene: allow``; no tracked line needs it today.

Tracked files are scanned by default. Their content is read from the git index by object id,
so the check inspects what a commit would contain, and a working-tree copy that differs from
the index is scanned as well. ``--untracked`` additionally scans the files that git reports as
untracked and not ignored, reading them from disk. A symbolic link is reported and never
opened, in either mode. The check needs the repository's git work tree and stops with a clear
message when it cannot read it.

Three paths are exempt from the content scan by exact name: the two vendored files that
``tests/test_assets.py`` hashes by checksum, and the generated notice bundle that quotes
upstream authors' addresses. Nothing else in those trees is exempt. Text and PNG/JPEG
metadata are inspected as a stream, so a large file is never loaded whole and a single line
above the scan limit is reported as unscannable. WebP and HEIC files are not inspected at all,
so metadata in those formats, which is this project's own asset format, is not detected here.
"""

from __future__ import annotations

import argparse
import os
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
# Exact paths: the checksum-pinned vendored files and a generated notice bundle.
SKIP_CONTENT_PATHS = frozenset(
    {
        "static/vendor/swagger-ui-5.36.0/swagger-ui-bundle.js",
        "static/vendor/swagger-ui-5.36.0/swagger-ui.css",
        "third_party/swagger-ui/swagger-ui-bundle.js.LICENSE.txt",
    }
)
SYMLINK_MODE = "120000"
# One block of streamed input, and the longest line inspected within a file.
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
    ("private key block", re.compile(r"-----BEGIN [A-Z ]{0,32}PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    (
        "GitHub token",
        re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})"),
    ),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,255}")),
    (
        "absolute host path",
        re.compile(
            r"(?:[A-Za-z]:\\Users\\|/Users/[A-Za-z]|/home/(?!developer\b)[A-Za-z]|/(?:root|srv|mnt)/)"
        ),
    ),
)
# The assigned value can be longer than a window, so this rule reads the whole line.
WHOLE_LINE_PATTERNS = (("assigned secret", ASSIGNED_SECRET),)
# RFC limits that bound the work done around each "@" instead of retrying the whole line.
EMAIL_LOCAL_PART = re.compile(r"[A-Za-z0-9._%+-]{1,64}\Z")
EMAIL_DOMAIN_PART = re.compile(r"[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}(?![A-Za-z0-9.-])")
RESERVED_EMAIL_DOMAINS = frozenset({"example.com", "example.org", "example.net", "example.invalid"})
# A long line is scanned in overlapping windows so the work stays linear in its length.
PATTERN_WINDOW_CHARS = 4096
PATTERN_OVERLAP_CHARS = 256
UNTRACKED_HELP = "also scan files that are untracked but not ignored by .gitignore"


class GitWorkTreeError(RuntimeError):
    """The check cannot read the repository's git work tree."""


@dataclass(frozen=True)
class IndexEntry:
    """One staged path: its index mode and the id of the object holding its content."""

    mode: str
    object_id: str


@dataclass(frozen=True)
class Source:
    """One file to inspect; a source without ``open`` is reported, never read."""

    path: str
    open: Callable[[], BinaryIO] | None = None
    note: str = ""


class BlobReader:
    """A bounded reader over one object that ``git cat-file --batch`` streams."""

    def __init__(self, stream: BinaryIO, size: int, object_id: str) -> None:
        self.stream = stream
        self.object_id = object_id
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
                raise GitWorkTreeError(f"short read for git object {self.object_id}")
            blocks.append(block)
            wanted -= len(block)
            self.remaining -= len(block)
        if self.remaining == 0:
            self.finished = True
            if self.stream.read(1) != b"\n":
                raise GitWorkTreeError(f"unterminated response for git object {self.object_id}")
        return b"".join(blocks)

    def close(self) -> None:
        """Consume nothing: the shared git process owns the stream."""


class IndexReader:
    """Stream staged blob content from one ``git cat-file --batch`` process."""

    def __init__(self) -> None:
        self.process = subprocess.Popen(
            ["git", "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def open(self, object_id: str) -> BinaryIO:
        """Request one object and read its header before its content is streamed."""
        self.process.stdin.write(object_id.encode() + b"\n")
        self.process.stdin.flush()
        header = self.process.stdout.readline().split()
        if len(header) != 3 or header[1] != b"blob":
            raise GitWorkTreeError(f"cannot read git object {object_id}")
        return BlobReader(self.process.stdout, int(header[2]), object_id)

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.stdin.close()
            status = self.process.wait(timeout=60)
        else:
            status = self.process.returncode
        if status != 0:
            raise GitWorkTreeError(f"git cat-file exited with status {status}")


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


def line_windows(line: str) -> Iterable[str]:
    """Yield the line in overlapping windows so a very long line is still scanned."""
    if len(line) <= PATTERN_WINDOW_CHARS:
        yield line
        return
    start = 0
    while start < len(line):
        yield line[start : start + PATTERN_WINDOW_CHARS + PATTERN_OVERLAP_CHARS]
        start += PATTERN_WINDOW_CHARS


def has_email_address(line: str) -> bool:
    """Check each "@" against its local part and domain instead of retrying the line."""
    start = 0
    while (at := line.find("@", start)) != -1:
        start = at + 1
        local = line[max(0, at - 64) : at]
        if not EMAIL_LOCAL_PART.search(local):
            continue
        domain = EMAIL_DOMAIN_PART.match(line, at + 1)
        if domain and domain.group(0).lower() not in RESERVED_EMAIL_DOMAINS:
            return True
    return False


def scan_line(path: str, number: int, line: str) -> list[str]:
    if ALLOW_MARKER in line:
        return []
    labels = []
    for window in line_windows(line):
        for label, pattern in CONTENT_PATTERNS:
            if pattern.search(window):
                labels.append(label)
    for label, pattern in WHOLE_LINE_PATTERNS:
        match = pattern.search(line)
        if match and not PLACEHOLDER_CHECKS.get(label, lambda _match: False)(match):
            labels.append(label)
    if has_email_address(line):
        labels.append("email address")
    findings = []
    for label in labels:
        finding = f"{path}:{number}: {label}"
        if finding not in findings:
            findings.append(finding)
    return findings


def scan_text(path: str, stream: BinaryIO) -> list[str]:
    """Inspect streamed text line by line, never holding a whole file or dropping a line."""
    findings = []
    number = 0
    remainder = ""
    skipping = False
    block = stream.read(SCAN_BLOCK_BYTES)
    if b"\0" in block:
        # Binary content is out of scope for the text rules; only PNG/JPEG are inspected.
        return findings
    while block:
        segments = (remainder + block.decode("utf-8", errors="ignore")).split("\n")
        remainder = ""
        tail = segments.pop()
        for segment in segments:
            if skipping:
                skipping = False
                continue
            number += 1
            findings.extend(scan_line(path, number, segment))
        if not skipping:
            if len(tail) > MAX_SCAN_BYTES:
                number += 1
                findings.append(f"{path}:{number}: line longer than {MAX_SCAN_BYTES} bytes")
                skipping = True
            else:
                remainder = tail
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
        if source.path in SKIP_CONTENT_PATHS:
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


def index_entries() -> dict[str, IndexEntry]:
    """Map each staged path to its index mode and object id."""
    entries = {}
    for entry in git_files("ls-files", "--stage", "-z"):
        metadata, _tab, path = entry.partition("\t")
        mode, object_id, _stage = metadata.split()
        entries[path] = IndexEntry(mode, object_id)
    return entries


def tracked_files() -> list[str]:
    return list(index_entries())


def untracked_files() -> list[str]:
    return git_files("ls-files", "--others", "--exclude-standard", "-z")


def modified_tracked_paths(entries: dict[str, IndexEntry]) -> list[str]:
    """Tracked paths whose working-tree content differs from the index."""
    records = git_files("status", "--porcelain", "-z")
    changed = []
    position = 0
    while position < len(records):
        record = records[position]
        position += 1
        status, path = record[:2], record[3:]
        if "R" in status or "C" in status:
            # A rename or copy carries its original path as the next NUL-separated field.
            position += 1
        if status == "??" or status[1] in {" ", "D"}:
            continue
        if path in entries and os.path.lexists(path):
            changed.append(path)
    return changed


def index_sources(paths: Iterable[str], reader: IndexReader) -> list[Source]:
    """Read the staged content of the given paths by object id."""
    entries = index_entries()
    sources = []
    for path in paths:
        entry = entries.get(path)
        if entry is None:
            sources.append(Source(path, note="not in the index"))
        elif entry.mode == SYMLINK_MODE:
            sources.append(Source(path, note="symbolic link not inspected"))
        else:
            sources.append(Source(path, partial(reader.open, entry.object_id)))
    return sources


def working_tree_sources(paths: Iterable[str]) -> list[Source]:
    """Read content from disk, refusing to follow a symbolic link."""
    sources = []
    for path in paths:
        candidate = Path(path)
        if candidate.is_symlink():
            sources.append(Source(path, note="symbolic link not inspected"))
        else:
            sources.append(Source(path, partial(candidate.open, "rb")))
    return sources


def planned_sources(
    entries: dict[str, IndexEntry], extra_paths: Iterable[str], reader: IndexReader
) -> tuple[list[Source], list[str]]:
    """Plan the scan: index content, changed working-tree copies and untracked files."""
    sources = []
    findings = []
    for path, entry in entries.items():
        if entry.mode == SYMLINK_MODE:
            findings.append(f"{path}: symbolic link not inspected")
            continue
        if path in SKIP_CONTENT_PATHS:
            continue
        sources.append(Source(path, partial(reader.open, entry.object_id)))
    for path in [*modified_tracked_paths(entries), *extra_paths]:
        # The entry type decides first: an exempt or dangling path can still be a link.
        source = working_tree_sources([path])[0]
        if source.open is None:
            findings.append(f"{path}: {source.note}")
        elif path not in SKIP_CONTENT_PATHS:
            sources.append(source)
    return sources, findings


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--untracked", action="store_true", help=UNTRACKED_HELP)
    options = parser.parse_args(argv)
    try:
        entries = index_entries()
        extra_paths = untracked_files() if options.untracked else []
        violations = find_violations([*entries, *extra_paths])
        with closing(IndexReader()) as reader:
            sources, findings = planned_sources(entries, extra_paths, reader)
            findings += content_violations(sources)
    except (GitWorkTreeError, OSError) as error:
        print(f"Hygiene check could not read the repository's git work tree: {error}")
        return 2
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
