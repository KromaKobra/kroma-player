"""Executing a plan the user has already seen and confirmed.

Two rules shape the whole file.

**Nothing half-written is ever visible.** Every file is built at a ``.part``
path and moved into place with :func:`os.replace`, the same discipline
:mod:`app.exporter.export` states for exports. A cancel, a crash or a drive
pulled out mid-run leaves the backup holding only finished files.

**The databases are written last.** This is the ordering that matters and it is
worth being explicit about why. Writing ``tracks.db`` first and then copying
audio means an interruption leaves a backup whose database confidently
describes tracks whose files were never written — broken in the silent way,
and nothing later notices or repairs it. Copying audio first and the database
last means an interruption leaves audio the database has not caught up with,
which is harmless: the files are inert, and the next sync writes the database
that names them. The same hazard, in the other direction, is documented at
``app/importer/stash.py:33``.

Deletions sit between the two, and only happen when the user asked for them on
this particular run. They are the one irreversible thing here.
"""
from __future__ import annotations

import logging
import os
import socket
from pathlib import Path
from typing import Callable, Optional

from app.backup import layout
from app.backup.hashing import copy_and_hash
from app.backup.identity import utc_now
from app.backup.locator import has_generated_name, lock_path, suggested_backup_name
from app.backup.manifest import STATE_COMPLETE, STATE_SYNCING, Manifest
from app.backup.plan import (
    Action,
    BackupCancelled,
    BackupError,
    BackupPlan,
    BackupResult,
    FileAction,
)
from app.backup.sqlite_copy import snapshot_database
from app.utils.library_paths import covers_db_path, tracks_db_path

logger = logging.getLogger(__name__)

# A run that has failed this many times in a row has hit something systemic — a
# drive unplugged, a disk full — rather than a bad file. Without the cap, one of
# those grinds through the whole library emitting the same error per track. The
# same reasoning as CONSECUTIVE_FAILURE_LIMIT in app.exporter.export.
CONSECUTIVE_FAILURE_LIMIT = 3

StageFn = Optional[Callable[[str], None]]
ProgressFn = Optional[Callable[[int, int], None]]
CancelFn = Optional[Callable[[], bool]]


def apply_plan(
    plan: BackupPlan,
    *,
    app_version: str = "",
    on_stage: StageFn = None,
    on_progress: ProgressFn = None,
    should_cancel: CancelFn = None,
) -> BackupResult:
    """Carry out *plan*, and return what actually happened.

    Raises :class:`BackupError` if the run could not proceed or could not
    continue, and :class:`BackupCancelled` if the user stopped it. A cancelled
    run leaves the backup consistent — every file already copied is complete —
    but incomplete, and its manifest says so.
    """
    if plan.is_blocked:
        raise BackupError(
            "This folder is a backup of a different library. Nothing was "
            "written."
        )

    # This is where a new backup's folder comes into being — the write phase,
    # after the user has confirmed. The scan deliberately leaves it absent so
    # that looking at a plan and then thinking better of it costs nothing.
    destination = plan.destination
    if destination.exists() and not destination.is_dir():
        raise BackupError(f"That is not a folder: {destination}")
    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupError(
            f"Could not create {destination} — {exc.strerror or exc}"
        ) from exc

    manifest = Manifest.load(destination)
    manifest.source_vault_id = plan.source_vault_id
    manifest.app_version = app_version
    manifest.options = {
        "tracks": plan.options.tracks,
        "backing": plan.options.backing,
        "guis": plan.options.guis,
        "models": plan.options.models,
    }

    warnings: list[str] = []
    copies = [a for a in plan.actions if a.action is not Action.EXTRA]
    removals = plan.extras if plan.options.delete_extras else ()
    total = len(copies) + len(removals) + 2   # +2 for the two databases

    with _run_lock(destination):
        # Marked before anything is written, so a run that dies here is
        # detectable rather than looking like one that simply finished.
        manifest.state = STATE_SYNCING
        _save(manifest, destination)

        _sweep_partials(destination)

        done = 0
        result_written = 0
        result_bytes = 0
        failures = 0

        try:
            for action in copies:
                _check(should_cancel)
                _stage(on_stage, _describe(action))
                try:
                    size = _copy_one(plan.source, destination, action, manifest)
                except OSError as exc:
                    failures += 1
                    message = f"{action.relpath}: {exc.strerror or exc}"
                    logger.warning("Backup could not write %s", message)
                    warnings.append(message)
                    if failures >= CONSECUTIVE_FAILURE_LIMIT:
                        raise BackupError(
                            f"Stopped after {failures} failures in a row — the "
                            f"last was {message}. The drive may be full or "
                            "disconnected."
                        ) from exc
                else:
                    failures = 0
                    if size is not None:
                        result_written += 1
                        result_bytes += size
                done += 1
                _progress(on_progress, done, total)

            # After every copy succeeded, never before: a run that dies partway
            # must not already have thrown away the only other copy of anything.
            deleted = 0
            for action in removals:
                _check(should_cancel)
                _stage(on_stage, f"Removing {action.relpath}")
                if _remove_one(destination, action, manifest):
                    deleted += 1
                done += 1
                _progress(on_progress, done, total)
        except (BackupCancelled, BackupError):
            # Keep what was learned. Every file copied before the stop is
            # complete and correctly recorded, so the next run skips it instead
            # of hashing the whole library again to rediscover the same thing.
            # The state stays "syncing", which is exactly what happened.
            _finish(manifest, destination, STATE_SYNCING)
            raise

        # ---- the databases, last ------------------------------------
        _check(should_cancel)
        _stage(on_stage, "Copying the library database")
        try:
            result_bytes += snapshot_database(
                tracks_db_path(), destination / layout.DATA_DIR / "tracks.db"
            )
        except Exception as exc:  # noqa: BLE001 — reported, not swallowed
            _finish(manifest, destination, STATE_SYNCING)
            raise BackupError(
                f"Could not copy the library database — {exc}. The audio files "
                "were written, so running this again will finish the job."
            ) from exc
        done += 1
        _progress(on_progress, done, total)

        _stage(on_stage, "Copying the artwork cache")
        try:
            result_bytes += snapshot_database(
                covers_db_path(), destination / layout.DATA_DIR / "covers.db"
            )
        except Exception as exc:  # noqa: BLE001
            # A derived cache, by its own docstring: "can be deleted and rebuilt
            # from the artwork embedded in the Opus files at any time". Losing
            # it costs a rebuild, not a library, so it must not fail a backup
            # whose tracks and database are already safely written.
            logger.warning("Could not snapshot covers.db: %s", exc)
            warnings.append(
                "The artwork cache could not be copied. Everything else was "
                "backed up; covers rebuild themselves from the tracks."
            )
        done += 1
        _progress(on_progress, done, total)

        for relpath in layout.data_payload(plan.source):
            try:
                _copy_plain(plan.source / relpath, destination / relpath)
            except OSError as exc:
                logger.warning("Could not copy %s: %s", relpath, exc)
                warnings.append(f"{relpath}: {exc.strerror or exc}")

        _finish(manifest, destination, STATE_COMPLETE)

    # Outside the lock, because the lock file lives in the folder being renamed —
    # and only now, because a folder should never claim a date it did not reach.
    destination = _restamp(destination, warnings)

    logger.info(
        "Backup finished: %d written, %d removed, %d bytes to %s",
        result_written, deleted, result_bytes, destination,
    )
    return BackupResult(
        written=result_written,
        deleted=deleted,
        bytes_written=result_bytes,
        destination=destination,
        warnings=tuple(warnings),
    )


# ======================================================================
# One file at a time
# ======================================================================


def _copy_one(
    source_root: Path, destination_root: Path, action: FileAction, manifest: Manifest,
) -> Optional[int]:
    """Copy one file into the backup and record it.

    Returns the bytes written, or ``None`` when there was nothing to copy
    because the file has left the vault since the plan was made.
    """
    source = source_root / action.relpath
    final = destination_root / action.relpath
    final.parent.mkdir(parents=True, exist_ok=True)
    temp = final.with_name(final.name + ".part")

    try:
        written, digest = copy_and_hash(source, temp)

        # The plan may be a few seconds old — a download could have finished
        # since the user read it. What was actually written is what gets
        # recorded, so the manifest always describes the bytes on the drive
        # rather than the bytes that were predicted.
        src_stat = source.stat()
        if written != src_stat.st_size:
            raise OSError(
                f"expected {src_stat.st_size} bytes, wrote {written}"
            )

        os.replace(temp, final)
        manifest.record(
            action.relpath, written, src_stat.st_mtime, final.stat().st_mtime, digest,
        )
        return written
    except FileNotFoundError:
        # The track was deleted between the scan and now. Not a failure — the
        # library no longer has it, so the backup not having it is correct.
        logger.info("Skipping %s — it is no longer in the vault", action.relpath)
        manifest.forget(action.relpath)
        return None
    finally:
        try:
            temp.unlink()
        except OSError:
            pass


def _copy_plain(source: Path, destination: Path) -> None:
    """Copy a small ``.data`` file atomically, without recording a digest."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".part")
    try:
        copy_and_hash(source, temp)
        os.replace(temp, destination)
    finally:
        try:
            temp.unlink()
        except OSError:
            pass


def _remove_one(destination_root: Path, action: FileAction, manifest: Manifest) -> bool:
    """Delete one file the vault no longer has. Returns whether it went."""
    try:
        (destination_root / action.relpath).unlink()
    except FileNotFoundError:
        manifest.forget(action.relpath)
        return False
    except OSError as exc:
        logger.warning("Could not remove %s from the backup: %s", action.relpath, exc)
        return False
    manifest.forget(action.relpath)
    return True


def _restamp(destination: Path, warnings: list[str]) -> Path:
    """Move the folder's name on to today's date. Returns where it ended up.

    Only folders this app named get restamped — ``venombackup_`` plus a date and
    nothing else. A name someone chose (``venombackup_offsite``) is theirs, and
    survives every sync; it stays findable either way, because any suffix is
    accepted when looking for a backup.

    Never fatal. The backup is already written and correct at this point, so a
    name that could not be changed is a note, not a failure. In particular the
    target being taken is left alone rather than resolved with a numeric suffix,
    which would only breed ``venombackup_20260823_2`` folders.
    """
    if not has_generated_name(destination):
        return destination

    target = destination.with_name(suggested_backup_name())
    if target == destination:
        return destination

    if target.exists():
        warnings.append(
            f"The backup kept the name {destination.name} — {target.name} is "
            "already taken."
        )
        return destination

    try:
        destination.rename(target)
    except OSError as exc:
        logger.warning("Could not rename %s to %s: %s", destination, target, exc)
        warnings.append(
            f"The backup kept the name {destination.name} — it could not be "
            f"renamed ({exc.strerror or exc})."
        )
        return destination

    logger.info("Backup folder restamped: %s -> %s", destination.name, target.name)
    return target


def _sweep_partials(destination: Path) -> None:
    """Remove ``.part`` files left behind by an interrupted run.

    They are always garbage: a ``.part`` only exists between the start of a copy
    and the ``os.replace`` that finishes it, so one still on disk means that
    copy never completed.
    """
    removed = 0
    for directory, _, names in os.walk(destination, followlinks=False):
        for name in names:
            if not name.endswith(".part"):
                continue
            try:
                (Path(directory) / name).unlink()
                removed += 1
            except OSError:
                pass
    if removed:
        logger.info("Cleared %d unfinished file(s) from a previous run", removed)


# ======================================================================
# Bookkeeping
# ======================================================================


class _run_lock:
    """Hold the backup for the duration of a run.

    Not a defence against a determined race — it is a plain file, and two
    processes could still interleave between the check and the write. It is a
    defence against the realistic case: a second window of the same app, or a
    user starting a sync while an earlier one is still grinding through a slow
    external drive.
    """

    def __init__(self, destination: Path) -> None:
        self._path = lock_path(destination)

    def __enter__(self) -> "_run_lock":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._path, "x", encoding="utf-8") as handle:
                handle.write(f"pid {os.getpid()} on {socket.gethostname()} "
                             f"at {utc_now()}")
        except FileExistsError as exc:
            raise BackupError(
                "Another backup to this folder is already running."
            ) from exc
        except OSError as exc:
            raise BackupError(
                f"Could not start a backup here — {exc.strerror or exc}"
            ) from exc
        return self

    def __exit__(self, *_exc) -> None:
        try:
            self._path.unlink()
        except OSError as exc:
            logger.warning("Could not release the backup lock: %s", exc)


def _finish(manifest: Manifest, destination: Path, state: str) -> None:
    manifest.state = state
    _save(manifest, destination)


def _save(manifest: Manifest, destination: Path) -> None:
    try:
        manifest.save(destination)
    except OSError as exc:
        raise BackupError(
            f"Could not write the backup index — {exc.strerror or exc}"
        ) from exc


def _describe(action: FileAction) -> str:
    verb = "Adding" if action.action is Action.ADD else "Updating"
    return f"{verb} {action.relpath}"


def _stage(on_stage: StageFn, text: str) -> None:
    if on_stage is not None:
        on_stage(text)


def _progress(on_progress: ProgressFn, done: int, total: int) -> None:
    if on_progress is not None:
        on_progress(done, total)


def _check(should_cancel: CancelFn) -> None:
    if should_cancel is not None and should_cancel():
        raise BackupCancelled()
