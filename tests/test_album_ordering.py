"""An album's running order, and the drag that rearranges it.

Modelled on tests/test_playlist_ordering.py, because the two models are the same
model: `AlbumTracksViewModel` is a copy of `PlaylistTracksViewModel` down to the
two-phase walk, and both exist because `TrackTable.qml` calls `moveRows` and
`removeAtRows` on its list model by name.

The assertion worth having twice is the write count. The walk moves a row up to
twice per gesture and each of those is an in-memory list operation; if any of
them reached the database a fifteen-track album would cost thirty round trips
for one drag.
"""
from __future__ import annotations

import pytest

from conftest import QueryCounter

from app.database.connection import DatabaseManager
from app.events.event_bus import EventBus
from app.library.row_cache import TrackRowCache
from app.models.track import Album, Artist, Track
from app.repositories.album_repository import AlbumRepository
from app.repositories.track_repository import TrackRepository
from app.services.album_service import AlbumService
from app.viewmodels.album_tracks_view_model import AlbumTracksViewModel


@pytest.fixture
def album_vm(tmp_path, qt_app):
    manager = DatabaseManager.from_path(tmp_path / "tracks.db")
    session = manager.create_session()

    artist = Artist(name="Someone")
    album = Album(name="A Record")
    session.add_all([artist, album])
    session.flush()

    for i in range(6):
        track = Track(title=f"Track {i}", duration_seconds=100, artists=[artist])
        track.albums = [album]
        session.add(track)
    session.commit()

    track_repo = TrackRepository(session)
    repo = AlbumRepository(session)
    bus = EventBus()
    service = AlbumService(repo, bus)
    repo.set_order(int(album.id), repo.track_ids(int(album.id)))

    vm = AlbumTracksViewModel(service, bus, TrackRowCache(track_repo))
    vm.load(int(album.id))
    counter = QueryCounter(manager.engine)
    yield {"vm": vm, "repo": repo, "service": service, "album": album,
           "session": session, "bus": bus, "sql": counter}
    counter.detach()
    session.close()


def _titles(state):
    session = state["session"]
    return [
        session.get(Track, t).title
        for t in state["repo"].track_ids(int(state["album"].id))
    ]


def test_the_fixture_starts_in_order(album_vm):
    assert _titles(album_vm) == [f"Track {i}" for i in range(6)]


def test_set_order_renumbers_densely(album_vm):
    repo, album = album_vm["repo"], album_vm["album"]
    ids = repo.track_ids(int(album.id))
    repo.set_order(int(album.id), list(reversed(ids)))

    rows = repo.cover_rows(int(album.id))
    assert [r["position"] for r in rows] == [0, 1, 2, 3, 4, 5]
    assert repo.track_ids(int(album.id)) == list(reversed(ids))


def test_set_order_is_a_no_op_when_nothing_moved(album_vm):
    repo, album = album_vm["repo"], album_vm["album"]
    assert repo.set_order(int(album.id), repo.track_ids(int(album.id))) is False


def test_set_order_ignores_ids_that_are_not_on_the_album(album_vm):
    """A stale list narrows what gets renumbered rather than dropping tracks."""
    repo, album = album_vm["repo"], album_vm["album"]
    ids = repo.track_ids(int(album.id))
    repo.set_order(int(album.id), [ids[3], 9999])

    assert repo.track_ids(int(album.id))[0] == ids[3]
    assert len(repo.track_ids(int(album.id))) == 6


def test_move_rows_sends_one_row_down(album_vm):
    album_vm["vm"].moveRows([0], 3)
    assert _titles(album_vm) == [
        "Track 1", "Track 2", "Track 3", "Track 0", "Track 4", "Track 5"
    ]


def test_move_rows_sends_one_row_up(album_vm):
    album_vm["vm"].moveRows([4], 1)
    assert _titles(album_vm) == [
        "Track 0", "Track 4", "Track 1", "Track 2", "Track 3", "Track 5"
    ]


def test_move_rows_carries_a_block_in_its_own_order(album_vm):
    """A non-contiguous selection lands contiguously, keeping its own order."""
    album_vm["vm"].moveRows([0, 2, 4], 2)
    assert _titles(album_vm) == [
        "Track 1", "Track 3", "Track 0", "Track 2", "Track 4", "Track 5"
    ]


def test_a_drag_costs_exactly_one_write(album_vm):
    """The walk is in memory; only the order it stops at reaches SQLite."""
    sql = album_vm["sql"]
    sql.reset()
    album_vm["vm"].moveRows([0, 1], 3)
    updates = [q for q in sql.statements if "UPDATE track_albums" in q]
    # Six rows renumbered, but one `set_order` call and one transaction.
    assert len(updates) == 6
    assert _titles(album_vm) == [
        "Track 2", "Track 3", "Track 4", "Track 0", "Track 1", "Track 5"
    ]


def test_a_move_that_changes_nothing_writes_nothing(album_vm):
    sql = album_vm["sql"]
    sql.reset()
    album_vm["vm"].moveRows([0], 0)
    assert [q for q in sql.statements if "UPDATE track_albums" in q] == []


def test_remove_at_rows_takes_tracks_off_the_album_not_the_library(album_vm):
    state = album_vm
    ids = state["repo"].track_ids(int(state["album"].id))
    state["vm"].removeAtRows([1, 2])

    assert _titles(state) == ["Track 0", "Track 3", "Track 4", "Track 5"]
    # Still in the library, just not on this record.
    assert state["session"].get(Track, ids[1]) is not None
    # And the album renumbered behind them.
    rows = state["repo"].cover_rows(int(state["album"].id))
    assert [r["position"] for r in rows] == [0, 1, 2, 3]


def test_the_view_model_exposes_the_two_names_the_drag_looks_for(album_vm):
    """TrackTable.qml resolves both by name before it arms the gesture."""
    vm = album_vm["vm"]
    assert callable(getattr(vm, "moveRows", None))
    assert callable(getattr(vm, "removeAtRows", None))


def test_a_reorder_elsewhere_reaches_the_open_album(album_vm):
    state = album_vm
    ids = state["repo"].track_ids(int(state["album"].id))
    state["service"].set_order(int(state["album"].id), list(reversed(ids)))
    assert state["vm"].trackIds() == list(reversed(ids))
