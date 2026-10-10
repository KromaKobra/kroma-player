"""Migration 7: ids leave their fixed columns for tables of their own.

A library from before it kept one column per catalogue on ``tracks``,
``artists`` and ``albums``. The migration copies every value into the
external-id tables under the key the plugin that understands it declares, then
drops the columns. These pin the part that cannot be got wrong: nothing is lost,
nothing is invented, and running it twice changes nothing.

The DDL below is the schema as it was at version 6, which is why it still
spells the old columns. It is history, the same as the migration that reads it.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.database.connection import DatabaseManager
from app.database.schema import SCHEMA_VERSION
from app.plugins import external_ids

V6_SCHEMA = """
CREATE TABLE tracks (
    id INTEGER NOT NULL, title VARCHAR(255) NOT NULL,
    release_date INTEGER, duration_seconds INTEGER NOT NULL,
    musical_key VARCHAR(50), bpm INTEGER, copyright_text VARCHAR,
    youtube_id VARCHAR(32), spotify_id VARCHAR(64), isrc VARCHAR(15),
    cover_hash VARCHAR(32), created_at INTEGER,
    gain_db FLOAT NOT NULL DEFAULT 0, instrumental_youtube_id VARCHAR(32),
    PRIMARY KEY (id));
CREATE TABLE artists (
    id INTEGER NOT NULL, name VARCHAR(255) NOT NULL UNIQUE,
    youtube_id VARCHAR(64), image_hash VARCHAR(32), PRIMARY KEY (id));
CREATE INDEX ix_artists_youtube_id ON artists (youtube_id);
CREATE TABLE albums (
    id INTEGER NOT NULL, name VARCHAR(255) NOT NULL,
    youtube_id VARCHAR(64), cover_hash VARCHAR(32),
    is_complete BOOLEAN NOT NULL DEFAULT 0, PRIMARY KEY (id));
CREATE INDEX ix_albums_youtube_id ON albums (youtube_id);
"""

LEGACY = {
    "tracks": {"youtube_id", "instrumental_youtube_id", "spotify_id"},
    "artists": {"youtube_id"},
    "albums": {"youtube_id"},
}


@pytest.fixture
def v6_db(tmp_path):
    path = tmp_path / "tracks.db"
    conn = sqlite3.connect(path)
    conn.executescript(V6_SCHEMA)
    conn.executemany(
        "INSERT INTO tracks (id, title, duration_seconds, youtube_id, "
        "instrumental_youtube_id, spotify_id) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, "Promises", 180, "vid00000001", None, "4uLU6hMCjMI75M1A2tKUQC"),
            # Padded, the way a hand-typed value might have been stored.
            (2, "Bangarang", 215, "  vid00000002  ", "inst0000001", None),
            # Blank is not an id, and must not become one.
            (3, "Ruffneck", 200, "", "   ", ""),
            # Two tracks sharing an id: never a constraint, so it happened.
            (4, "Ruffneck (Full Flex)", 200, "vid00000001", "inst0000001", None),
        ],
    )
    conn.executemany(
        "INSERT INTO artists (id, name, youtube_id, image_hash) VALUES (?, ?, ?, ?)",
        [(1, "Skrillex", "UCskrillex01", "aaa"), (2, "Nobody", None, None)],
    )
    conn.executemany(
        "INSERT INTO albums (id, name, youtube_id, cover_hash) VALUES (?, ?, ?, ?)",
        [(1, "Bangarang EP", "MPREb_bangarang", "bbb"), (2, "Loose", None, None)],
    )
    conn.execute("PRAGMA user_version = 6")
    conn.commit()
    conn.close()
    return path


def _columns(path, table):
    conn = sqlite3.connect(path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _indexes(path):
    conn = sqlite3.connect(path)
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        conn.close()


def _user_version(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _held(manager):
    session = manager.create_session()
    try:
        return {
            entity: external_ids.read_many(session, entity, range(1, 10))
            for entity in external_ids.ENTITIES
        }
    finally:
        session.close()


def test_every_id_moves_to_the_tables(v6_db):
    manager = DatabaseManager.from_path(v6_db)

    assert _held(manager) == {
        "track": {
            1: {"youtube:video": "vid00000001", "spotify:track": "4uLU6hMCjMI75M1A2tKUQC"},
            2: {"youtube:video": "vid00000002", "youtube:instrumental-video": "inst0000001"},
            4: {"youtube:video": "vid00000001", "youtube:instrumental-video": "inst0000001"},
        },
        "artist": {1: {"youtube:channel": "UCskrillex01"}},
        "album": {1: {"youtube:release": "MPREb_bangarang"}},
    }
    assert _user_version(v6_db) == SCHEMA_VERSION


def test_the_old_columns_and_their_indexes_are_gone(v6_db):
    DatabaseManager.from_path(v6_db)

    for table, columns in LEGACY.items():
        assert not columns & _columns(v6_db, table), table
    assert not {"ix_artists_youtube_id", "ix_albums_youtube_id"} & _indexes(v6_db)
    # Everything else on the rows is untouched.
    assert {"image_hash", "name"} <= _columns(v6_db, "artists")
    assert {"cover_hash", "is_complete"} <= _columns(v6_db, "albums")


def test_the_rest_of_each_row_survives(v6_db):
    manager = DatabaseManager.from_path(v6_db)
    conn = sqlite3.connect(manager.db_path)
    try:
        assert conn.execute("SELECT title FROM tracks ORDER BY id").fetchall() == [
            ("Promises",), ("Bangarang",), ("Ruffneck",), ("Ruffneck (Full Flex)",),
        ]
        assert conn.execute("SELECT image_hash FROM artists WHERE id = 1").fetchone() == ("aaa",)
        assert conn.execute("SELECT cover_hash FROM albums WHERE id = 1").fetchone() == ("bbb",)
    finally:
        conn.close()


def test_migrating_twice_changes_nothing(v6_db):
    """A second run meets no old column, and must not touch what the first wrote."""
    manager = DatabaseManager.from_path(v6_db)
    conn = sqlite3.connect(v6_db)
    # An edit made since: one id cleared by hand. A re-run must not bring it back.
    conn.execute("DELETE FROM track_external_ids WHERE track_id = 2 AND kind = 'video'")
    conn.execute("PRAGMA user_version = 6")
    conn.commit()
    conn.close()
    before = _held(manager)

    DatabaseManager.from_path(v6_db)

    assert _held(manager) == before
    assert "youtube:video" not in before["track"][2]
    assert _user_version(v6_db) == SCHEMA_VERSION


def test_a_fresh_library_never_had_the_columns(tmp_path):
    manager = DatabaseManager.from_path(tmp_path / "new.db")

    for table, columns in LEGACY.items():
        assert not columns & _columns(manager.db_path, table), table
    assert _held(manager) == {"track": {}, "artist": {}, "album": {}}
