"""Consistent SQLite/original-photo operational snapshots, not portable exports."""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path, PurePosixPath

import django
from django.conf import settings
from django.db import connection
from django.utils import timezone

from .images import render_derivatives, sync_directory, sync_directory_tree
from .media import media_operation, private_path

FORMAT = "growbook-operational-backup"
VERSION = 1


class BackupError(Exception):
    pass


def safe_path(root, relative):
    value = PurePosixPath(str(relative))
    if not value.parts or value.is_absolute() or any(part in {"..", "."} for part in value.parts):
        raise BackupError("An archive path is invalid.")
    if "\\" in str(relative) or ":" in str(relative):
        raise BackupError("An archive path is invalid.")
    root = Path(root).resolve()
    result = root.joinpath(*value.parts)
    if not result.resolve().is_relative_to(root):
        raise BackupError("An archive path escaped its directory.")
    if any(parent.is_symlink() for parent in [result, *result.parents] if parent != root):
        raise BackupError("Archive symlinks are not supported.")
    return result


def file_digest(path):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def copy_private(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with Path(source).open("rb") as reader, destination.open("xb") as writer:
        shutil.copyfileobj(reader, writer, length=1024 * 1024)
        writer.flush()
        os.fsync(writer.fileno())
    destination.chmod(0o600)


def open_snapshot(database):
    return sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro&immutable=1", uri=True)


def snapshot_photos(snapshot):
    try:
        return list(
            snapshot.execute(
                "SELECT id, original, original_sha256, display, thumbnail, "
                "original_format, width, height FROM core_photo ORDER BY id"
            )
        )
    except sqlite3.Error as exc:
        raise BackupError("The snapshot does not contain the supported photo schema.") from exc


def check_database(snapshot):
    if snapshot.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise BackupError("SQLite integrity check failed.")
    if snapshot.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise BackupError("The snapshot contains broken foreign-key relationships.")


def add_manifest_file(files, temporary, relative, kind):
    checksum, size = file_digest(safe_path(temporary, relative))
    files.append({"path": relative, "kind": kind, "sha256": checksum, "bytes": size})
    return checksum


def publish_directory(temporary, destination):
    if destination.exists():
        raise BackupError("The completed destination appeared during the operation.")
    os.rename(temporary, destination)
    try:
        sync_directory(destination.parent)
    except OSError as exc:
        raise BackupError(
            "The completed directory was published, but its final storage flush failed. "
            "Retain and verify it before use; nothing was overwritten."
        ) from exc


def create_backup(destination, configuration_files):
    """Snapshot rows plus referenced originals while media mutation/cleanup is paused."""
    destination = Path(destination).resolve()
    if destination.is_relative_to(Path(settings.MEDIA_ROOT).resolve()):
        raise BackupError("Backups must be stored outside live media storage.")
    if destination.exists():
        raise BackupError("The backup destination already exists; nothing was overwritten.")
    if not configuration_files:
        raise BackupError("Supply the private configuration file(s) needed for recovery.")
    configuration_files = [Path(path).resolve() for path in configuration_files]
    if any(not path.is_file() for path in configuration_files):
        raise BackupError("A recovery configuration file does not exist.")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = Path(tempfile.mkdtemp(prefix=".backup-pending-", dir=destination.parent))
    try:
        files = []
        with media_operation():
            connection.ensure_connection()
            snapshot_path = temporary / "database.sqlite3"
            target = sqlite3.connect(snapshot_path)
            try:
                connection.connection.backup(target, pages=1000, sleep=0.01)
                target.execute("PRAGMA journal_mode=DELETE")
                check_database(target)
                photos = snapshot_photos(target)
            finally:
                target.close()
            snapshot_path.chmod(0o600)
            with snapshot_path.open("r+b") as stream:
                os.fsync(stream.fileno())
            add_manifest_file(files, temporary, "database.sqlite3", "database")
            copied = set()
            for _, original, expected, *_ in photos:
                relative = f"media/{original}"
                if relative in copied:
                    continue
                copy_private(private_path(original), safe_path(temporary, relative))
                actual = add_manifest_file(files, temporary, relative, "original")
                if actual != expected:
                    raise BackupError("An original photo failed its stored checksum.")
                copied.add(relative)
            for index, source in enumerate(configuration_files, start=1):
                relative = f"configuration/{index:03d}-{source.name}"
                copy_private(source, safe_path(temporary, relative))
                add_manifest_file(files, temporary, relative, "configuration")
        manifest = {
            "format": FORMAT,
            "version": VERSION,
            "created_at": timezone.now().isoformat(),
            "django_version": django.get_version(),
            "sqlite_version": sqlite3.sqlite_version,
            "derivatives": "regenerate",
            "files": files,
        }
        with (temporary / "manifest.json").open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        (temporary / "manifest.json").chmod(0o600)
        verify_backup(temporary)
        sync_directory_tree(temporary)
        publish_directory(temporary, destination)
        return manifest
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise BackupError("Backup creation failed; no complete backup was published.") from exc
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def verify_backup(backup):
    """Check every file, the database, and agreement between rows and the manifest."""
    backup = Path(backup).resolve()
    try:
        manifest_path = backup / "manifest.json"
        if manifest_path.is_symlink():
            raise BackupError("Archive symlinks are not supported.")
        if manifest_path.stat().st_size > 8 * 1024 * 1024:
            raise BackupError("The backup manifest is too large.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise BackupError("The backup manifest must be a JSON object.")
        if (
            manifest.get("format") != FORMAT
            or manifest.get("version") != VERSION
            or manifest.get("derivatives") != "regenerate"
        ):
            raise BackupError("This is not a supported growbook operational backup.")
        files = manifest.get("files")
        if not isinstance(files, list):
            raise BackupError("The manifest has no file list.")
        paths = set()
        originals = {}
        configuration_count = 0
        for item in files:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("path"), str)
                or not isinstance(item.get("sha256"), str)
                or not isinstance(item.get("kind"), str)
                or type(item.get("bytes")) is not int
                or item["bytes"] < 0
            ):
                raise BackupError("The manifest contains invalid file metadata.")
            relative = item["path"]
            if relative in paths or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]):
                raise BackupError("The manifest has duplicate paths or invalid checksums.")
            paths.add(relative)
            source = safe_path(backup, relative)
            checksum, size = file_digest(source)
            if checksum != item["sha256"] or size != item["bytes"]:
                raise BackupError("A backup file failed its checksum or size check.")
            if item["kind"] == "original" and relative.startswith("media/photos/"):
                originals[relative.removeprefix("media/")] = checksum
            elif item["kind"] == "configuration" and relative.startswith("configuration/"):
                configuration_count += 1
            elif item["kind"] != "database" or relative != "database.sqlite3":
                raise BackupError("The manifest contains an unexpected file kind/path.")
        if "database.sqlite3" not in paths or not configuration_count:
            raise BackupError("The backup lacks its database or recovery configuration.")
        actual_paths = {
            path.relative_to(backup).as_posix() for path in backup.rglob("*") if path.is_file()
        }
        if actual_paths != paths | {"manifest.json"}:
            raise BackupError("The archive includes unlisted or missing files.")
        snapshot = open_snapshot(backup / "database.sqlite3")
        try:
            check_database(snapshot)
            expected = {row[1]: row[2] for row in snapshot_photos(snapshot)}
            if expected != originals:
                raise BackupError("Photo rows and the backup manifest do not agree.")
        finally:
            snapshot.close()
        return manifest
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        raise BackupError("The operational backup could not be verified.") from exc


def restore_backup(backup, destination):
    """Restore into a new directory only; never merge with or overwrite live state."""
    backup = Path(backup).resolve()
    destination = Path(destination).resolve()
    if destination.is_relative_to(backup):
        raise BackupError("Restore outside the completed backup directory.")
    if destination.is_relative_to(Path(settings.MEDIA_ROOT).resolve()):
        raise BackupError("Restore outside the running instance's media storage.")
    if destination.exists():
        raise BackupError("Restore requires a new, nonexistent data directory.")
    manifest = verify_backup(backup)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = Path(tempfile.mkdtemp(prefix=".restore-pending-", dir=destination.parent))
    try:
        for item in manifest["files"]:
            source = safe_path(backup, item["path"])
            if item["kind"] == "database":
                relative = "db.sqlite3"
            elif item["kind"] == "configuration":
                relative = "recovered-configuration/" + item["path"].removeprefix("configuration/")
            else:
                relative = item["path"]
            copy_private(source, safe_path(temporary, relative))
            if file_digest(safe_path(temporary, relative)) != (item["sha256"], item["bytes"]):
                raise BackupError("A restored file failed verification.")
        snapshot = open_snapshot(temporary / "db.sqlite3")
        try:
            check_database(snapshot)
            for (
                _,
                original,
                _,
                display,
                thumbnail,
                expected_format,
                width,
                height,
            ) in snapshot_photos(snapshot):
                source = safe_path(temporary / "media", original)
                display_path = safe_path(temporary / "media", display)
                thumbnail_path = safe_path(temporary / "media", thumbnail)
                display_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                thumbnail_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                actual = render_derivatives(source, display_path, thumbnail_path)
                if actual != (expected_format, width, height):
                    raise BackupError(
                        "A restored original decoded differently from its photo record."
                    )
                display_path.chmod(0o600)
                thumbnail_path.chmod(0o600)
        finally:
            snapshot.close()
        sync_directory_tree(temporary)
        publish_directory(temporary, destination)
        return manifest
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise BackupError("Restore failed; no complete instance was published.") from exc
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
