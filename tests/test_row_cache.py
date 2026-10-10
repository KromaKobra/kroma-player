"""Tests for the shared row cache."""
from __future__ import annotations

from app.library.fields import Tier
from app.library.row_cache import MISS, TrackRowCache

from conftest import TRACK_COUNT


def test_get_misses_before_any_fetch(cache):
    assert cache.get(1, "title") is MISS
    assert len(cache) == 0


def test_fetch_loads_the_whole_scalar_tier_at_once(cache, queries):
    queries.reset()
    cache.fetch([1, 2, 3], {"title"})

    assert len(queries) == 1, "every scalar field costs the same one SELECT"
    assert cache.get(1, "title") == "Track 00000"
    assert cache.get(1, "bpm") == 60, "asking for title also brought bpm"
    assert cache.get(1, "coverHash") == f"{0:032x}"


def test_scalar_and_relationship_tiers_are_separate(cache, queries):
    cache.fetch([1], {"title"})
    assert cache.get(1, "artist") is MISS, "artist costs a join, so it is not in the scalar tier"

    queries.reset()
    cache.fetch([1], {"artist"})
    assert len(queries) == 1
    assert cache.get(1, "artist") == "Artist 000"
    assert cache.get(1, "genres") is MISS, "genres is its own tier again"


def test_refetching_a_warm_tier_costs_nothing(cache, queries):
    # Two tiers, because `album` stopped being a `tracks` column when albums
    # became rows: it needs the track_albums join now, and warming `title`
    # alone no longer warms it.
    cache.fetch([1, 2, 3], {"title", "album"})
    queries.reset()
    cache.fetch([1, 2, 3], {"title", "album", "duration"})
    assert len(queries) == 0


def test_fetch_returns_only_the_ids_it_updated(cache):
    assert cache.fetch([1, 2, 3], {"title"}) == {1, 2, 3}
    assert cache.fetch([1, 2, 3], {"title"}) == set(), "already warm"
    assert cache.fetch([3, 4], {"title"}) == {4}


def test_null_column_is_a_value_not_a_miss(db, repo, cache):
    """A NULL column and an unread column must be distinguishable.

    One means "draw a blank", the other means "ask again" — a list model that
    confuses them either loops forever or shows permanently empty cells.
    """
    from app.models.track import Track

    session = db.session
    track = Track(title="No isrc", duration_seconds=10)
    session.add(track)
    session.commit()

    cache.fetch([track.id], {"isrc"})
    assert cache.get(track.id, "isrc") is None
    assert cache.get(track.id, "isrc") is not MISS


def test_a_track_on_no_album_is_fetched_and_empty(db, repo, cache):
    """The joined tiers say "" where a column says None, and both beat MISS.

    ``fetch_albums`` follows the rule ``fetch_artists`` set: a track with no
    rows on the other side of the join comes back present and empty rather than
    absent, so the cache can tell "asked, and there is nothing" from "not asked
    yet". Only the spelling of "nothing" differs between a column and a join.
    """
    from app.models.track import Track

    session = db.session
    track = Track(title="No album", duration_seconds=10)
    session.add(track)
    session.commit()

    cache.fetch([track.id], {"album"})
    assert cache.get(track.id, "album") == ""
    assert cache.get(track.id, "album") is not MISS


def test_in_clause_is_chunked_beyond_the_sqlite_parameter_limit(repo, cache):
    """1200 ids exceeds SQLite's 999-parameter default; the fetch must still work."""
    ids = repo.query_ids()
    assert len(ids) == TRACK_COUNT

    cache.fetch(ids, {"title", "artist", "genres"})
    assert cache.get(ids[0], "title") == "Track 00000"
    assert cache.get(ids[-1], "title") == f"Track {TRACK_COUNT - 1:05d}"
    assert cache.get(ids[-1], "artist") == f"Artist {(TRACK_COUNT - 1) % 40:03d}"


def test_lru_evicts_oldest_rows(repo):
    small = TrackRowCache(repo, capacity=100)
    ids = repo.query_ids()[:300]
    small.fetch(ids, {"title"})

    assert len(small) == 100
    assert small.get(ids[0], "title") is MISS, "oldest rows evicted"
    assert small.get(ids[-1], "title") is not MISS, "newest rows retained"


def test_reading_a_row_keeps_it_alive(repo):
    small = TrackRowCache(repo, capacity=150)
    ids = repo.query_ids()[:100]
    small.fetch(ids, {"title"})

    small.get(ids[0], "title")           # touch the oldest row
    small.fetch(repo.query_ids()[100:200], {"title"})

    assert small.get(ids[0], "title") is not MISS, "a touched row survives eviction"


def test_invalidate_drops_one_track(cache):
    cache.fetch([1, 2], {"title"})
    cache.invalidate(1)

    assert cache.get(1, "title") is MISS
    assert cache.get(2, "title") is not MISS


def test_has_tier_reports_load_state(cache):
    assert cache.has_tier(1, Tier.SCALAR) is False
    cache.fetch([1], {"title"})
    assert cache.has_tier(1, Tier.SCALAR) is True
    assert cache.has_tier(1, Tier.ARTISTS) is False


def test_missing_track_does_not_get_cached(cache):
    cache.fetch([999_999], {"title"})
    assert cache.get(999_999, "title") is MISS
    assert cache.has_tier(999_999, Tier.SCALAR) is False


def test_tracks_without_relationships_resolve_to_empty(db, cache):
    """A track with no artists must come back as "" rather than never resolving."""
    from app.models.track import Track

    session = db.session
    track = Track(title="Orphan", duration_seconds=10)
    session.add(track)
    session.commit()

    cache.fetch([track.id], {"artist"})
    assert cache.get(track.id, "artist") == ""
    assert cache.has_tier(track.id, Tier.ARTISTS) is True
