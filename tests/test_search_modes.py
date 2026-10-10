"""Search returning artists and tracks in one ranked list.

The compatibility claim is the important one here and it is tested first: the
default mode returns exactly what search returned before artists existed, so a
GUI written against the older contract cannot be handed a row it has no delegate
for. Mixed results are something a GUI opts into.
"""
from __future__ import annotations

import pytest

from app.events.event_bus import EventBus
from app.library.roles import FIELD_TO_ROLE
from app.library.row_cache import TrackRowCache
from conftest import make_track

from app.models.track import Artist, Track
from app.repositories.album_repository import AlbumRepository
from app.repositories.artist_repository import ArtistRepository
from app.repositories.genre_repository import GenreRepository
from app.repositories.track_repository import TrackRepository
from app.search import ranking
from app.services.album_service import AlbumService
from app.services.artist_service import ArtistService
from app.services.track_service import TrackService
from app.viewmodels.search_view_model import SearchViewModel


@pytest.fixture
def search(tmp_path):
    """A library where one query matches an artist and a track in several ways."""
    from app.database.connection import DatabaseManager

    manager = DatabaseManager.from_path(tmp_path / "tracks.db")
    session = manager.create_session()

    skrillex = Artist(name="Skrillex")
    duo = Artist(name="Skrillex & Diplo")
    nero = Artist(name="Nero")
    session.add_all([skrillex, duo, nero])
    session.flush()
    session.add_all([
        # An exact *track* title match, by a different artist.
        make_track(session, title="Skrillex", album="Tribute", duration_seconds=100, artists=[nero]),
        make_track(session, title="Bangarang", album="Bangarang EP", duration_seconds=215,
              artists=[skrillex]),
        make_track(session, title="Kyoto", album="Bangarang EP", duration_seconds=190,
              artists=[skrillex]),
        make_track(session, title="Take U There", album="Skrillex Mix", duration_seconds=180,
              artists=[duo]),
    ])
    session.commit()

    bus = EventBus()
    track_repo = TrackRepository(session)
    artist_repo = ArtistRepository(session)
    album_repo = AlbumRepository(session)
    track_service = TrackService(track_repo, artist_repo, GenreRepository(session), bus)
    artist_service = ArtistService(artist_repo, bus, track_service=track_service)
    album_service = AlbumService(album_repo, bus, track_service=track_service)

    vm = SearchViewModel(
        track_repo, None, bus, track_service, None, TrackRowCache(track_repo),
        artist_service=artist_service, album_service=album_service,
    )
    yield vm, session
    session.close()


def _kinds(vm):
    return [vm.row_value(i, "rowKind") for i in range(vm.rowCount())]


def _names(vm):
    out = []
    for i in range(vm.rowCount()):
        if vm.row_value(i, "rowKind") == "artist":
            out.append(("artist", vm.row_value(i, "artistName")))
        else:
            out.append(("track", vm.trackIdAt(i)))
    return out


# ---------------------------------------------------------------------------
# The compatibility promise
# ---------------------------------------------------------------------------


def test_the_default_mode_is_tracks_only(search):
    vm, _ = search
    assert vm.searchMode == "tracks"

    vm.text_search("skrillex")
    # Title or album, exactly as quick_search_ids has always answered.
    assert all(vm.trackIdAt(i) for i in range(vm.rowCount()))
    assert vm.rowCount() == 2


def test_the_default_mode_serves_rows_from_the_cache(search):
    """A track row must still miss into the row cache, not answer from a dict.

    If it answered from the (empty) row dict instead, the miss that triggers the
    fetch would never happen and every cell would stay blank forever.
    """
    vm, _ = search
    vm.text_search("bangarang")
    from app.library.row_cache import MISS

    assert vm.local_value(0, "title") is MISS


def test_without_an_artist_service_the_mode_cannot_produce_artist_rows(search, tmp_path):
    """Belt and braces: a mixed mode with nothing to search for artists in
    degrades to tracks rather than failing."""
    vm, _ = search
    vm._artist_service = None
    vm.searchMode = "best"
    vm.text_search("skrillex")
    assert all(k != "artist" for k in _kinds(vm))


# ---------------------------------------------------------------------------
# Interleaving
# ---------------------------------------------------------------------------


def test_best_match_interleaves_by_relevance_not_by_kind(search):
    vm, _ = search
    vm.searchMode = "best"
    vm.text_search("skrillex")

    kinds = _kinds(vm)
    # An exact artist name outranks an exact track title, which outranks an
    # artist matched only on a prefix. Kind alone never decides.
    assert kinds[:3] == ["artist", "track", "artist"]
    assert vm.row_value(0, "artistName") == "Skrillex"
    assert vm.row_value(2, "artistName") == "Skrillex & Diplo"


def test_artist_rows_carry_their_own_values(search):
    vm, _ = search
    vm.searchMode = "best"
    vm.text_search("skrillex")

    assert vm.row_value(0, "artistId") > 0
    assert vm.row_value(0, "artistTrackCount") == 2
    # Not a library track, and it says so rather than claiming id 0.
    assert vm.row_value(0, "trackId") is None


def test_artist_rows_mirror_onto_title_for_a_gui_that_has_not_learned_rowkind(search):
    vm, _ = search
    vm.searchMode = "best"
    vm.text_search("skrillex")
    assert vm.data(vm.index(0, 0), FIELD_TO_ROLE["title"]) == "Skrillex"


def test_artists_mode_returns_only_artists(search):
    vm, _ = search
    vm.searchMode = "artists"
    vm.text_search("skrillex")

    assert _kinds(vm) == ["artist", "artist"]
    assert vm.get_result_ids() == []


def test_play_all_skips_artist_rows(search):
    vm, _ = search
    vm.searchMode = "best"
    vm.text_search("skrillex")

    ids = vm.get_result_ids()
    assert ids and all(i > 0 for i in ids)
    assert len(ids) < vm.rowCount()      # some rows were artists
    assert vm.trackIds() == ids


def test_changing_the_mode_re_runs_the_current_query(search):
    vm, _ = search
    vm.text_search("skrillex")
    before = vm.rowCount()

    vm.searchMode = "best"
    assert vm.rowCount() > before
    assert "artist" in _kinds(vm)


def test_switching_back_to_tracks_drops_the_artist_rows(search):
    vm, _ = search
    vm.searchMode = "best"
    vm.text_search("skrillex")
    vm.searchMode = "tracks"

    assert all(vm.trackIdAt(i) for i in range(vm.rowCount()))


def test_an_unknown_mode_is_ignored(search):
    vm, _ = search
    vm.searchMode = "sideways"
    assert vm.searchMode == "tracks"


# ---------------------------------------------------------------------------
# Albums, which are their own mode and only their own mode
# ---------------------------------------------------------------------------


def test_albums_mode_returns_only_albums(search):
    vm, _ = search
    vm.searchMode = "albums"
    vm.text_search("skrillex")

    assert vm.rowCount() > 0
    assert set(_kinds(vm)) == {"album"}
    # An album is a route to a page, not a track to queue, so "Play all" over a
    # page of them correctly has nothing to play.
    assert vm.get_result_ids() == []
    assert vm.trackIds() == []


def test_albums_never_join_the_mixed_list(search):
    """The design decision, pinned where someone would otherwise "fix" it.

    "Skrillex Mix" matches this query as an album, and must not appear in
    `best` — that list ranks answers to "which recording did you mean", and an
    album is a different question.
    """
    vm, _ = search
    vm.searchMode = "best"
    vm.text_search("skrillex")

    assert "album" not in _kinds(vm)


def test_album_rows_carry_their_own_values(search):
    vm, _ = search
    vm.searchMode = "albums"
    vm.text_search("bangarang")

    assert vm.row_value(0, "albumName") == "Bangarang EP"
    assert vm.row_value(0, "albumId") > 0
    assert vm.row_value(0, "albumTrackCount") == 2
    # Not a library track, and it says so rather than claiming id 0.
    assert vm.row_value(0, "trackId") is None


def test_album_rows_are_served_from_the_row_rather_than_the_cache(search):
    """The membership test in `local_value`, which fails silently when wrong.

    A row kind missing from it is sent to the track row cache, which has no
    `tracks` row to answer with — so every cell comes back blank and stays
    blank, with nothing logged and nothing raised.
    """
    from app.library.row_cache import MISS

    vm, _ = search
    vm.searchMode = "albums"
    vm.text_search("bangarang")

    assert vm.local_value(0, "title") == "Bangarang EP"
    assert vm.local_value(0, "title") is not MISS


def test_album_rows_mirror_onto_title_for_a_gui_that_has_not_learned_rowkind(search):
    vm, _ = search
    vm.searchMode = "albums"
    vm.text_search("bangarang")

    assert vm.data(vm.index(0, 0), FIELD_TO_ROLE["title"]) == "Bangarang EP"


def test_without_an_album_service_the_mode_falls_back_to_tracks(search, tmp_path):
    """The same compatibility rule the artist modes follow, not an exception."""
    vm, session = search
    bare = SearchViewModel(
        TrackRepository(session), None, EventBus(),
        vm.track_service, None,
        TrackRowCache(TrackRepository(session)),
    )
    bare.searchMode = "albums"
    bare.text_search("bangarang")

    assert bare.searchMode == "albums"
    assert "album" not in _kinds(bare)
    assert all(bare.trackIdAt(i) for i in range(bare.rowCount()))


# ---------------------------------------------------------------------------
# The ranking itself
# ---------------------------------------------------------------------------


def test_score_bands():
    assert ranking.score_artist("Skrillex", "skrillex") == ranking.SCORE_ARTIST_EXACT
    assert ranking.score_artist("Skrillex & Diplo", "skrillex") == ranking.SCORE_ARTIST_PREFIX
    assert ranking.score_artist("The Skrillex Show", "skrillex") == ranking.SCORE_ARTIST_CONTAINS
    assert ranking.score_artist("Nero", "skrillex") == 0

    assert ranking.score_track("Kyoto", "", "kyoto") == ranking.SCORE_TRACK_EXACT
    assert ranking.score_track("Kyoto Nights", "", "kyoto") == ranking.SCORE_TRACK_PREFIX
    # An album match is the weakest signal — it says the same about every other
    # track on the record.
    assert ranking.score_track("Anything", "Kyoto EP", "kyoto") == ranking.SCORE_ALBUM_CONTAINS


def test_ties_between_artists_break_on_catalogue_size():
    rows = ranking.rank(
        artists=[
            {"id": 1, "name": "Nero", "image_hash": "", "track_count": 1},
            {"id": 2, "name": "Nero", "image_hash": "", "track_count": 9},
        ],
        tracks=[],
        query="nero",
    )
    assert [r["artist"]["id"] for r in rows] == [2, 1]


def test_ranking_never_drops_a_row():
    """"Play all" over the results has to mean all of them."""
    artists = [
        {"id": i, "name": f"Artist {i}", "image_hash": "", "track_count": i}
        for i in range(50)
    ]
    tracks = [(i, f"Track {i}", "Album") for i in range(1, 400)]
    rows = ranking.rank(artists, tracks, "a")
    assert len(rows) == len(artists) + len(tracks)


def test_ranking_tolerates_identical_rows():
    """Sorting must go through a key: two artists can tie on both score and
    count, and comparing the tuples would fall through to the dicts and raise."""
    same = [
        {"id": 1, "name": "Same", "image_hash": "", "track_count": 3},
        {"id": 2, "name": "Same", "image_hash": "", "track_count": 3},
    ]
    assert len(ranking.rank(same, [(1, "Same", "")], "same")) == 3
