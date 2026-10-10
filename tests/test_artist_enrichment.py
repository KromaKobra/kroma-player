"""Artists arrive from a fetch or an import already identified and pictured.

The whole design rests on one claim: this costs a single query per track, and one
artwork question plus one image download per artist the library has never seen a
picture for. If the skip does not hold, a 200-track import makes 200 network
round trips for 30 artists and the feature is worse than not having it.

So most of what is asserted here is *absence* — that the fake artwork provider
was not asked. A call-counting stand-in is the only way to test that, which is
why the fakes below record rather than just answer.

The other rule is that enrichment never fails a track. It is a bonus on a path
whose job is getting the audio in, so a dead image server or a broken plugin has
to come out the far side as a track with no portrait, not as a failed import.
"""
from __future__ import annotations

import pytest

from app.events.event_bus import EventBus
from app.importer.artist_enrichment import enrich_artist_refs
from app.models.track import Artist
from app.repositories.artist_repository import ArtistRepository
from app.repositories.genre_repository import GenreRepository
from app.repositories.track_repository import TrackRepository
from app.services.track_service import TrackService
from tests.fakes.providers import CHANNEL as KEY, FakeArtwork, FakeRegistry, held_id, id_rows

CHANNEL = "UCfakeChannel0000000001A"
OTHER_CHANNEL = "UCfakeChannel0000000002B"
PORTRAIT = "p" * 32


class CountingArtwork(FakeArtwork):
    """An artwork provider that records every artist id it was asked about."""

    def __init__(self, image_url="https://img.invalid/p.jpg"):
        super().__init__()
        self.image_url = image_url
        self.asked: list[str] = []

    def get(self, entity, ids, output_dir, *, should_stop=None):
        self.asked.append(ids.get(KEY))
        return {"urls": [self.image_url]}


class CountingCovers:
    def __init__(self):
        self.ingested: list[bytes] = []

    def ingest_source(self, data):
        self.ingested.append(data)
        return (PORTRAIT, b"master")


@pytest.fixture
def library(tmp_path):
    from app.database.connection import DatabaseManager

    manager = DatabaseManager.from_path(tmp_path / "tracks.db")
    session = manager.create_session()
    bus = EventBus()
    artist_repo = ArtistRepository(session)
    track_service = TrackService(
        TrackRepository(session), artist_repo, GenreRepository(session), bus
    )
    yield track_service, artist_repo, session
    session.close()


@pytest.fixture(autouse=True)
def bytes_without_a_socket(monkeypatch):
    monkeypatch.setattr(
        "app.importer.stash.fetch_bytes", lambda url, **kw: b"imagebytes"
    )


def _enrich(refs, track_service, artwork, covers=None, **kw):
    return enrich_artist_refs(
        refs,
        track_service=track_service,
        registry=FakeRegistry(artwork=artwork) if artwork is not None else None,
        cover_service=covers if covers is not None else CountingCovers(),
        **kw,
    )


# ---------------------------------------------------------------------------
# The fetch
# ---------------------------------------------------------------------------

def test_a_new_artist_gets_a_portrait(library):
    track_service, _repo, _session = library
    engine = CountingArtwork()
    covers = CountingCovers()

    refs = _enrich(
        [{"name": "Seven Lions", "external_ids": {KEY: CHANNEL}}], track_service, engine, covers
    )

    assert engine.asked == [CHANNEL]
    assert refs == [
        {"name": "Seven Lions", "external_ids": {KEY: CHANNEL}, "image_hash": PORTRAIT}
    ]
    assert len(covers.ingested) == 1


def test_the_refs_that_come_back_are_in_the_order_they_arrived(library):
    track_service, _repo, _session = library
    refs = _enrich(
        [
            {"name": "Seven Lions", "external_ids": {KEY: CHANNEL}},
            {"name": "Jason Ross", "external_ids": {}},
        ],
        track_service,
        CountingArtwork(),
    )
    assert [r["name"] for r in refs] == ["Seven Lions", "Jason Ross"]


def test_a_ref_with_no_channel_id_is_left_exactly_as_it_arrived(library):
    """No search-by-name fallback. Guessing a channel from a name during a bulk
    import is how a wrong id gets written, and nothing ever overwrites one."""
    track_service, _repo, _session = library
    engine = CountingArtwork()

    refs = _enrich([{"name": "A Friend", "external_ids": {}}], track_service, engine)

    assert engine.asked == []
    assert refs == [{"name": "A Friend", "external_ids": {}}]


def test_a_channel_id_already_on_the_row_is_used_when_the_ref_has_none(library):
    """An artist identified by an earlier import but never pictured still wants
    a picture — the gap is the portrait, not the identity."""
    track_service, _repo, session = library
    session.add(Artist(name="Seven Lions", external_id_rows=id_rows("artist", {KEY: CHANNEL})))
    session.commit()
    engine = CountingArtwork()

    refs = _enrich([{"name": "Seven Lions", "external_ids": {}}], track_service, engine)

    assert engine.asked == [CHANNEL]
    assert refs[0]["image_hash"] == PORTRAIT


# ---------------------------------------------------------------------------
# The skip — what the cost argument rests on
# ---------------------------------------------------------------------------

def test_an_artist_that_is_identified_and_pictured_costs_no_network(library):
    track_service, _repo, session = library
    session.add(Artist(name="Seven Lions", external_id_rows=id_rows("artist", {KEY: CHANNEL}), image_hash=PORTRAIT))
    session.commit()
    engine = CountingArtwork()
    covers = CountingCovers()

    refs = _enrich(
        [{"name": "Seven Lions", "external_ids": {KEY: CHANNEL}}], track_service, engine, covers
    )

    assert engine.asked == []
    assert covers.ingested == []
    assert refs == [{"name": "Seven Lions", "external_ids": {KEY: CHANNEL}}]


def test_the_skip_is_case_insensitive(library):
    track_service, _repo, session = library
    session.add(Artist(name="Seven Lions", external_id_rows=id_rows("artist", {KEY: CHANNEL}), image_hash=PORTRAIT))
    session.commit()
    engine = CountingArtwork()

    _enrich([{"name": "seven lions", "external_ids": {KEY: CHANNEL}}], track_service, engine)

    assert engine.asked == []


def test_the_second_track_by_the_same_artist_is_free(library):
    """The claim the whole cost argument makes. The database row is the memo, so
    it survives a restart and there is no cache to go stale."""
    track_service, _repo, _session = library
    engine = CountingArtwork()
    ref = {"name": "Seven Lions", "external_ids": {KEY: CHANNEL}}

    first = _enrich([dict(ref)], track_service, engine)
    # What the first track's write would have done.
    track_service.create_track_with_relationships({
        "title": "Foolish Of Me", "duration_seconds": 187, "artist_refs": first,
    })

    _enrich([dict(ref)], track_service, engine)

    assert engine.asked == [CHANNEL], "the portrait was fetched twice"


def test_an_artist_with_a_picture_but_no_id_keeps_both_and_asks_nothing(library):
    track_service, _repo, session = library
    session.add(Artist(name="Seven Lions", image_hash=PORTRAIT))
    session.commit()
    engine = CountingArtwork()

    refs = _enrich([{"name": "Seven Lions", "external_ids": {KEY: CHANNEL}}], track_service, engine)

    assert engine.asked == []
    assert refs == [{"name": "Seven Lions", "external_ids": {KEY: CHANNEL}}]


# ---------------------------------------------------------------------------
# Never failing a track
# ---------------------------------------------------------------------------

def test_no_plugins_disables_enrichment_entirely(library):
    """What keeps every headless caller and every existing test unchanged."""
    track_service, _repo, _session = library
    refs = [{"name": "Seven Lions", "external_ids": {KEY: CHANNEL}}]
    assert _enrich(refs, track_service, None) is refs


def test_no_cover_service_disables_enrichment_entirely(library):
    track_service, _repo, _session = library
    refs = [{"name": "Seven Lions", "external_ids": {KEY: CHANNEL}}]
    assert enrich_artist_refs(
        refs, track_service=track_service,
        registry=FakeRegistry(artwork=CountingArtwork()), cover_service=None,
    ) is refs


def test_no_artist_artwork_provider_disables_enrichment(library):
    """A plugin that identifies but has no pictures costs nothing to ask."""
    track_service, _repo, _session = library
    refs = [{"name": "Seven Lions", "external_ids": {KEY: CHANNEL}}]
    assert enrich_artist_refs(
        refs, track_service=track_service, registry=FakeRegistry(),
        cover_service=CountingCovers(),
    ) is refs


def test_a_plugin_that_raises_returns_the_refs_unharmed(library):
    track_service, _repo, _session = library

    class Broken(FakeArtwork):
        def get(self, entity, ids, output_dir, *, should_stop=None):
            raise RuntimeError("network down")

    refs = [{"name": "Seven Lions", "external_ids": {KEY: CHANNEL}}]
    assert _enrich(refs, track_service, Broken()) == refs


def test_an_unreachable_portrait_leaves_the_ref_identified_but_faceless(library, monkeypatch):
    track_service, _repo, _session = library
    monkeypatch.setattr("app.importer.stash.fetch_bytes", lambda url, **kw: None)

    refs = _enrich([{"name": "Seven Lions", "external_ids": {KEY: CHANNEL}}], track_service, CountingArtwork())

    assert refs == [{"name": "Seven Lions", "external_ids": {KEY: CHANNEL}}]


def test_an_empty_ref_list_is_returned_as_is(library):
    track_service, _repo, _session = library
    assert _enrich([], track_service, CountingArtwork()) == []


def test_a_cancelled_stash_stops_fetching_portraits(library):
    track_service, _repo, _session = library
    engine = CountingArtwork()

    refs = _enrich(
        [{"name": "Seven Lions", "external_ids": {KEY: CHANNEL}}],
        track_service,
        engine,
        should_stop=lambda: True,
    )

    assert engine.asked == []
    assert refs == [{"name": "Seven Lions", "external_ids": {KEY: CHANNEL}}]


# ---------------------------------------------------------------------------
# What TrackService does with the result
# ---------------------------------------------------------------------------

def test_the_portrait_hash_reaches_the_artist_row(library):
    track_service, repo, _session = library

    track_service.create_track_with_relationships({
        "title": "Foolish Of Me",
        "duration_seconds": 187,
        "artist_refs": [
            {"name": "Seven Lions", "external_ids": {KEY: CHANNEL}, "image_hash": PORTRAIT}
        ],
    })

    artist = repo.get_by_name("Seven Lions")
    assert held_id(artist, KEY) == CHANNEL
    assert artist.image_hash == PORTRAIT


def test_an_existing_portrait_is_never_overwritten(library):
    """Backfill only, exactly as the channel id beside it. A picture the user
    chose is not something a passing import gets to replace."""
    track_service, repo, session = library
    session.add(Artist(name="Seven Lions", image_hash="o" * 32))
    session.commit()

    track_service.create_track_with_relationships({
        "title": "Rush Over",
        "duration_seconds": 200,
        "artist_refs": [
            {"name": "Seven Lions", "external_ids": {KEY: CHANNEL}, "image_hash": PORTRAIT}
        ],
    })

    assert repo.get_by_name("Seven Lions").image_hash == "o" * 32


def test_a_ref_without_a_portrait_leaves_the_row_alone(library):
    track_service, repo, _session = library

    track_service.create_track_with_relationships({
        "title": "Rush Over",
        "duration_seconds": 200,
        "artist_refs": [{"name": "Seven Lions", "external_ids": {KEY: CHANNEL}}],
    })

    assert repo.get_by_name("Seven Lions").image_hash is None


# ---------------------------------------------------------------------------
# identities_for_names
# ---------------------------------------------------------------------------

def test_identities_for_names_matches_regardless_of_case(library):
    _track_service, repo, session = library
    session.add(Artist(name="Seven Lions", external_id_rows=id_rows("artist", {KEY: CHANNEL}), image_hash=PORTRAIT))
    session.commit()

    known = repo.identities_for_names(["SEVEN LIONS"])

    assert known["seven lions"] == {"external_ids": {KEY: CHANNEL}, "image_hash": PORTRAIT}


def test_identities_for_names_omits_artists_it_has_never_seen(library):
    _track_service, repo, _session = library
    assert repo.identities_for_names(["Nobody"]) == {}


def test_identities_for_names_handles_an_empty_request(library):
    _track_service, repo, _session = library
    assert repo.identities_for_names([]) == {}
    assert repo.identities_for_names(["", "  "]) == {}
