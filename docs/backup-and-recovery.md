# Operational backup and recovery

This is the canonical procedure for the backup, verification, restore and cleanup
commands. The [main README](../README.md#backup-and-recovery) links here; keep
operational instructions on this page rather than maintaining duplicate guides.

This is an **operational backup** for disaster recovery, not a portable export of
garden data. It contains accounts and password hashes, sessions, garden records,
private originals and operator-selected recovery configuration: treat it as
sensitive. There is no portable import/export feature.

## Create and verify

Run against the intended instance and settings; production configuration belongs in
an untracked environment file or a private operations repository.

```sh
uv run python manage.py backup_garden local/backups/example-001 --config local/operator.env
uv run python manage.py verify_backup local/backups/example-001
```

The example paths are generic. Supply the actual configuration needed for recovery
using repeated `--config` arguments. For a disposable local instance configured by
the [local setup](../README.md#run-locally), the generated development secret can
be used instead of the example operator configuration:

```sh
uv run python manage.py backup_garden local/backups/example-001 --config local/development/.development-secret
```

That includes only the local secret; supply any additional configuration required
by the instance. For container operations, explicitly mount private backup and
configuration paths: `compose.yaml` mounts neither a backup destination nor its
`.env` file by default. Use the intended production settings for those operations,
not the disposable development defaults.

The backup never guesses or exports all environment variables. Configuration
files must stay stable during the operation and must be protected independently
of source control.

The procedure:

1. Obtain the process-shared media-operation lock. All supported media writers and
   orphan cleanup use the same lock on host-local storage.
2. Create a SQLite **backup-API snapshot**; do not copy a live database/WAL file.
   Validate database integrity and foreign-key references. Account/session writes
   may continue: they are consistently represented by the database snapshot.
3. Read photo references from that snapshot; copy every immutable original and
   verify its stored checksum. File copying does not hold a SQLite write transaction.
4. Copy the supplied private configuration. Write a versioned manifest containing
   paths, sizes and SHA-256 checksums; derivatives are explicitly regenerated.
5. Verify the whole package and rename the temporary directory to its completed
   destination. Existing destinations are never intentionally overwritten.

The backup destination must be outside live media storage, and a restore must not
write inside a completed backup or the running instance's media directory. These
boundaries keep cleanup from treating a misplaced backup as an orphan.

Failed or incomplete operations are not restorable. Do not run schema migrations
concurrently with a backup. Any new media writer must use the same coordination
lock.

Keep at least one independent copy outside live storage. Retention, access
controls and encryption are operator responsibilities; the application does not
choose a backup service or automate retention. Windows development requires private
directory ACLs because POSIX `chmod` modes do not establish ACLs.

## Restore into a clean instance

```sh
uv run python manage.py restore_garden local/backups/example-001 local/restored-example
```

The destination must **not exist**. Restore never merges with or replaces the live
instance. It verifies the manifest, files and database, restores `db.sqlite3` and
originals, regenerates WebP display/thumbnail files, checks decoded dimensions, and
publishes the completed directory only after success.

Recovery configuration is copied to `recovered-configuration/`, not automatically
applied. Review it deliberately: use a fresh installation with the same application
version and compatible image settings, point its data directory at the restored
path, and supply its private secret/host/proxy configuration. Do not start both
instances against the same live directory.

For each deployment, verify that:

- Existing accounts can authenticate, and deactivated accounts stay inactive.
- Dates, notes, authorship and save receipts are preserved.
- Crop/variety relationships, area nesting, nullable measurements and archive flags
  are preserved, including active children of archived records.
- Referenced originals for diary entries, areas and varieties match checksums;
  regenerated derivatives display and photo captions/order remain intact.
- Anonymous clients cannot read entries, registers, media or original downloads.
- New validated saves work with the restored database's WAL configuration.

Verify recovery before storing real observations, and repeat these checks whenever
the schema or media handling changes.

## Orphan cleanup

```sh
uv run python manage.py cleanup_media
uv run python manage.py cleanup_media --apply
```

Without `--apply`, the command lists what it would remove. It considers only
unreferenced photo bundles and abandoned staging directories, defaults to a 24-hour
minimum age, and processes at most 1,000 candidates. It waits for in-flight media
operations and never removes a currently referenced bundle. It never scans or
deletes independent backup storage. A crash after file publication but before the
database commit may leave an orphan; a successful save must not leave a committed
photo row pointing at a file that has not yet been published.

Creation retries compare the original immutable payload fingerprint and author
against the stored receipt, not mutable record fields. Register create/edit saves
also retain receipts so an uncertain retry cannot append photos twice. A reused
key with different content conflicts; a missing receipt target fails instead of
recreating a deleted record. The application has no diary-entry edit/delete
operation or UI.

Allowed register/photo deletion commits database changes before removing live file
bundles, using the same media-operation lock as backups. A failed file cleanup
leaves an unreferenced bundle for `cleanup_media`; it does not invalidate an
independent completed backup.
