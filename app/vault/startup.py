"""Getting a vault from "chosen" to "open", without risking it on the way.

Startup used to check the vault's layout, migrate the layout, and leave the
database to :class:`~app.database.connection.DatabaseManager`, which migrated it
somewhere in the middle of building the service registry. Three things were
wrong with that, and all three are why this module exists:

- **A database from a newer build was opened anyway.** ``check_vault`` reads
  the layout stamp in ``vault.json``, and ``VAULT_VERSION`` has not moved since
  it was introduced; ``SCHEMA_VERSION`` has moved six times. A build behind the
  database found nothing to migrate and ran against it.
- **Nothing was copied before a migration.** Two of those six drop columns. A
  migration that went wrong did so on the only copy of the user's library.
- **A failed migration took the window down.** It raised out of the registry,
  and on Windows — a windowed executable with no stderr — it did so silently.

So, in order, each before anything can hold the database open:

1. :func:`check_vault` (the layout) and :func:`check_database` (the schema)
   refuse a vault this build cannot understand. Both only read.
2. :func:`prepare_vault` copies the database and the ``.data`` documents aside
   if anything is about to be migrated, then migrates. A copy that cannot be
   made means nothing is migrated.
3. Any refusal or failure comes back as a sentence for the vault picker, which
   ``main`` already shows — the same screen a missing vault gets.

The copies live in ``<vault>/.data/upgrade-snapshots/``, three at most. They are
not backups: they sit on the same disk as the library, and they hold the
database and settings, not the audio. They exist for the one thing a backup is
too coarse for — getting back the library database exactly as it was the moment
before a migration touched it. Settings → Library → Backup is still the real
safety net, and the updater still says so before an update.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.backup.identity import utc_now, write_json_atomic
from app.backup.sqlite_copy import snapshot_database
from app.database import schema
from app.database.connection import migrate_database
from app.database.schema import DatabaseTooNewError, read_schema_version
from app.utils.library_paths import covers_db_path, data_root, tracks_db_path
from app.utils.logfile import current_log_file
from app.vault.vault_version import check_vault, effective_version, migrate_vault
from app.versions import APP_VERSION, VAULT_VERSION

logger = logging.getLogger(__name__)

# Under <vault>/.data/. Outside every root app.backup.layout walks, so a backup
# never carries the snapshots of the library it is backing up — see the test in
# tests/test_vault_startup.py that holds it there.
SNAPSHOT_ROOT = "upgrade-snapshots"

# Three: the upgrade just taken, and the two before it. Enough to step back
# through a pair of quick releases; few enough that a large library is not
# quietly carrying a dozen copies of its database.
SNAPSHOTS_KEPT = 3

# covers.db is left out. It is a derived thumbnail cache that no migration has
# ever touched (app/artwork/cover_store.py creates its one table with
# IF NOT EXISTS), and it can run to hundreds of megabytes — three of those for
# nothing. Flip this if a migration ever does reach it.
INCLUDE_COVERS = False

SNAPSHOT_MANIFEST = "snapshot.json"
SNAPSHOT_README = "RESTORE.txt"
SNAPSHOT_DATA_DIR = "data"
SNAPSHOT_FORMAT = 1

# A `.part` snapshot younger than this may belong to another copy of Kroma
# starting at the same moment, so pruning leaves it alone.
STALE_PART_SECONDS = 3600


@dataclass(frozen=True)
class Upgrade:
    """What opening a vault with this build would migrate.

    ``schema_from`` is ``None`` when the vault has no database yet — a new
    vault, which ``create_all`` builds at the current version with nothing to
    migrate and nothing worth copying.
    """

    schema_from: int | None
    vault_from: int
    schema_to: int
    vault_to: int

    @property
    def schema_pending(self) -> bool:
        return self.schema_from is not None and self.schema_from < self.schema_to

    @property
    def vault_pending(self) -> bool:
        return self.vault_from < self.vault_to

    @property
    def pending(self) -> bool:
        """Whether anything would be migrated at all."""
        return self.schema_pending or self.vault_pending


# ---------------------------------------------------------------------------
# Refusing what this build cannot open
# ---------------------------------------------------------------------------


def _too_new_message(vault: Path, found: int, supported: int) -> str:
    """The picker's sentence for a database a newer build migrated.

    Worded like ``check_vault``'s message for a newer layout, because to the
    user they are the same situation: a newer Kroma Player has used this vault.
    """
    return (
        f"“{vault.name}” was last used by a newer version of Kroma Player "
        f"(library database {found}; this build understands up to {supported}). "
        "Opening it with this build could damage it. Update Kroma Player, or "
        "choose a different vault."
    )


def check_database(vault: Path) -> str:
    """Return ``""`` when this build can open *vault*'s database, or why not.

    Only reads, and never creates the file: a vault refused here is left exactly
    as it was found.
    """
    found = read_schema_version(tracks_db_path(vault))
    if found is None or found <= schema.SCHEMA_VERSION:
        return ""
    return _too_new_message(vault, found, schema.SCHEMA_VERSION)


def pending_upgrade(vault: Path) -> Upgrade:
    """What :func:`prepare_vault` would migrate in *vault*."""
    return Upgrade(
        schema_from=read_schema_version(tracks_db_path(vault)),
        vault_from=effective_version(vault),
        schema_to=schema.SCHEMA_VERSION,
        vault_to=VAULT_VERSION,
    )


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def snapshot_root(vault: Path) -> Path:
    """Where *vault*'s upgrade snapshots are kept."""
    return data_root(vault) / SNAPSHOT_ROOT


def _snapshot_name(upgrade: Upgrade) -> str:
    """A name that sorts by age and says what it was taken before.

    UTC, so the order survives a timezone change, and the versions in it so a
    user looking at the folder can tell which snapshot is which without opening
    anything.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parts = [stamp]
    if upgrade.schema_pending:
        parts.append(f"schema-{upgrade.schema_from}-to-{upgrade.schema_to}")
    if upgrade.vault_pending:
        parts.append(f"layout-{upgrade.vault_from}-to-{upgrade.vault_to}")
    return "_".join(parts)


def _restore_text(vault: Path, upgrade: Upgrade) -> str:
    return (
        "Kroma Player upgrade snapshot\n"
        "=============================\n\n"
        f"Taken by Kroma Player {APP_VERSION} on {utc_now()}, just before it\n"
        f"upgraded the vault at:\n\n    {vault}\n\n"
        "The files in data/ are that vault's .data folder as it was at that\n"
        "moment: the library database and the settings beside it. Your music\n"
        "is not in here and was never touched by the upgrade.\n\n"
        "To put the library back the way it was:\n\n"
        "  1. Quit Kroma Player.\n"
        "  2. Copy every file in data/ into the vault's .data folder,\n"
        "     replacing the files that are there.\n"
        "  3. If .data holds tracks.db-wal or tracks.db-shm, delete them.\n"
        "  4. Open the vault with the version of Kroma Player you were using\n"
        "     before the upgrade. Opening it with the newer version upgrades\n"
        "     it again, which is also safe.\n\n"
        "snapshot.json records exactly what was copied and which versions the\n"
        "upgrade went from and to.\n"
    )


def snapshot_before_upgrade(vault: Path, upgrade: Upgrade) -> Path:
    """Copy *vault*'s database and ``.data`` documents aside. Returns the folder.

    Written into ``<name>.part/`` and renamed into place only once complete, so
    an interrupted snapshot is never mistaken for a good one. Raises ``OSError``
    or ``sqlite3.Error`` on failure, and the caller migrates nothing.
    """
    root = snapshot_root(vault)
    root.mkdir(parents=True, exist_ok=True)

    name = _snapshot_name(upgrade)
    final = root / name
    counter = 2
    while final.exists() or final.with_name(final.name + ".part").exists():
        final = root / f"{name}-{counter}"
        counter += 1
    part = final.with_name(final.name + ".part")
    data = part / SNAPSHOT_DATA_DIR
    data.mkdir(parents=True)

    copied: list[str] = []
    try:
        databases = [tracks_db_path(vault)]
        if INCLUDE_COVERS:
            databases.append(covers_db_path(vault))
        for database in databases:
            if database.is_file():
                # Through SQLite rather than as a file: the database runs in WAL
                # mode, and a file copy misses whatever is still in the -wal.
                snapshot_database(database, data / database.name)
                copied.append(database.name)

        for document in sorted(data_root(vault).glob("*.json")):
            if document.is_file():
                shutil.copy2(document, data / document.name)
                copied.append(document.name)

        write_json_atomic(
            part / SNAPSHOT_MANIFEST,
            {
                "format": SNAPSHOT_FORMAT,
                "created_at": utc_now(),
                "created_by": APP_VERSION,
                "vault": str(vault),
                "schema": {"from": upgrade.schema_from, "to": upgrade.schema_to},
                "vault_layout": {"from": upgrade.vault_from, "to": upgrade.vault_to},
                "files": copied,
                "covers_included": INCLUDE_COVERS,
                # Set once the upgrade it guards has finished. Until then this
                # snapshot is the one a retry must reuse — see
                # existing_snapshot_for.
                "completed_at": None,
            },
        )
        (part / SNAPSHOT_README).write_text(_restore_text(vault, upgrade), encoding="utf-8")
        os.replace(part, final)
    except BaseException:
        shutil.rmtree(part, ignore_errors=True)
        raise

    logger.info("Snapshot of %s taken before upgrading: %s", vault, final)
    return final


def _read_manifest(folder: Path) -> dict:
    try:
        document = json.loads((folder / SNAPSHOT_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


def _snapshots(vault: Path) -> list[Path]:
    """Every finished snapshot of *vault*, oldest first."""
    root = snapshot_root(vault)
    try:
        folders = [
            child for child in root.iterdir()
            if child.is_dir() and not child.name.endswith(".part")
            and (child / SNAPSHOT_MANIFEST).is_file()
        ]
    except OSError:
        return []
    return sorted(folders, key=lambda folder: folder.name)


def existing_snapshot_for(vault: Path, upgrade: Upgrade) -> Path | None:
    """The snapshot an unfinished attempt at *upgrade* already took, if any.

    A migration that failed partway leaves the database somewhere between where
    it started and where it was going. Snapshotting it again would copy that
    half-migrated state — and, three retries later, prune the one clean copy
    from before the first attempt. So a retry reuses the snapshot whose upgrade
    never completed, provided it was heading to the same versions and started
    no later than where the vault is now.
    """
    for folder in reversed(_snapshots(vault)):
        manifest = _read_manifest(folder)
        if manifest.get("completed_at"):
            continue
        schema_part = manifest.get("schema") or {}
        layout_part = manifest.get("vault_layout") or {}
        if schema_part.get("to") != upgrade.schema_to:
            continue
        if layout_part.get("to") != upgrade.vault_to:
            continue
        schema_from = schema_part.get("from")
        if upgrade.schema_from is not None and isinstance(schema_from, int):
            if schema_from > upgrade.schema_from:
                continue
        layout_from = layout_part.get("from")
        if isinstance(layout_from, int) and layout_from > upgrade.vault_from:
            continue
        return folder
    return None


def _mark_completed(folder: Path) -> None:
    """Record that the upgrade *folder* guarded has finished."""
    manifest = _read_manifest(folder)
    if not manifest:
        return
    manifest["completed_at"] = utc_now()
    try:
        write_json_atomic(folder / SNAPSHOT_MANIFEST, manifest)
    except OSError as exc:
        logger.debug("Could not mark %s complete: %s", folder, exc)


def prune_snapshots(vault: Path, keep: int = SNAPSHOTS_KEPT) -> int:
    """Delete all but the newest *keep* snapshots. Returns how many went.

    Also clears ``.part`` folders an interrupted snapshot left behind, once they
    are old enough that nothing can still be writing them.
    """
    removed = 0
    finished = _snapshots(vault)
    for folder in finished[: max(0, len(finished) - keep)]:
        shutil.rmtree(folder, ignore_errors=True)
        removed += 1

    try:
        leftovers = [c for c in snapshot_root(vault).iterdir() if c.name.endswith(".part")]
    except OSError:
        leftovers = []
    now = time.time()
    for leftover in leftovers:
        try:
            if now - leftover.stat().st_mtime < STALE_PART_SECONDS:
                continue
        except OSError:
            continue
        shutil.rmtree(leftover, ignore_errors=True)
    return removed


# ---------------------------------------------------------------------------
# Opening
# ---------------------------------------------------------------------------


def _where_the_log_is() -> str:
    log = current_log_file()
    return f" The details are in {log}." if log is not None else ""


def prepare_vault(vault: Path) -> str:
    """Snapshot if needed, migrate, and return ``""`` — or why it did not work.

    Runs only after :func:`check_vault` and :func:`check_database` have passed,
    so everything pending is a migration this build carries.
    """
    upgrade = pending_upgrade(vault)
    snapshot: Path | None = None

    if upgrade.pending:
        snapshot = existing_snapshot_for(vault, upgrade)
        if snapshot is not None:
            logger.info("Retrying an unfinished upgrade; keeping the snapshot at %s", snapshot)
        else:
            try:
                snapshot = snapshot_before_upgrade(vault, upgrade)
            except Exception as exc:  # noqa: BLE001 — refused, not crashed
                logger.exception("Could not snapshot %s before upgrading it", vault)
                return (
                    f"Kroma Player needs to upgrade “{vault.name}”, but could not "
                    f"make a safety copy of it first ({exc}). Nothing was changed. "
                    "Check there is free space and that the folder can be written "
                    "to, then open it again." + _where_the_log_is()
                )
            prune_snapshots(vault, SNAPSHOTS_KEPT)

    try:
        migrate_vault(vault)
        migrate_database(tracks_db_path(vault))
    except DatabaseTooNewError as exc:
        return _too_new_message(vault, exc.found, exc.supported)
    except Exception as exc:  # noqa: BLE001 — the picker explains; the log keeps the rest
        logger.exception("Upgrading the vault at %s failed", vault)
        if snapshot is not None:
            return (
                f"Upgrading “{vault.name}” for Kroma Player {APP_VERSION} failed "
                f"({exc}). A copy of the library from just before the upgrade is "
                f"in {snapshot}. Opening the vault again retries the upgrade, "
                "which is safe." + _where_the_log_is()
            )
        return (
            f"Kroma Player could not open “{vault.name}” ({exc})."
            + _where_the_log_is()
        )

    if snapshot is not None:
        _mark_completed(snapshot)
    return ""


def open_problem(vault: Path) -> str:
    """Everything between choosing *vault* and opening it. ``""`` means open it.

    In order, and each only if the one before passed: a layout this build
    cannot read, a database this build cannot read, then the snapshot and the
    migrations. Whatever comes back non-empty is shown in the vault picker.
    """
    return check_vault(vault) or check_database(vault) or prepare_vault(vault)
