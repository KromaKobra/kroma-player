"""Collection summaries use the same credit rules as album details, in bounded queries."""
import pytest
from sqlalchemy import event

from test_album_contributors import credits  # noqa: F401
from test_artists import artists  # noqa: F401
from test_playlist_collection_sort import playlists  # noqa: F401
from app.events.event_bus import EventBus
from app.repositories.collection_summaries import album_metadata
from app.viewmodels.albums_view_model import AlbumsViewModel, AlbumRoles
from app.viewmodels.artists_view_model import ArtistsViewModel, ArtistRoles


def test_album_summary_majority_fallback_empty_and_dates(credits):
    session = credits["session"]
    credits["one"].duration_seconds = 90
    credits["two"].duration_seconds = 100
    credits["one"].release_date = None
    credits["two"].release_date = 20241001
    credits["three"].release_date = 20200101
    session.commit()
    rows = credits["service"].list_all()
    album_metadata(session, rows)
    by_id = {row["id"]: row for row in rows}
    assert by_id[credits["mixtape"].id]["primary_artists"] == "Ada"
    assert by_id[credits["single"].id]["primary_artists"] == "Zed, Yan"
    assert by_id[credits["empty"].id]["primary_artists"] == ""
    assert by_id[credits["empty"].id]["total_duration"] == 0
    assert by_id[credits["mixtape"].id]["release_date"] == 20241001
    expected = sum(track.duration_seconds or 0 for track in (
        credits["one"], credits["two"], credits["three"], credits["four"], credits["guest"]))
    assert by_id[credits["mixtape"].id]["total_duration"] == expected


def test_album_summary_uses_two_queries_regardless_of_album_count(credits):
    rows = credits["service"].list_all()
    statements = []
    def record(*args): statements.append(args[2])
    event.listen(credits["session"].bind, "before_cursor_execute", record)
    try:
        album_metadata(credits["session"], rows)
    finally:
        event.remove(credits["session"].bind, "before_cursor_execute", record)
    assert len(statements) == 2


def test_collection_selection_survives_sort_and_refresh(credits):
    model = AlbumsViewModel(credits["service"], EventBus())
    selected = [credits["mixtape"].id, credits["single"].id]
    model.setSelectedMultiple(selected)
    model.sortField = "totalDuration"
    model.sortAscending = True
    model.refresh()
    assert set(model.selectedUids()) == set(selected)
    row = next(i for i in range(model.rowCount()) if model.uidAt(i) == credits["mixtape"].id)
    assert model.data(model.index(row), AlbumRoles.PrimaryArtists) == "Ada"
    assert model.data(model.index(row), AlbumRoles.TotalDuration) > 0


def test_artist_total_time_and_empty_artist(artists):
    service, _, _, rows = artists
    model = ArtistsViewModel(service, EventBus())
    model.sortField = "totalDuration"
    model.sortAscending = False
    index = next(i for i in range(model.rowCount()) if model.uidAt(i) == rows["Skrillex"].id)
    assert model.data(model.index(index), ArtistRoles.TotalDuration) == 405
    index = next(i for i in range(model.rowCount()) if model.uidAt(i) == rows["Orphan"].id)
    assert model.data(model.index(index), ArtistRoles.TotalDuration) == 0


def test_playlist_batch_order_overlap_and_selection_pruning(playlists):
    model, service, ids, tracks = playlists
    model.sortField = "name"
    model.sortAscending = True
    entries = model.playbackEntries(list(reversed(ids)))
    assert [entry["id"] for entry in entries] == tracks
    assert all(entry["sourceType"] == "playlist" and entry["sourceId"] == ids[1] for entry in entries)
    model.setSelectedMultiple(ids)
    service.delete_playlist(ids[0])
    assert set(model.selectedUids()) == set(ids[1:])
    assert model.playbackEntries([ids[0], -1]) == []
