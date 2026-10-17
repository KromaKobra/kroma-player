"""The open playlist's cover, resolved where the playlist is.

The playlist header used to be handed its cover by whoever opened it — the grid
tile that was clicked. A playlist reopened from history has no tile behind it,
so the cover now comes from the playlist itself, by the same rule the grid
uses: its own cover if it has one, otherwise its first track's artwork. And
because "first" is an order somebody chose, rearranging the playlist can change
it.
"""
from __future__ import annotations

import pytest

from app.models.playlist import Playlist
from app.models.track import Track
from app.repositories.playlist_repository import PlaylistRepository
from app.services.playlist_service import PlaylistService
from app.viewmodels.playlist_tracks_view_model import PlaylistTracksViewModel


@pytest.fixture
def playlists(qt_app, db, cache, event_bus, library):
    repository = PlaylistRepository(db.session)
    service = PlaylistService(repository, event_bus)
    tracks = db.session.query(Track).order_by(Track.id).limit(3).all()
    for index, track in enumerate(tracks):
        track.cover_hash = f"{index}" * 32
    db.session.commit()
    return service, repository, [t.id for t in tracks], db.session


def _summary(service, playlist_id):
    return next(s for s in service.get_summaries() if s["id"] == playlist_id)


def test_the_first_track_determines_the_cover_even_without_artwork(playlists):
    service, repository, track_ids, session = playlists
    playlist_id = service.create_playlist("Covers")
    session.get(Track, track_ids[0]).cover_hash = None
    session.commit()
    service.add_tracks(playlist_id, track_ids)

    assert service.get_cover_hash(playlist_id) is None
    assert _summary(service, playlist_id)["cover_hash"] is None, (
        "the grid and the header disagree about the same playlist's cover"
    )


def test_a_playlists_own_cover_wins(playlists):
    service, repository, track_ids, session = playlists
    playlist_id = service.create_playlist("Own cover")
    service.add_tracks(playlist_id, track_ids)
    session.get(Playlist, playlist_id).cover_hash = "f" * 32
    session.commit()

    assert service.get_cover_hash(playlist_id) == "f" * 32
    assert _summary(service, playlist_id)["cover_hash"] == "f" * 32


def test_an_empty_or_missing_playlist_has_no_cover(playlists):
    service, repository, track_ids, session = playlists
    playlist_id = service.create_playlist("Empty")

    assert service.get_cover_hash(playlist_id) is None
    assert _summary(service, playlist_id)["cover_hash"] is None
    assert service.get_cover_hash(playlist_id + 1000) is None


def test_the_open_playlist_reports_its_cover_and_whether_it_exists(playlists, cache, event_bus):
    service, repository, track_ids, session = playlists
    playlist_id = service.create_playlist("Open")
    service.add_tracks(playlist_id, track_ids)
    vm = PlaylistTracksViewModel(service, event_bus, cache)

    vm.load(playlist_id)
    assert vm.hasPlaylist
    assert vm.coverHash == "0" * 32

    vm.load(playlist_id + 1000)
    assert not vm.hasPlaylist, "a playlist that is gone must not read as open"
    assert vm.coverHash == ""


def test_reordering_the_open_playlist_moves_its_cover(playlists, cache, event_bus):
    service, repository, track_ids, session = playlists
    playlist_id = service.create_playlist("Reorder")
    service.add_tracks(playlist_id, track_ids)
    vm = PlaylistTracksViewModel(service, event_bus, cache)
    vm.load(playlist_id)
    changes = []
    vm.playlist_changed.connect(lambda: changes.append(vm.coverHash))

    vm.moveTrack(2, 0)

    assert vm.coverHash == "2" * 32
    assert changes == ["2" * 32], "the header was not told its cover changed"


def test_deleting_the_open_playlist_closes_it(playlists, cache, event_bus):
    service, repository, track_ids, session = playlists
    playlist_id = service.create_playlist("Doomed")
    service.add_tracks(playlist_id, track_ids)
    vm = PlaylistTracksViewModel(service, event_bus, cache)
    vm.load(playlist_id)

    service.delete_playlist(playlist_id)

    assert not vm.hasPlaylist
    assert vm.coverHash == ""
