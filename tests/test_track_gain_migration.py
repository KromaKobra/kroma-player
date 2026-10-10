"""The per-track gain column, against a database that predates it.

Same shape of check as ``tests/test_artist_migration.py`` and for the same
reason: ``create_all()`` creates missing *tables* and never missing *columns*,
so a new column on ``tracks`` reaches fresh vaults and nowhere else unless a
migration puts it there.

What is specific to this one is the default. Every row that already exists has
to come out at zero decibels — "as recorded" — because a library that has never
been told otherwise must sound exactly as it did before the column existed.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.database.connection import DatabaseManager
from app.database.schema import SCHEMA_VERSION, migrate
from app.models.track import Track

# `tracks` as it was before a track could carry its own level. The other tables
# are left to `create_all`, which does make missing tables.
V2_TRACKS = """
CREATE TABLE tracks (
    id INTEGER NOT NULL, title VARCHAR(255) NOT NULL, album VARCHAR(255),
    release_date INTEGER, duration_seconds INTEGER NOT NULL,
    musical_key VARCHAR(50), bpm INTEGER, copyright_text VARCHAR,
    youtube_id VARCHAR(32), spotify_id VARCHAR(64), isrc VARCHAR(15),
    cover_hash VARCHAR(32), created_at INTEGER, PRIMARY KEY (id));
"""


@pytest.fixture
def pre_gain_db(tmp_path):
    path = tmp_path / "tracks.db"
    conn = sqlite3.connect(path)
    conn.executescript(V2_TRACKS)
    conn.executemany(
        "INSERT INTO tracks (id, title, duration_seconds) VALUES (?, ?, ?)",
        [(1, "Promises", 180), (2, "Bangarang", 215)],
    )
    conn.commit()
    conn.close()
    return path


def _columns(path, table):
    conn = sqlite3.connect(path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _gains(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT id, gain_db FROM tracks ORDER BY id").fetchall()
    finally:
        conn.close()


def _user_version(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def test_opening_an_older_database_adds_the_column(pre_gain_db):
    assert "gain_db" not in _columns(pre_gain_db, "tracks")

    DatabaseManager.from_path(pre_gain_db)

    assert "gain_db" in _columns(pre_gain_db, "tracks")
    assert _user_version(pre_gain_db) == SCHEMA_VERSION


def test_every_existing_track_lands_on_no_correction(pre_gain_db):
    """Zero, not NULL. A library nobody has trimmed must sound unchanged."""
    DatabaseManager.from_path(pre_gain_db)

    assert _gains(pre_gain_db) == [(1, 0.0), (2, 0.0)]


def test_the_column_reads_back_through_the_model(pre_gain_db):
    manager = DatabaseManager.from_path(pre_gain_db)
    session = manager.create_session()
    try:
        assert all(t.gain_db == pytest.approx(0.0) for t in session.query(Track).all())

        track = session.get(Track, 1)
        track.gain_db = -4.5
        session.commit()
    finally:
        session.close()

    assert _gains(pre_gain_db) == [(1, -4.5), (2, 0.0)]


def test_migrating_twice_changes_nothing(pre_gain_db):
    """The stamp is an optimisation; `add_column` is what makes the step safe.

    A trim the user has since set must survive a step that meets a column it
    has, in effect, already added — which is exactly what a freshly created
    database looks like, since `create_all` makes the column while
    ``user_version`` is still 0.
    """
    manager = DatabaseManager.from_path(pre_gain_db)

    conn = sqlite3.connect(pre_gain_db)
    conn.execute("UPDATE tracks SET gain_db = -6.0 WHERE id = 1")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()

    assert migrate(manager.engine) == SCHEMA_VERSION

    assert _gains(pre_gain_db) == [(1, -6.0), (2, 0.0)], "a set trim was not reset"
