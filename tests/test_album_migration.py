"""Migration 4: the free-text `tracks.album` becomes rows of its own.

The pattern of tests/test_artist_migration.py and tests/test_track_gain_migration.py:
build a database by hand in the shape a released build left behind, migrate it,
and assert on what came out. Then migrate again, because a step that runs twice
must be a no-op the second time.
"""
from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import create_engine, text

from app.database.schema import SCHEMA_VERSION, create_schema, migrate

# A v3 library: `tracks.album` present, no albums tables, `track_artists` already
# carrying the credit order migration 2 added. Deliberately verbatim rather than
# generated from the models — the models no longer describe this shape, which is
# the whole point.
V3_SCHEMA = """
CREATE TABLE tracks (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    title VARCHAR(255) NOT NULL,
    album VARCHAR(255),
    release_date INTEGER,
    duration_seconds INTEGER NOT NULL,
    musical_key VARCHAR(50),
    bpm INTEGER,
    copyright_text VARCHAR,
    youtube_id VARCHAR(32),
    spotify_id VARCHAR(64),
    isrc VARCHAR(15),
    cover_hash VARCHAR(32),
    created_at INTEGER,
    gain_db FLOAT NOT NULL DEFAULT 0
);
CREATE TABLE artists (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(255) NOT NULL UNIQUE,
    youtube_id VARCHAR(64),
    image_hash VARCHAR(32)
);
CREATE TABLE track_artists (
    track_id INTEGER NOT NULL REFERENCES tracks(id),
    artist_id INTEGER NOT NULL REFERENCES artists(id),
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (track_id, artist_id)
);
CREATE TABLE genres (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(100) NOT NULL UNIQUE
);
CREATE TABLE track_genres (
    track_id INTEGER NOT NULL, genre_id INTEGER NOT NULL,
    PRIMARY KEY (track_id, genre_id)
);
CREATE TABLE version_types (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(100) NOT NULL UNIQUE
);
CREATE TABLE track_versions (
    track_id INTEGER NOT NULL, version_type_id INTEGER NOT NULL,
    PRIMARY KEY (track_id, version_type_id)
);
PRAGMA user_version = 3;
"""

# (id, title, album, cover_hash, artist_id)
LEGACY_TRACKS = [
    (1, "Bohemian",      "Greatest Hits", None,  1),
    (2, "Somebody",      "Greatest Hits", "aaa", 1),
    (3, "Dancing Queen", "Greatest Hits", "bbb", 2),
    (4, "Waterloo",      "greatest hits", "bbb", 2),   # folds into ABBA's
    (5, "One More Time", "Discovery",     "ccc", 1),
    (6, "Untitled",      "",              None,  1),   # no album at all
    (7, "Whitespace",    "   ",           None,  1),   # nor this
    (8, "Orphan",        "Nobody",        "ddd", None),  # no artist credited
]


@pytest.fixture
def legacy_db(tmp_path):
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(path)
    raw.executescript(V3_SCHEMA)
    raw.executemany(
        "INSERT INTO artists (id, name) VALUES (?, ?)",
        [(1, "Queen"), (2, "ABBA")],
    )
    for track_id, title, album, cover, artist_id in LEGACY_TRACKS:
        raw.execute(
            "INSERT INTO tracks (id, title, album, duration_seconds, cover_hash)"
            " VALUES (?,?,?,?,?)",
            (track_id, title, album, 200, cover),
        )
        if artist_id:
            raw.execute(
                "INSERT INTO track_artists (track_id, artist_id, position)"
                " VALUES (?,?,0)",
                (track_id, artist_id),
            )
    raw.commit()
    raw.close()
    return path


@pytest.fixture
def migrated(legacy_db):
    engine = create_engine(f"sqlite:///{legacy_db}")
    create_schema(engine)
    return engine


def _rows(engine, sql):
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql))]


def test_the_database_ends_on_the_current_version(migrated):
    assert _rows(migrated, "PRAGMA user_version")[0][0] == SCHEMA_VERSION


def test_the_free_text_column_is_gone(migrated):
    columns = {r[1] for r in _rows(migrated, "PRAGMA table_info(tracks)")}
    assert "album" not in columns


@pytest.mark.skipif(
    sqlite3.sqlite_version_info < (3, 35, 0),
    reason="DROP COLUMN needs SQLite 3.35; below it the column is blanked instead",
)
def test_dropping_is_what_actually_happened(migrated):
    """Guarded rather than assumed — the migration feature-detects, so this does."""
    columns = {r[1] for r in _rows(migrated, "PRAGMA table_info(tracks)")}
    assert "album" not in columns


def test_same_name_by_different_artists_stays_two_albums(migrated):
    names = _rows(migrated, "SELECT name FROM albums ORDER BY id")
    assert [n[0] for n in names].count("Greatest Hits") == 2


def test_case_folds_into_one_album(migrated):
    """"greatest hits" joins ABBA's rather than minting a third row."""
    rows = _rows(
        migrated,
        "SELECT a.id, COUNT(ta.track_id) FROM albums a"
        " JOIN track_albums ta ON ta.album_id = a.id"
        " WHERE a.name = 'Greatest Hits' GROUP BY a.id ORDER BY a.id",
    )
    assert sorted(count for _id, count in rows) == [2, 2]


def test_the_display_name_keeps_the_first_spelling(migrated):
    """Grouping folded case; one spelling has to win and it is the earliest."""
    names = {n[0] for n in _rows(migrated, "SELECT name FROM albums")}
    assert "greatest hits" not in names


def test_position_follows_track_id_within_an_album(migrated):
    rows = _rows(
        migrated,
        "SELECT ta.track_id, ta.position FROM track_albums ta"
        " JOIN albums a ON a.id = ta.album_id"
        " WHERE a.name = 'Discovery' OR ta.album_id ="
        "   (SELECT MIN(album_id) FROM track_albums)"
        " ORDER BY ta.album_id, ta.position",
    )
    # Queen's Greatest Hits: tracks 1 and 2, numbered 0 and 1 in that order.
    assert (1, 0) in rows and (2, 1) in rows


def test_every_backfilled_track_is_primary(migrated):
    positions = _rows(migrated, "SELECT DISTINCT album_position FROM track_albums")
    assert positions == [(0,)]


def test_cover_is_seeded_from_the_first_track_that_has_one(migrated):
    """Not simply the first track — that one may be the one missing artwork."""
    rows = _rows(
        migrated,
        "SELECT a.cover_hash FROM albums a"
        " JOIN track_albums ta ON ta.album_id = a.id AND ta.position = 0"
        " WHERE ta.track_id = 1",
    )
    assert rows == [("aaa",)]


def test_a_track_with_no_album_gets_no_row(migrated):
    rows = _rows(migrated, "SELECT track_id FROM track_albums WHERE track_id IN (6, 7)")
    assert rows == []


def test_a_track_with_no_artist_still_gets_its_album(migrated):
    rows = _rows(
        migrated,
        "SELECT a.name FROM albums a JOIN track_albums ta ON ta.album_id = a.id"
        " WHERE ta.track_id = 8",
    )
    assert rows == [("Nobody",)]


def test_migrating_twice_changes_nothing(migrated):
    before = _rows(migrated, "SELECT id, name, cover_hash FROM albums ORDER BY id")
    members = _rows(migrated, "SELECT track_id, album_id, position FROM track_albums")

    assert migrate(migrated) == SCHEMA_VERSION
    assert _rows(migrated, "SELECT id, name, cover_hash FROM albums ORDER BY id") == before
    assert _rows(migrated, "SELECT track_id, album_id, position FROM track_albums") == members


def test_a_hand_arranged_order_survives_a_second_run(migrated):
    """The guard exists so a re-run cannot renumber what the user arranged."""
    with migrated.begin() as conn:
        conn.execute(text("UPDATE track_albums SET position = 9 WHERE track_id = 1"))

    migrate(migrated)
    rows = _rows(migrated, "SELECT position FROM track_albums WHERE track_id = 1")
    assert rows == [(9,)]


def test_a_fresh_database_is_stamped_current_and_holds_no_albums(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    create_schema(engine)

    assert _rows(engine, "PRAGMA user_version")[0][0] == SCHEMA_VERSION
    assert _rows(engine, "SELECT COUNT(*) FROM albums") == [(0,)]
    columns = {r[1] for r in _rows(engine, "PRAGMA table_info(tracks)")}
    assert "album" not in columns
