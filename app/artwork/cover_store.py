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
        conn.execute(
            "CREATE TABLE IF NOT EXISTS playlist_covers ("
            "playlist_id INTEGER PRIMARY KEY, cover_hash TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_playlist_covers_hash "
            "ON playlist_covers (cover_hash)"
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
        # Generated playlist art is derived metadata owned by this cache rather
        # than a custom cover in tracks.db. It participates in every sweep.
        playlist_hashes = {row[0] for row in conn.execute(
            "SELECT DISTINCT cover_hash FROM playlist_covers"
        )}
        orphans = existing - valid_hashes - playlist_hashes
        if not orphans:
            return 0
        conn.executemany(
            "DELETE FROM covers WHERE cover_hash = ? AND NOT EXISTS "
            "(SELECT 1 FROM playlist_covers WHERE cover_hash = ?)",
            [(h, h) for h in orphans],
        )
        conn.commit()
        return len(orphans)

    def playlist_cover(self, playlist_id: int) -> str | None:
        row = self._conn().execute(
            "SELECT cover_hash FROM playlist_covers WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()
        return row[0] if row else None

    def set_playlist_cover(
        self, playlist_id: int, cover_hash: str, tiers: dict[int, bytes],
    ) -> None:
        """Publish both tiers and their playlist reference atomically."""
        conn = self._conn()
        with conn:
            conn.executemany(
                "INSERT OR IGNORE INTO covers "
                "(cover_hash, size, format, data, created_at) VALUES (?, ?, 'avif', ?, ?)",
                [(cover_hash, size, data, int(time.time())) for size, data in tiers.items()],
            )
            conn.execute(
                "INSERT OR REPLACE INTO playlist_covers (playlist_id, cover_hash) VALUES (?, ?)",
                (playlist_id, cover_hash),
            )

    def remove_playlist_cover(self, playlist_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM playlist_covers WHERE playlist_id = ?", (playlist_id,))

    def delete_unreferenced_playlist_cover(self, cover_hash: str) -> None:
        """Delete retired tiles unless another generated playlist still uses them.

        The caller first checks references in tracks.db (tracks, albums, artists
        and custom playlist covers). This targets one hash, avoiding a full sweep.
        """
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM covers WHERE cover_hash = ? AND NOT EXISTS "
                "(SELECT 1 FROM playlist_covers WHERE cover_hash = ?)",
                (cover_hash, cover_hash),
            )
