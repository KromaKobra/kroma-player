"""The `lyrics` table: its schema claim, its upsert, and its cascade.

Adding a *table* has never needed a migration — ``create_all()`` creates any
that does not exist yet, which is the claim ``app/database/schema.py`` makes in
its own docstring. That is worth pinning rather than trusting, because the
alternative (a column on an existing table) does need one and looks identical
in the model file. So the first test here opens a database built **before** the
table existed and checks it simply appears.

The rest is the shape of the row: one per track, replaced rather than
accumulated, and gone at the database level when the track is.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.database.connection import DatabaseManager  # noqa: E402
from app.models.track import Track, TrackLyrics  # noqa: E402
from app.repositories.lyrics_repository import LyricsRepository  # noqa: E402
from app.services.lyrics_service import (  # noqa: E402
    SOURCE_MANUAL,
    SOURCE_SEARCH,
    SOURCE_TAG,
    LyricsService,
)

# A vault from before the table, written by hand. Everything else is left to
# `create_all`, which is exactly what is under test.
PRE_LYRICS_SCHEMA = """
CREATE TABLE tracks (
    id INTEGER NOT NULL, title VARCHAR(255) NOT NULL, album VARCHAR(255),
    release_date INTEGER, duration_seconds INTEGER NOT NULL,
    musical_key VARCHAR(50), bpm INTEGER, copyright_text VARCHAR,
    youtube_id VARCHAR(32), spotify_id VARCHAR(64), isrc VARCHAR(15),
    cover_hash VARCHAR(32), created_at INTEGER,
    gain_db FLOAT NOT NULL DEFAULT 0, PRIMARY KEY (id));
"""


@pytest.fixture
def lyrics(db):
    return LyricsRepository(db.session)


@pytest.fixture
def service(db, event_bus):
    return LyricsService(LyricsRepository(db.session), event_bus)


def _track_id(db) -> int:
    return db.session.query(Track.id).order_by(Track.id).first()[0]


# ============================================================ the schema


def test_the_table_appears_on_a_database_that_predates_it(tmp_path):
    """`create_all` makes a missing table, which is why there is no migration."""
    path = tmp_path / "tracks.db"
    conn = sqlite3.connect(path)
    conn.executescript(PRE_LYRICS_SCHEMA)
    conn.executemany(
        "INSERT INTO tracks (id, title, duration_seconds) VALUES (?, ?, ?)",
        [(1, "Promises", 180), (2, "Bangarang", 215)],
    )
    conn.commit()
    conn.close()

    # Opening the vault is the whole migration.
    DatabaseManager.from_path(path)

    conn = sqlite3.connect(path)
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        columns = {row[1] for row in conn.execute("PRAGMA table_info(lyrics)")}
    finally:
        conn.close()

    assert "lyrics" in tables
    assert columns == {"track_id", "content", "source", "updated_at"}
    # The tracks that were already there are untouched.
    assert len(tables & {"tracks"}) == 1


# ======================================================== the repository


def test_a_track_with_no_lyrics_reads_as_none(lyrics, db):
    assert lyrics.get(_track_id(db)) is None


def test_setting_lyrics_stores_the_document_and_its_source(lyrics, db):
    track_id = _track_id(db)
    lyrics.set(track_id, "[00:01.00]a line", SOURCE_TAG)

    row = lyrics.get(track_id)
    assert row.content == "[00:01.00]a line"
    assert row.source == SOURCE_TAG


def test_setting_twice_replaces_rather_than_accumulates(lyrics, db):
    """One track has one set of lyrics — a second set is a correction."""
    track_id = _track_id(db)
    lyrics.set(track_id, "from the file", SOURCE_TAG)
    lyrics.set(track_id, "[00:02.50]from a search", SOURCE_SEARCH)

    assert db.session.query(TrackLyrics).filter_by(track_id=track_id).count() == 1
    row = lyrics.get(track_id)
    assert row.content == "[00:02.50]from a search"
    assert row.source == SOURCE_SEARCH


def test_set_many_writes_a_whole_batch(lyrics, db):
    ids = [row[0] for row in db.session.query(Track.id).order_by(Track.id).limit(5)]

    written = lyrics.set_many([(i, f"words {i}") for i in ids], SOURCE_TAG)

    assert written == 5
    assert [lyrics.get(i).content for i in ids] == [f"words {i}" for i in ids]


def test_removing_lyrics_leaves_the_track(lyrics, db):
    track_id = _track_id(db)
    lyrics.set(track_id, "words", SOURCE_TAG)

    assert lyrics.remove(track_id) is True
    assert lyrics.get(track_id) is None
    assert db.session.get(Track, track_id) is not None
    # Removing what is not there is not an error.
    assert lyrics.remove(track_id) is False


# ================================================ track_ids_without_lyrics


def test_every_track_is_missing_lyrics_to_begin_with(lyrics, db):
    missing = lyrics.track_ids_without_lyrics()

    assert len(missing) == db.session.query(Track).count()
    assert missing == sorted(missing)


def test_a_track_leaves_the_missing_list_once_it_has_lyrics(lyrics, db):
    track_id = _track_id(db)
    before = len(lyrics.track_ids_without_lyrics())

    lyrics.set(track_id, "words", SOURCE_TAG)
    after = lyrics.track_ids_without_lyrics()

    assert len(after) == before - 1
    assert track_id not in after


# ============================================================ the cascade


def test_deleting_a_track_takes_its_lyrics_with_it(lyrics, db):
    """`ON DELETE CASCADE`, enforced by SQLite rather than by a service."""
    track_id = _track_id(db)
    lyrics.set(track_id, "words", SOURCE_TAG)

    db.session.delete(db.session.get(Track, track_id))
    db.session.commit()

    assert lyrics.get(track_id) is None


# ============================================================= the service


def test_saving_announces_the_track_id(service, db, event_bus):
    seen: list[int] = []
    event_bus.subscribe("lyrics.updated", seen.append)
    track_id = _track_id(db)

    assert service.save(track_id, "words", SOURCE_TAG) is True

    assert seen == [track_id]
    assert service.get(track_id) == "words"


def test_saving_nothing_is_a_no_op_not_a_delete(service, db, event_bus):
    """A lookup that found nothing must not wipe lyrics that are already there."""
    track_id = _track_id(db)
    service.save(track_id, "words", SOURCE_TAG)
    seen: list[int] = []
    event_bus.subscribe("lyrics.updated", seen.append)

    assert service.save(track_id, "", SOURCE_SEARCH) is False
    assert service.save(track_id, "   \n ", SOURCE_SEARCH) is False

    assert service.get(track_id) == "words"
    assert seen == []


def test_an_unknown_source_is_refused(service, db):
    with pytest.raises(ValueError):
        service.save(_track_id(db), "words", "guessed")


def test_save_many_announces_one_event_per_track(service, db, event_bus):
    ids = [row[0] for row in db.session.query(Track.id).order_by(Track.id).limit(3)]
    seen: list[int] = []
    event_bus.subscribe("lyrics.updated", seen.append)

    assert service.save_many([(i, "words") for i in ids], SOURCE_TAG) == 3

    assert seen == ids


def test_save_many_skips_the_entries_with_nothing_in_them(service, db):
    ids = [row[0] for row in db.session.query(Track.id).order_by(Track.id).limit(3)]

    written = service.save_many(
        [(ids[0], "words"), (ids[1], ""), (ids[2], None)], SOURCE_TAG
    )

    assert written == 1
    assert service.get(ids[1]) is None


# ======================================================= the file mirror
#
# The row is the read path; the file's LYRICS comment is the copy that travels
# with the audio. `save()` writes both, in that order — see
# docs/architecture/decisions/0008-lyrics-are-mirrored-into-the-database.md.
# What is pinned here is the order, the skip, and that a file that cannot be
# written never costs the caller the row.


@pytest.fixture
def written(monkeypatch):
    """Record what reaches the Opus file instead of writing one.

    The tag write itself is `update_opus_tags`, which `tests/test_track_edit.py`
    already exercises against a real container. What these tests are about is
    *which* writes happen and *when*, so the file is stubbed out and the whole
    module stays runnable without ffmpeg.
    """
    from app.services import lyrics_service as module

    calls: list[tuple] = []

    def fake_update(path, changes, picture_block=None):
        calls.append((str(path), dict(changes)))

    monkeypatch.setattr(module, "update_opus_tags", fake_update)
    return calls


@pytest.fixture
def on_disk(db, monkeypatch, isolated_library_root):
    """Give every seeded track a file, so the mirror has somewhere to write."""
    from app.utils.library_paths import track_path

    def ensure(track_id: int):
        path = track_path(track_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not really opus")
        return path

    return ensure


def test_saving_a_search_result_writes_the_file_too(service, db, written, on_disk):
    track_id = _track_id(db)
    path = on_disk(track_id)

    service.save(track_id, "[00:01.00]words", SOURCE_SEARCH)

    assert service.get(track_id) == "[00:01.00]words"
    assert written == [(str(path), {"LYRICS": "[00:01.00]words"})]


def test_lyrics_read_out_of_a_file_are_not_written_back_to_it(
    service, db, written, on_disk,
):
    """The walk's write path. Re-tagging with what the tag already said is a
    rewrite of the container for no change at all."""
    track_id = _track_id(db)
    on_disk(track_id)

    service.save(track_id, "words from the tag", SOURCE_TAG)

    assert service.get(track_id) == "words from the tag"
    assert written == []


def test_the_stash_can_override_the_inference(service, db, written, on_disk):
    """Its audio is still in a temp directory and gets tagged a moment later."""
    track_id = _track_id(db)
    on_disk(track_id)

    service.save(track_id, "words", SOURCE_SEARCH, write_tag=False)

    assert service.get(track_id) == "words"
    assert written == []


def test_a_batch_from_the_walk_writes_no_files(service, db, written, on_disk):
    ids = [row[0] for row in db.session.query(Track.id).order_by(Track.id).limit(3)]
    for track_id in ids:
        on_disk(track_id)

    service.save_many([(i, f"words {i}") for i in ids], SOURCE_TAG)

    assert written == []
    assert service.get(ids[0]) == f"words {ids[0]}"


def test_a_track_with_no_file_is_not_an_error(service, db, written):
    """Nothing on disk yet, or a track whose audio has gone missing."""
    track_id = _track_id(db)

    assert service.save(track_id, "words", SOURCE_SEARCH) is True

    assert service.get(track_id) == "words"
    assert written == []


def test_a_refused_retag_does_not_cost_the_caller_the_row(
    service, db, monkeypatch, on_disk,
):
    """On Windows the audio backend holds the playing track's file open — and the
    playing track is exactly the one the mic button targets."""
    from app.services import lyrics_service as module

    track_id = _track_id(db)
    on_disk(track_id)

    def refuse(path, changes, picture_block=None):
        raise PermissionError("the file is open in another process")

    monkeypatch.setattr(module, "update_opus_tags", refuse)

    assert service.save(track_id, "words", SOURCE_SEARCH) is True
    assert service.get(track_id) == "words"


def test_the_row_is_committed_before_the_file_is_touched(
    service, db, monkeypatch, on_disk,
):
    """The invariant behind the write order: a failure leaves the table right
    and the file stale, never a file carrying words the library never heard of."""
    from app.services import lyrics_service as module

    track_id = _track_id(db)
    on_disk(track_id)
    seen: list = []

    def record(path, changes, picture_block=None):
        # By the time the file is written the row must already be readable.
        seen.append(service.get(track_id))

    monkeypatch.setattr(module, "update_opus_tags", record)

    service.save(track_id, "words", SOURCE_SEARCH)

    assert seen == ["words"]


def test_deleting_clears_the_tag_as_well_as_the_row(service, db, written, on_disk):
    track_id = _track_id(db)
    path = on_disk(track_id)
    service.save(track_id, "words", SOURCE_TAG)
    written.clear()

    assert service.delete(track_id) is True

    assert service.get(track_id) is None
    # An empty value removes the comment rather than writing a blank one.
    assert written == [(str(path), {"LYRICS": ""})]


def test_lyrics_the_user_typed_reach_the_file_like_a_search_result_does(
    service, db, written, on_disk,
):
    """`manual` is the third source, and it mirrors for the same reason `search`
    does: only `tag` means "this text is already in that exact file".

    Hand-corrected lyrics are the ones most worth having travel with the audio,
    since they are the only ones that cannot be found again.
    """
    track_id = _track_id(db)
    path = on_disk(track_id)

    service.save(track_id, "[00:01.00]corrected", SOURCE_MANUAL)

    assert service.get(track_id) == "[00:01.00]corrected"
    assert written == [(str(path), {"LYRICS": "[00:01.00]corrected"})]
