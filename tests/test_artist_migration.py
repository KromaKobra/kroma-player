"""The schema migration, against a database that predates it.

``create_all()`` creates missing *tables* and never missing *columns*, so before
this existed a new column on an existing table simply never reached anybody's
library. These tests build a v0 ``tracks.db`` by hand — the shape a real library
already on disk has — and check that opening it evolves it without losing a row.

Running each one twice is not padding. A freshly created database gets its
columns from ``create_all`` and still carries ``user_version = 0``, so the very
first thing the migration meets in the ordinary case is a step that has, in
effect, already been applied. Idempotence is the property that has to hold.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.database.connection import DatabaseManager
from app.database.schema import SCHEMA_VERSION, add_column, migrate
from app.models.track import Artist
from app.repositories.artist_repository import ArtistRepository


# The `artists` table as it was before artists had an identity of their own,
# plus just enough around it for the foreign keys to resolve.
V0_SCHEMA = """
CREATE TABLE tracks (
    id INTEGER NOT NULL, title VARCHAR(255) NOT NULL, album VARCHAR(255),
    release_date INTEGER, duration_seconds INTEGER NOT NULL,
    musical_key VARCHAR(50), bpm INTEGER, copyright_text VARCHAR,
    youtube_id VARCHAR(32), spotify_id VARCHAR(64), isrc VARCHAR(15),
    cover_hash VARCHAR(32), created_at INTEGER, PRIMARY KEY (id));
CREATE TABLE artists (
    id INTEGER NOT NULL, name VARCHAR(255) NOT NULL,
    PRIMARY KEY (id), UNIQUE (name));
CREATE TABLE track_artists (
    track_id INTEGER NOT NULL, artist_id INTEGER NOT NULL,
    PRIMARY KEY (track_id, artist_id),
    FOREIGN KEY(track_id) REFERENCES tracks (id),
    FOREIGN KEY(artist_id) REFERENCES artists (id));
"""


@pytest.fixture
def v0_db(tmp_path):
    """A database in the shape it had before artists gained columns."""
    path = tmp_path / "tracks.db"
    conn = sqlite3.connect(path)
    conn.executescript(V0_SCHEMA)
    conn.executemany(
        "INSERT INTO artists (id, name) VALUES (?, ?)",
        [(1, "person1/person2"), (2, "Skrillex")],
    )
    conn.executemany(
        "INSERT INTO tracks (id, title, duration_seconds) VALUES (?, ?, ?)",
        [(1, "Promises", 180), (2, "Bangarang", 215), (3, "A Collaboration", 200)],
    )
    # Track 3's credits go in deliberately *against* artist id, which is the only
    # arrangement that can tell the credit-order backfill from the ordering it
    # replaces. Row order is the record of who was credited first.
    conn.executemany(
        "INSERT INTO track_artists VALUES (?, ?)", [(1, 1), (2, 2), (3, 2), (3, 1)]
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


def _indexes(path):
    conn = sqlite3.connect(path)
    try:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_%'"
            )
        }
    finally:
        conn.close()


def _credits(path, track_id):
    """``[(artist_id, position)]`` for one track, in the order it reads in."""
    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            "SELECT artist_id, position FROM track_artists "
            "WHERE track_id = ? ORDER BY position",
            (track_id,),
        ).fetchall()
    finally:
        conn.close()


def _user_version(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def test_opening_a_v0_database_adds_the_artist_columns(v0_db):
    assert _columns(v0_db, "artists") == {"id", "name"}
    assert _user_version(v0_db) == 0

    DatabaseManager.from_path(v0_db)

    # The id column migration 1 added is gone again by the end of the chain:
    # ids have a table of their own now.
    assert _columns(v0_db, "artists") == {"id", "name", "image_hash"}
    assert _columns(v0_db, "artist_external_ids") == {"artist_id", "provider", "kind", "value"}
    assert _user_version(v0_db) == SCHEMA_VERSION


def test_migration_adds_the_lookup_indexes(v0_db):
    """``track_artists`` is keyed (track_id, artist_id), so a lookup by artist
    alone cannot use the primary key — and that lookup is the artist page."""
    DatabaseManager.from_path(v0_db)
    assert {"ix_track_artists_artist", "ix_artist_external_ids_value"} <= _indexes(v0_db)


def test_existing_rows_survive(v0_db):
    manager = DatabaseManager.from_path(v0_db)
    session = manager.create_session()
    try:
        names = {a.name for a in session.query(Artist).all()}
        assert names == {"person1/person2", "Skrillex"}
        # Nothing identifies them yet, and that reads as empty rather than as an error.
        assert all(not a.external_id_rows for a in session.query(Artist).all())
        # Track 1 alone, plus the collaboration.
        assert ArtistRepository(session).track_count(1) == 2
    finally:
        session.close()


def test_the_credit_order_is_recovered_from_the_rows_already_there(v0_db):
    """The order was never stored, but it was never quite lost either.

    Every path that credits a track inserts its artists in the order the source
    gave them, so physical row order *is* the credit order — and reading it back
    is what recovers what the old ``ORDER BY artists.id`` was throwing away.
    """
    assert "position" not in _columns(v0_db, "track_artists")

    DatabaseManager.from_path(v0_db)

    assert _credits(v0_db, 3) == [(2, 0), (1, 1)], (
        "artist 2 was credited first on track 3 and must stay first. Numbering by "
        "artist_id instead would put artist 1 in front, which is the ordering "
        "this migration exists to stop using."
    )
    # A track with one artist is still numbered from zero rather than left as
    # whatever the ADD COLUMN default was.
    assert _credits(v0_db, 1) == [(1, 0)]


def test_a_hand_made_order_survives_a_later_migration_run(v0_db):
    """The backfill is guarded on the column being new, and this is why.

    Re-running it against a library whose owner has since arranged their credits
    by hand would renumber them back to row order — silently undoing exactly the
    work the column exists to keep.
    """
    manager = DatabaseManager.from_path(v0_db)
    conn = sqlite3.connect(v0_db)
    conn.execute("UPDATE track_artists SET position = 9 WHERE track_id = 3 AND artist_id = 2")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()

    migrate(manager.engine)

    assert _credits(v0_db, 3) == [(1, 1), (2, 9)]


def test_migrating_twice_changes_nothing(v0_db):
    """The stamp is an optimisation, not what makes the step safe."""
    manager = DatabaseManager.from_path(v0_db)
    before = (_columns(v0_db, "artists"), _columns(v0_db, "track_artists"),
              _indexes(v0_db), _user_version(v0_db), _credits(v0_db, 3))

    assert migrate(manager.engine) == SCHEMA_VERSION
    # And again with the stamp deliberately rolled back, which is what a step
    # meeting an already-applied change looks like.
    conn = sqlite3.connect(v0_db)
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()
    assert migrate(manager.engine) == SCHEMA_VERSION

    assert (_columns(v0_db, "artists"), _columns(v0_db, "track_artists"),
            _indexes(v0_db), _user_version(v0_db), _credits(v0_db, 3)) == before


def test_a_fresh_database_is_stamped_current(tmp_path):
    """create_all() built the columns; the migration must still record itself,
    or every later launch would re-run every step from the beginning."""
    manager = DatabaseManager.from_path(tmp_path / "new.db")
    assert _user_version(manager.db_path) == SCHEMA_VERSION
    assert "image_hash" in _columns(manager.db_path, "artists")


def test_add_column_is_a_no_op_when_the_column_exists(v0_db):
    manager = DatabaseManager.from_path(v0_db)
    with manager.engine.begin() as conn:
        assert add_column(conn, "artists", "image_hash", "VARCHAR(32)") is False
        assert add_column(conn, "artists", "brand_new", "VARCHAR(8)") is True
    assert "brand_new" in _columns(v0_db, "artists")
