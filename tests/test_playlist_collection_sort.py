"""Playlist collection sorting keeps its order through membership and name edits."""
import pytest

from app.models.playlist import Playlist
from app.models.track import Track
from app.repositories.playlist_repository import PlaylistRepository
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
    first = service.create_playlist("First")
    second = service.create_playlist("Second")
    assert [row["playlistId"] for row in model.playlistList()
            if row["playlistId"] in {first, second}] == [second, first]
