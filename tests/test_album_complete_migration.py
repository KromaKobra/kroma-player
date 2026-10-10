"""The album completeness flag, against a database that predates it.

The same shape as ``tests/test_track_gain_migration.py``, and it exists for the
same reason: ``create_all()`` creates missing *tables* and never missing
*columns*, so a column added to ``albums`` reaches fresh vaults and nowhere else
until a migration puts it there.

What is specific to this one is that there is nothing to backfill even in
principle. A track's credit order was recoverable from physical row order, and a
gain of zero is the honest reading of a library nobody has trimmed — but whether
an album holds the whole release is a fact about a catalogue's page, and no
amount of reading the database can recover it. Every existing album therefore
starts un-flagged, which is not a guess: "nobody has checked this" and "this is
not the whole record" are the same absence of a checkmark.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.database.connection import DatabaseManager
from app.database.schema import SCHEMA_VERSION, migrate
from app.models.track import Album

# `albums` as it was between the v4 migration that created it and this one. The
# rest of the schema is left to `create_all`, which does make missing tables.
V4_ALBUMS = """
CREATE TABLE albums (
    id INTEGER NOT NULL, name VARCHAR(255) NOT NULL,
    youtube_id VARCHAR(64), cover_hash VARCHAR(32), PRIMARY KEY (id));
"""


@pytest.fixture
def pre_complete_db(tmp_path):
    path = tmp_path / "tracks.db"
    conn = sqlite3.connect(path)
    conn.executescript(V4_ALBUMS)
    conn.executemany(
        "INSERT INTO albums (id, name) VALUES (?, ?)",
        [(1, "Discovery"), (2, "Homework")],
    )
    # Stamped as far as the step before this one, which is what a library that
    # has been opened by the previous build actually looks like.
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    conn.close()
    return path


def _columns(path, table):
    conn = sqlite3.connect(path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _flags(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            "SELECT id, is_complete FROM albums ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def _user_version(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def test_opening_an_older_database_adds_the_column(pre_complete_db):
    assert "is_complete" not in _columns(pre_complete_db, "albums")

    DatabaseManager.from_path(pre_complete_db)

    assert "is_complete" in _columns(pre_complete_db, "albums")
    assert _user_version(pre_complete_db) == SCHEMA_VERSION


def test_every_existing_album_starts_unflagged(pre_complete_db):
    """Zero, not NULL — a checkmark nobody has earned must not appear."""
    DatabaseManager.from_path(pre_complete_db)

    assert _flags(pre_complete_db) == [(1, 0), (2, 0)]


def test_the_column_reads_back_through_the_model(pre_complete_db):
    manager = DatabaseManager.from_path(pre_complete_db)
    session = manager.create_session()
    try:
        assert all(a.is_complete is False for a in session.query(Album).all())

        album = session.get(Album, 1)
        album.is_complete = True
        session.commit()
    finally:
        session.close()

    assert _flags(pre_complete_db) == [(1, 1), (2, 0)]


def test_migrating_twice_changes_nothing(pre_complete_db):
    """The stamp is an optimisation; `add_column` is what makes the step safe.

    A flag the user has since earned must survive a step that meets a column it
    has, in effect, already added — which is exactly what a freshly created
    database looks like, since `create_all` makes the column while
    ``user_version`` is still 0.
    """
    manager = DatabaseManager.from_path(pre_complete_db)

    conn = sqlite3.connect(pre_complete_db)
    conn.execute("UPDATE albums SET is_complete = 1 WHERE id = 1")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()

    assert migrate(manager.engine) == SCHEMA_VERSION

    assert _flags(pre_complete_db) == [(1, 1), (2, 0)], "a set flag was cleared"
