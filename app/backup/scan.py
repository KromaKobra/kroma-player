"""Working out what a sync would do, without doing any of it.

This is the half of the feature that makes the other half safe. It reads both
sides, decides every file's fate, and hands back a frozen
:class:`~app.backup.plan.BackupPlan` — having written nothing at all. The user
sees that plan before anything is overwritten, and the plan they confirm is the
one that executes.

The cheap comparison, and why it is not hashing
----------------------------------------------

The obvious design is to hash a shard folder, and only hash the tracks inside
when the folder digests disagree. It does not work: there is no way to hash a
folder except by hashing every file in it, so the outer tier costs exactly what
the inner tier costs and saves nothing.

What is genuinely cheap is ``os.scandir``, which reports size and modification
time straight off the directory entry. Combined with the manifest — which
records what both sides looked like at the last sync — a file can usually be
dismissed without being opened:

===============================================  =========  =======
condition                                        result     hashes
===============================================  =========  =======
absent from the backup                           ADD        0
manifest agrees with both sides                  skip       0
sizes differ                                     UPDATE     0
sizes match, anything else disagrees             hash both  2
absent from the vault                            EXTRA      0
===============================================  =========  =======

So adding 50 tracks to a library of 1000 costs two directory walks and no
hashing whatsoever. Hashing happens only where the cheap signals genuinely
cannot settle the question — which is the case that matters, because a track
whose metadata was retagged keeps its size and gains a new mtime.
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional

from app.backup import layout
from app.backup.hashing import hash_file
from app.backup.identity import read_identity, vault_id
from app.backup.locator import (
    candidate_backup_folders,
    has_backup_name,
    is_backup,
    lock_path,
)
from app.backup.manifest import Manifest
from app.backup.plan import (
    Action,
    BackupCancelled,
    BackupError,
    BackupOptions,
    BackupPlan,
    FileAction,
    Identity,
)
from app.backup.sqlite_copy import SIDECAR_SUFFIXES
from app.utils.library_paths import covers_db_path, tracks_db_path

logger = logging.getLogger(__name__)

# Written and deleted to answer "can we actually create files here?", which on
# Windows no amount of inspecting permissions will tell you. The same trick
# app.exporter.export.preflight uses, and for the same reason.
_PROBE_NAME = ".kroma-backup-check"

# Headroom left free on the destination. A drive filled to the last byte by a
# backup is a drive that cannot finish writing its own index.
FREE_SPACE_MARGIN = 64 * 1024 * 1024

# Above this share of the existing backup being rewritten or removed, the dialog
# stops being informational and starts being a warning. This is the guard the
# vault id cannot provide: a backup that really is this library's, but years out
# of date, passes the identity check and still deserves a second look.
CHURN_WARNING = 0.5

StageFn = Optional[Callable[[str], None]]
ProgressFn = Optional[Callable[[int, int], None]]
CancelFn = Optional[Callable[[], bool]]


def build_plan(
    source: Path,
    destination: Path,
    options: BackupOptions,
    *,
    on_stage: StageFn = None,
    on_progress: ProgressFn = None,
    should_cancel: CancelFn = None,
) -> BackupPlan:
    """Compare *source* against *destination* and describe what would change.

    Raises :class:`BackupError` if the destination could not be used at all, and
    :class:`BackupCancelled` if the user stopped the scan. Writes nothing except
    the writability probe, which it removes.
    """
    source = Path(source)
    destination = Path(destination)

    preflight(source, destination)
    _stage(on_stage, "Reading the backup")

    manifest = Manifest.load(destination)
    identity, backup_id, backup_created = check_identity(source, destination, manifest)

    src_files = layout.walk(source, options)
    _check(should_cancel)
    dst_files = layout.walk(destination, options)
    _check(should_cancel)

    _stage(on_stage, "Comparing")
    actions, unchanged = _diff(
        source, destination, src_files, dst_files, manifest,
        on_progress=on_progress, should_cancel=should_cancel,
    )

    warnings: list[str] = []
    if manifest.interrupted:
        warnings.append(
            "The last backup to this folder did not finish. This run will "
            "complete it."
        )

    plan = BackupPlan(
        source=source,
        destination=destination,
        options=options,
        identity=identity,
        actions=tuple(actions),
        unchanged=unchanged,
        data_bytes=_data_bytes(source),
        backup_bytes_before=_tree_bytes(destination),
        free_bytes=_free_bytes(destination),
        source_vault_id=vault_id(source),
        backup_vault_id=backup_id,
        backup_created_at=backup_created,
        interrupted=manifest.interrupted,
        full_verify=not manifest.loaded and bool(dst_files),
        warnings=tuple(warnings),
    )

    # The capacity and churn warnings are derived from totals the plan computes,
    # so they can only be worked out once it exists.
    _append_capacity_warning(plan, warnings)
    return replace(plan, warnings=tuple(warnings))


def preflight(source: Path, destination: Path) -> None:
    """Raise :class:`BackupError` if this pairing could not possibly work.

    Everything checkable is checked before a single byte moves, so a
    misconfigured backup fails as a sentence rather than as a half-populated
    folder on somebody's external drive.
    """
    # ``Path("")`` collapses to ``Path(".")``, so an unset destination arrives
    # looking like the working directory — which exists, is writable, and is
    # nobody's idea of where their music library should be copied.
    if str(destination) in ("", "."):
        raise BackupError("Choose a folder to back up to")
    if not source.is_dir():
        raise BackupError(f"The vault is missing: {source}")

    # A backup that does not exist yet is the normal case for creating one: the
    # user picks somewhere to put it and we propose a folder inside. What has to
    # exist and be writable is then the *parent*, and the folder itself is made
    # by `apply_plan` once the plan has been confirmed — never here, because a
    # scan the user goes on to cancel must leave nothing behind.
    if destination.exists():
        if not destination.is_dir():
            raise BackupError(f"That is not a folder: {destination}")
        writable = destination
    else:
        writable = destination.parent
        if not writable.is_dir():
            raise BackupError(f"No such folder: {writable}")

    _check_separate(source, destination)

    probe = writable / _PROBE_NAME
    try:
        probe.write_bytes(b"")
    except OSError as exc:
        raise BackupError(
            f"Cannot write to {writable} — {exc.strerror or exc}"
        ) from exc
    finally:
        try:
            probe.unlink()
        except OSError:
            pass

    _check_lock(destination)


def _check_separate(source: Path, destination: Path) -> None:
    """Refuse a destination that overlaps the vault.

    Backing a vault up into itself never terminates: the copy keeps creating new
    files inside the tree it is still walking. The reverse — a vault sitting
    inside the chosen backup folder — is just as bad, because the run would
    happily overwrite the library it is reading from.

    Resolved first, so a symlink pointing back into the vault is caught too.
    """
    try:
        real_source = Path(os.path.realpath(source))
        real_dest = Path(os.path.realpath(destination))
    except OSError as exc:
        raise BackupError(f"Could not resolve those folders — {exc}") from exc

    if real_source == real_dest:
        raise BackupError("That is the vault itself — choose a different folder")
    if _contains(real_source, real_dest):
        raise BackupError(
            "That folder is inside the vault. A backup has to live somewhere "
            "else, ideally on another drive."
        )
    if _contains(real_dest, real_source):
        raise BackupError(
            "The vault is inside that folder. Choose somewhere that does not "
            "contain your library."
        )


def _contains(parent: Path, child: Path) -> bool:
    """True when *child* is at or below *parent*."""
    try:
        return os.path.commonpath([str(parent), str(child)]) == str(parent)
    except ValueError:
        # Different drives on Windows — which is the good case.
        return False


def _check_lock(destination: Path) -> None:
    """Refuse to start if something else is already writing this backup."""
    path = lock_path(destination)
    try:
        held = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return
    except OSError:
        return
    raise BackupError(
        "Another backup to this folder is already running, or one was "
        f"interrupted ({held}). If nothing is running, delete "
        f"{path} and try again."
    )


def check_identity(
    source: Path, destination: Path, manifest: Manifest,
) -> tuple[Identity, str, str]:
    """Decide whether *destination* is a backup of *source*.

    Returns the verdict plus the backup's recorded id and creation date, which
    the dialog shows beside the vault's own when they disagree.

    The three outcomes are handled very differently, and deliberately so:

    ``NEW``
        Not a backup yet. Everything gets written; nothing can be lost.
    ``MATCH``
        The ids agree. Normal.
    ``ABSENT``
        A backup with no recorded id — made by an older build, or a folder
        somebody assembled by hand. Allowed, because refusing would strand
        anyone who backed up before ids existed, but the dialog says the folder
        cannot vouch for itself and confirming adopts the id.
    ``MISMATCH``
        The ids disagree. This is the case the whole mechanism exists for, and
        it is refused unless the user explicitly overrides it.
    """
    if not is_backup(destination):
        return Identity.NEW, "", ""

    recorded = manifest.source_vault_id
    backup_created = manifest.created_at
    # Fall back to the identity file the backup carries, for a backup whose
    # manifest predates the id but which was copied from a vault that had one.
    if not recorded:
        recorded = read_identity(destination).get("vault_id", "")

    if not recorded:
        return Identity.ABSENT, "", backup_created

    mine = vault_id(source)
    if not mine:
        # The vault has no id and the backup does. Cannot be confirmed either
        # way, so it is treated as unverifiable rather than as a mismatch.
        return Identity.ABSENT, recorded, backup_created

    if recorded == mine:
        return Identity.MATCH, recorded, backup_created
    return Identity.MISMATCH, recorded, backup_created


def choose_backup_folder(picked: Path, source: Path) -> Path | None:
    """Which backup inside *picked* this vault should sync to, if any.

    Lives here rather than in :mod:`app.backup.locator` for two reasons: it has
    to read each candidate's manifest, and ``manifest`` already imports from
    ``locator`` so the reverse would be circular. It also belongs beside
    :func:`check_identity`, which answers the same question one folder at a time.

    The rules, in order:

    - **A folder the user picked directly is used, whoever it belongs to.** If
      they aimed at a backup of somebody else's library, that has to reach the
      review pane and show its mismatch panel. Quietly steering them somewhere
      else would hide the one thing worth seeing.
    - Otherwise, among the folders just inside: prefer one whose recorded vault
      id **is this vault's**, most recently synced first. A drive holding
      several backups syncs to this library's, not to whichever was touched last.
    - Then any candidate with no recorded id — a folder named like a backup that
      has never been synced, or one whose manifest was lost. There is nothing in
      those belonging to anyone else.
    - A folder carrying a **different** vault id is never chosen automatically.
      Returning ``None`` makes the caller propose a new backup beside it, which
      is the right answer: two libraries, two backups, one drive.
    """
    if is_backup(picked) or has_backup_name(picked):
        return picked

    mine = vault_id(source)
    unclaimed: list[Path] = []

    # candidate_backup_folders is already ordered most-recently-modified first,
    # so the first match in each pass is the most recent one.
    for candidate in candidate_backup_folders(picked):
        if candidate == picked:
            continue
        recorded = _recorded_vault_id(candidate)
        if recorded and mine and recorded == mine:
            return candidate
        if not recorded:
            unclaimed.append(candidate)

    return unclaimed[0] if unclaimed else None


def _recorded_vault_id(backup: Path) -> str:
    """The vault id a candidate backup claims, or ``""`` if it claims none."""
    recorded = Manifest.load(backup).source_vault_id
    if recorded:
        return recorded
    # A backup copied from a vault that had an id, by a build whose manifest did
    # not record one yet, still carries the vault's own identity file.
    return read_identity(backup).get("vault_id", "")


# ======================================================================
# The diff
# ======================================================================


def _diff(
    source: Path,
    destination: Path,
    src_files: dict[str, layout.Entry],
    dst_files: dict[str, layout.Entry],
    manifest: Manifest,
    *,
    on_progress: ProgressFn = None,
    should_cancel: CancelFn = None,
) -> tuple[list[FileAction], int]:
    """Classify every path on either side. Returns the actions and the skip count."""
    actions: list[FileAction] = []
    unchanged = 0

    total = len(src_files)
    for index, (relpath, src) in enumerate(sorted(src_files.items())):
        if index % 64 == 0:
            _check(should_cancel)
            _progress(on_progress, index, total)

        dst = dst_files.get(relpath)
        if dst is None:
            actions.append(FileAction(relpath, Action.ADD, src.size, src.mtime))
            continue

        if manifest.is_unchanged(relpath, src.size, src.mtime, dst.size, dst.mtime):
            unchanged += 1
            continue

        # Different sizes settle it without reading either file.
        if src.size != dst.size:
            actions.append(FileAction(relpath, Action.UPDATE, src.size, src.mtime))
            continue

        # Same size, and the cheap signals disagree. Now it is worth reading.
        if _same_contents(source / relpath, destination / relpath):
            unchanged += 1
        else:
            actions.append(FileAction(relpath, Action.UPDATE, src.size, src.mtime))

    _progress(on_progress, total, total)

    for relpath, dst in sorted(dst_files.items()):
        if relpath not in src_files:
            actions.append(FileAction(relpath, Action.EXTRA, dst.size, dst.mtime))

    return actions, unchanged


def _same_contents(source: Path, destination: Path) -> bool:
    """Whether two same-sized files hold the same bytes.

    An unreadable file counts as different, so the sync's answer is to rewrite
    it. That is the safe direction: a file we cannot read on the backup is a
    file worth replacing, and one we cannot read in the vault will fail loudly
    during the copy rather than being silently skipped here.
    """
    try:
        return hash_file(source) == hash_file(destination)
    except OSError as exc:
        logger.warning("Could not compare %s: %s", source, exc)
        return False


# ======================================================================
# Sizing
# ======================================================================


def _data_bytes(source: Path) -> int:
    """Bytes the fixed ``.data`` payload contributes: databases plus json."""
    total = 0
    for path in (tracks_db_path(), covers_db_path()):
        total += _size_of(path)
        # A WAL holding uncommitted pages makes the snapshot bigger than the
        # .db on its own suggests. Counted so the free-space check is not
        # optimistic about a database with a long-running write behind it.
        for suffix in SIDECAR_SUFFIXES:
            total += _size_of(path.with_name(path.name + suffix))
    for relpath in layout.data_payload(source):
        total += _size_of(source / relpath)
    return total


def _tree_bytes(root: Path) -> int:
    """Total size of everything under *root*, or 0 if it cannot be read."""
    total = 0
    for directory, _, names in os.walk(root, followlinks=False):
        for name in names:
            total += _size_of(Path(directory) / name)
    return total


def _size_of(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _free_bytes(destination: Path) -> int:
    """Free space on the drive *destination* is on, or would be on.

    Walks up to the nearest folder that actually exists. Without that, a backup
    being created for the first time asks about a path that is not there yet,
    ``disk_usage`` raises, and the answer becomes ``0`` — which is falsy, so the
    not-enough-room check silently does not happen. That is precisely the run
    where it matters: the first one onto an external drive.
    """
    for candidate in (destination, *destination.parents):
        try:
            return shutil.disk_usage(candidate).free
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning("Could not read free space on %s: %s", candidate, exc)
            return 0
    return 0


def _append_capacity_warning(plan: BackupPlan, warnings: list[str]) -> None:
    """Add the space and churn warnings, if either applies."""
    needed = plan.bytes_to_write + FREE_SPACE_MARGIN
    if plan.free_bytes and plan.free_bytes < needed:
        short = needed - plan.free_bytes
        warnings.append(
            f"Not enough room — this needs about {_mb(short)} MB more than the "
            "drive has free."
        )
    if plan.churn >= CHURN_WARNING and (plan.updated or plan.extras):
        warnings.append(
            f"This would rewrite or remove {round(plan.churn * 100)}% of what is "
            "already in this backup. That is normal for a backup that is badly "
            "out of date, and is what it looks like when the folder belongs to "
            "a different library. Check the numbers before confirming."
        )


def _mb(value: int) -> int:
    return max(1, round(value / (1024 * 1024)))


# ======================================================================
# Callback plumbing
# ======================================================================


def _stage(on_stage: StageFn, text: str) -> None:
    if on_stage is not None:
        on_stage(text)


def _progress(on_progress: ProgressFn, done: int, total: int) -> None:
    if on_progress is not None:
        on_progress(done, total)


def _check(should_cancel: CancelFn) -> None:
    if should_cancel is not None and should_cancel():
        raise BackupCancelled()
