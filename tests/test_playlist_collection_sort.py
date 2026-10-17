"""Playlist collection sorting keeps its order through membership and name edits."""
import pytest

from app.models.playlist import Playlist
from app.models.track import Track
from app.repositories.playlist_repository import PlaylistRepository
from app.repositories.track_repository import TrackRepository
from app.services.playlist_service import PlaylistService
from app.viewmodels.playlists_view_model import PlaylistsViewModel


@pytest.fixture
def playlists(db, event_bus):
    service = PlaylistService(PlaylistRepository(db.session), event_bus)
    ids = [service.create_playlist(name) for name in ("zebra", "Alpha", "Middle")]
    for playlist_id, date in zip(ids, (20261001, 20261003, 20261002)):
        db.session.get(Playlist, playlist_id).created_at = date
    db.session.commit()
    tracks = [row.id for row in db.session.query(Track).order_by(Track.id).limit(3)]
    service.add_tracks(ids[0], tracks[:1])
    service.add_tracks(ids[1], tracks[:3])
    service.add_tracks(ids[2], tracks[:2])
    return PlaylistsViewModel(service, event_bus), service, ids, tracks


@pytest.mark.parametrize("field,ascending,order", [
    ("dateAdded", False, [1, 2, 0]),
    ("dateAdded", True, [0, 2, 1]),
    ("name", True, [1, 2, 0]),
    ("name", False, [0, 2, 1]),
    ("trackCount", False, [1, 2, 0]),
    ("trackCount", True, [0, 2, 1]),
])
def test_playlist_sort_fields_and_directions(playlists, field, ascending, order):
    model, _, ids, _ = playlists
    model.sortField = field
    model.sortAscending = ascending
    assert [row["playlistId"] for row in model.playlistList()] == [ids[i] for i in order]


def test_playlist_mutations_keep_the_chosen_sort(playlists):
    model, service, ids, tracks = playlists
    model.sortField = "name"
    model.sortAscending = True
    service.rename_playlist(ids[0], "aardvark")
    assert model.playlistList()[0]["playlistId"] == ids[0]
    model.sortField = "trackCount"
    service.add_tracks(ids[0], tracks[1:])
    assert model.playlistList()[0]["playlistId"] == ids[2]
    assert model.sortField == "trackCount"
    assert model.sortAscending


def test_date_sort_breaks_same_day_ties_by_creation_order(playlists):
    model, service, _, _ = playlists
    model.sortField = "dateAdded"
    first = service.create_playlist("First")
    second = service.create_playlist("Second")
    assert [row["playlistId"] for row in model.playlistList()
            if row["playlistId"] in {first, second}] == [second, first]


@pytest.mark.parametrize("mutation", [
    "create", "rename", "add", "add_many", "remove", "remove_many", "reorder",
])
def test_recently_modified_moves_each_edit_to_the_top(playlists, mutation, db, event_bus):
    model, service, ids, tracks = playlists
    model.sortField = "recentlyModified"
    model.sortAscending = False
    target = ids[1] if mutation == "reorder" else ids[0]
    before = service.get_playlist(target).updated_at
    created_at = service.get_playlist(target).created_at
    model.setSelectedExclusive(target)

    if mutation == "create":
        target = service.create_playlist("New playlist")
    elif mutation == "rename":
        service.rename_playlist(target, "Renamed")
    elif mutation == "add":
        service.add_track(target, tracks[1])
    elif mutation == "add_many":
        service.add_tracks(target, tracks[1:])
    elif mutation == "remove":
        service.remove_track(target, tracks[0])
    elif mutation == "remove_many":
        service.remove_tracks(target, tracks)
    else:
        service.set_order(target, list(reversed(tracks)))

    assert model.uidAt(0) == target
    assert model.sortField == "recentlyModified"
    assert not model.sortAscending
    if mutation != "create":
        assert service.get_playlist(target).updated_at > before
        assert service.get_playlist(target).created_at == created_at
        assert model.selectedUids() == [target]

    # Read through a fresh database session: the order must survive reopening.
    with db.manager.create_session() as session:
        reopened = PlaylistsViewModel(PlaylistService(PlaylistRepository(session), event_bus), event_bus)
        reopened.sortField = "recentlyModified"
        assert reopened.uidAt(0) == target
        reopened.sortAscending = True
        assert reopened.uidAt(reopened.rowCount() - 1) == target


def test_recently_modified_ignores_no_op_edits(playlists):
    model, service, ids, tracks = playlists
    model.sortField = "recentlyModified"
    before_order = model.playlistList()
    before = service.get_playlist(ids[0]).updated_at
    service.add_track(ids[0], tracks[0])
    service.add_tracks(ids[0], [tracks[0]])
    service.remove_track(ids[0], tracks[1])
    service.remove_tracks(ids[0], tracks[1:])
    service.set_order(ids[0], [tracks[0]])
    service.rename_playlist(ids[0], " zebra ")
    service.rename_playlist(ids[0], " ")
    assert service.get_playlist(ids[0]).updated_at == before
    assert model.playlistList() == before_order


def test_recently_modified_orders_rapid_edits_even_when_clock_repeats(playlists, monkeypatch):
    model, service, ids, _ = playlists
    model.sortField = "recentlyModified"
    # Also covers a clock correction backwards and edits to older (lower-id) lists.
    monkeypatch.setattr("app.repositories.playlist_repository._now_us", lambda: 1)
    service.rename_playlist(ids[1], "First edit")
    service.rename_playlist(ids[0], "Second edit")
    assert [row["playlistId"] for row in model.playlistList()] == [ids[0], ids[1], ids[2]]
    newest = service.create_playlist("Third edit")
    assert model.uidAt(0) == newest


def test_library_track_deletion_marks_only_the_playlists_it_changes(playlists, db, event_bus):
    model, service, ids, tracks = playlists
    model.sortField = "recentlyModified"
    # The third track belongs only to ids[1]. The untouched playlists keep
    # their relative order and timestamps when that track leaves the library.
    before = {row["id"]: row["updated_at"] for row in service.get_summaries()}
    assert TrackRepository(db.session).delete(tracks[2])
    event_bus.emit("track.deleted", tracks[2])
    assert [row["playlistId"] for row in model.playlistList()] == [ids[1], ids[2], ids[0]]
    after = {row["id"]: row["updated_at"] for row in service.get_summaries()}
    assert after[ids[1]] > max(before.values())
    assert after[ids[0]] == before[ids[0]]
    assert after[ids[2]] == before[ids[2]]
    assert tracks[2] not in service.get_track_ids(ids[1])
