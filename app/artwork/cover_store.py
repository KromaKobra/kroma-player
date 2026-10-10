"""Content-addressed SQLite blob cache for cover thumbnails.

Why a single SQLite file instead of one image file per thumbnail:
- Keeps the library portable — the whole cache is one file next to ``tracks.db``
  inside ``.data/``, so moving/backing-up the library stays trivial.
- Avoids a "cache dump" of thousands of tiny files that are slow to copy and
  clutter the filesystem.
- SQLite handles many small blobs efficiently.

The store is purely a derived cache: it can be deleted and rebuilt from the
artwork embedded in the Opus files at any time. Rows are keyed by
``(cover_hash, size)`` so identical album covers are stored once and shared.

Thread-safety: the cover provider decodes on Qt worker threads while downloads
and the backfill write from their own threads. SQLite connections cannot be
shared across threads, so each thread lazily gets its own connection via
``threading.local``. WAL mode lets readers and the writer proceed concurrently.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


class CoverStore:
    """Thread-safe, content-addressed blob cache for AVIF cover thumbnails."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        # Create the schema once on the constructing thread.
        self._ensure_schema(self._conn())

    # ------------------------------------------------------------------
    # Connection management (one connection per thread)
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS covers (
                cover_hash TEXT    NOT NULL,
                size       INTEGER NOT NULL,
                format     TEXT    NOT NULL DEFAULT 'avif',
                data       BLOB    NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (cover_hash, size)
            )
            """
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def has(self, cover_hash: str, size: int) -> bool:
        """Return True if a thumbnail of this hash/size is cached."""
        row = self._conn().execute(
            "SELECT 1 FROM covers WHERE cover_hash = ? AND size = ? LIMIT 1",
            (cover_hash, size),
        ).fetchone()
        return row is not None

    def get(self, cover_hash: str, size: int) -> bytes | None:
        """Return the cached AVIF bytes for a hash/size, or None if absent."""
        row = self._conn().execute(
            "SELECT data FROM covers WHERE cover_hash = ? AND size = ?",
            (cover_hash, size),
        ).fetchone()
        return bytes(row[0]) if row is not None else None

    def put(self, cover_hash: str, size: int, data: bytes, fmt: str = "avif") -> None:
        """Insert or replace a thumbnail blob."""
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO covers (cover_hash, size, format, data, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (cover_hash, size, fmt, data, int(time.time())),
        )
        conn.commit()

    def vacuum_orphans(self, valid_hashes: set[str]) -> int:
        """Delete thumbnails whose hash is no longer referenced by any track.

        Args:
            valid_hashes: The set of cover hashes still in use.

        Returns:
            Number of rows deleted.
        """
        conn = self._conn()
        existing = {row[0] for row in conn.execute("SELECT DISTINCT cover_hash FROM covers")}
        orphans = existing - valid_hashes
        if not orphans:
            return 0
        conn.executemany("DELETE FROM covers WHERE cover_hash = ?", [(h,) for h in orphans])
        conn.commit()
        return len(orphans)
