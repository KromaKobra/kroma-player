"""Taking a consistent copy of a database the app currently has open.

``tracks.db`` and ``covers.db`` both run ``PRAGMA journal_mode=WAL``
(:mod:`app.database.connection` and :mod:`app.artwork.cover_store`). Under WAL,
committed transactions live in a ``-wal`` sidecar until something checkpoints
them back. So copying ``tracks.db`` as a file produces a database missing every
recent write, or — if a checkpoint happens to land mid-copy — a torn one. It
would look fine. It would open. It would be wrong, and nothing would say so.

``sqlite3.Connection.backup()`` is the answer, and it is in the standard
library. It copies page by page while holding the right locks, so the result is
a consistent snapshot even though the app is still playing music and writing
rows. Three details beyond calling it:

* The destination is switched to ``journal_mode=DELETE`` before it is closed, so
  the snapshot on the drive is one self-contained file rather than a database
  plus sidecars.
* Any stale ``-wal``/``-shm`` left beside the destination by an earlier run is
  removed. Left there, SQLite would treat the old WAL as belonging to the new
  database and recover it over the top — turning a good backup into a corrupt
  one at the moment it is opened.
* The write goes to a ``.part`` and is renamed into place, so an interrupted
  run leaves the previous snapshot intact rather than a half-copied file.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# Pages per step. The whole point of stepping is that the source stays usable
# while the copy runs; SQLite yields its read lock between batches.
PAGES_PER_STEP = 512

SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def snapshot_database(source: Path, destination: Path) -> int:
    """Copy the SQLite database at *source* to *destination*. Returns bytes written.

    Raises ``OSError`` or ``sqlite3.Error`` on failure. Callers decide how much
    that matters: a failed ``tracks.db`` fails the run, while ``covers.db`` is a
    derived cache and only earns a warning.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".part")
    _remove_quietly(temp)

    source_conn = _connect_source(source)
    try:
        target_conn = sqlite3.connect(str(temp))
        try:
            source_conn.backup(target_conn, pages=PAGES_PER_STEP)
            # Fold the copy's own journal away so what lands on the drive is a
            # single file. Without this the snapshot can arrive with sidecars
            # that the next run would then have to reason about.
            target_conn.execute("PRAGMA journal_mode=DELETE")
            target_conn.commit()
        finally:
            target_conn.close()
    finally:
        source_conn.close()

    _fsync(temp)
    os.replace(temp, destination)

    # Only now: a sidecar removed before the replace would belong to the
    # database still in place, which is the one thing worth not corrupting if
    # this run stops here.
    for suffix in SIDECAR_SUFFIXES:
        _remove_quietly(destination.with_name(destination.name + suffix))

    return destination.stat().st_size


def _connect_source(source: Path) -> sqlite3.Connection:
    """Open *source* for reading, preferring a connection that cannot write.

    Read-only is the right default — this must never be able to modify the
    library it is copying. But a read-only connection to a WAL database has to
    read the ``-shm`` shared-memory file and is not allowed to create one, so it
    fails when no other connection is holding the database open. That is the
    normal case in tests and for a vault the app is not currently running
    against, so fall back rather than making the safer spelling the fragile one.
    """
    try:
        return sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        logger.debug("Read-only open of %s failed (%s); opening normally", source, exc)
        return sqlite3.connect(str(source))


def _fsync(path: Path) -> None:
    """Force *path* to the physical drive.

    Skipped silently if it cannot be done. This matters more here than it
    usually would: backups go to removable media, and a user who sees "done" and
    unplugs the drive should be getting a database that survives it.
    """
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError as exc:
        logger.debug("Could not fsync %s: %s", path, exc)
    finally:
        os.close(fd)


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.debug("Could not remove %s: %s", path, exc)
