"""A track's artists are credited in an order, and the order is the track's own.

The order used to be thrown away at the association table, so every read fell
back on ``artists.id`` — the order the artist joined the *library*. That put
whoever was imported earliest at the front of every track they appear on, which
is why a library reads as though it were sorted by who has the most tracks.

Tested at both ends, because the two are separately wrong-able: the ORM
relationship feeds the editor, the player bar, MPRIS and the export, while
``fetch_artists`` feeds every track list on screen, and they are different
queries over the same column.

Artist ids here are deliberately *not* in credit order — that is the whole point.
An assertion that passes when the two coincide proves nothing.
"""
from __future__ import annotations

import pytest

from app.events.event_bus import EventBus
from app.models.track import Artist
from app.repositories.artist_repository import ArtistRepository
from app.repositories.genre_repository import GenreRepository
from app.repositories.track_repository import TrackRepository
from app.services.artist_service import ArtistService
from app.services.track_service import TrackService


@pytest.fixture
def library(tmp_path):
    """A track service over an empty vault, plus the repos to inspect it with."""
    from app.database.connection import DatabaseManager

    manager = DatabaseManager.from_path(tmp_path / "tracks.db")
    session = manager.create_session()

    bus = EventBus()
    track_repo = TrackRepository(session)
    artist_repo = ArtistRepository(session)
    track_service = TrackService(
        track_repo, artist_repo, GenreRepository(session), bus
    )
    artist_service = ArtistService(artist_repo, bus, track_service=track_service)
    yield track_service, artist_service, track_repo, artist_repo, session
    session.close()


def _make(track_service, title: str, artists: str) -> int:
    return track_service.create_track_with_relationships(
        {"title": title, "artists": artists, "duration_seconds": 200}
    )


def _names(track_repo, track_id: int) -> list[str]:
    """What the track lists show — the batched string, split back up."""
    return [n for n in track_repo.fetch_artists([track_id])[track_id].split(", ") if n]


def _related(track_repo, track_id: int) -> list[str]:
    """What the ORM yields, which is what everything outside the lists reads."""
    return [a.name for a in track_repo.get_by_id(track_id).artists]


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def test_the_order_a_track_arrives_with_is_the_order_it_keeps(library):
    track_service, _, track_repo, _, _ = library

    # "Zara" is created first and so holds the lowest artist id, but is credited
    # last. Under the old ordering she led every one of these tracks.
    first = _make(track_service, "One", "Zara, Ada")
    second = _make(track_service, "Two", "Ada, Zara")

    assert _names(track_repo, first) == ["Zara", "Ada"]
    assert _names(track_repo, second) == ["Ada", "Zara"]
    assert _related(track_repo, first) == ["Zara", "Ada"]
    assert _related(track_repo, second) == ["Ada", "Zara"]


def test_two_tracks_can_credit_the_same_pair_in_opposite_orders(library):
    """The fact a global ordering cannot express, which is why the column exists."""
    track_service, _, track_repo, _, _ = library
    lead = _make(track_service, "Theirs", "Ada, Zara")
    guest = _make(track_service, "Hers", "Zara, Ada")

    assert _names(track_repo, lead) != _names(track_repo, guest)
    assert _names(track_repo, lead) == list(reversed(_names(track_repo, guest)))


def test_artist_refs_order_wins_over_the_string(library):
    """The structured credit list is the good path; the string is the lossy one."""
    track_service, _, track_repo, _, _ = library
    track_id = track_service.create_track_with_relationships({
        "title": "Downloaded",
        "duration_seconds": 200,
        "artists": "Ada, Zara",
        "artist_refs": [{"name": "Zara", "external_ids": {}},
                        {"name": "Ada", "external_ids": {}}],
    })
    assert _names(track_repo, track_id) == ["Zara", "Ada"]


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
#
# The order a source credits a track in is the one worth having — it is the
# original, and every other order in the library is derived from it. A plugin
# hands it over as a row's `artistRefs`, and it has to survive three hops to
# reach the database: `row_from_protocol` → `track_info_from_row` →
# `enrich_artist_refs` → `create_track_with_relationships`. Each preserves it,
# and each could stop. (How the plugin builds the list from what its catalogue
# says is its own to test.)
#
# The fixture is arranged so that each of the three plausible bugs shows up:
#
#   sorted by name          "Crystal Skies" would lead, and does not
#   sorted by artist id     the library below creates them in the opposite order
#   identified artist hoisted  "Seven Lions" holds the id and is credited last
#
# None of that is decoration. A fixture that happens to be alphabetical, or whose
# artists are created in the order they are credited, passes every one of these
# tests with a sort put back in — which is exactly what a first draft of this
# file did.

CREDITS = ["Jason Ross", "Crystal Skies", "Seven Lions"]
CHANNEL = "UCfakeChannel0000000001A"

PROTOCOL_ROW = {
    "title": "Falling Away",
    "artists": list(CREDITS),
    "duration": 214,
    "externalIds": [{"provider": "fake", "kind": "video", "value": "abc12345678"}],
    "artistRefs": [
        {"name": "Jason Ross", "externalIds": []},
        {"name": "Crystal Skies", "externalIds": []},
        {"name": "Seven Lions",
         "externalIds": [{"provider": "fake", "kind": "channel", "value": CHANNEL}]},
    ],
}


def test_the_credit_order_survives_the_row_conversion():
    from app.plugins.providers import row_from_protocol

    refs = row_from_protocol(PROTOCOL_ROW)["artist_refs"]
    assert [r["name"] for r in refs] == CREDITS
    # The one carrying the id is still third, where the source put it.
    assert refs[2]["external_ids"] == {"fake:channel": CHANNEL}
    assert refs[0]["external_ids"] == {}


def test_a_fetched_track_is_stored_in_the_order_its_source_credited_it(library):
    """The whole chain, from the protocol row to the rows on disk."""
    from app.importer.fetch import track_info_from_row
    from app.plugins.providers import row_from_protocol

    track_service, _, track_repo, _, session = library

    # A warm library: these three are already known, and were met in the reverse
    # of the order this fetch credits them. Fetching into an empty library would
    # mint the rows in credit order and so hide any ordering by id.
    for name in reversed(CREDITS):
        session.add(Artist(name=name))
    session.commit()

    track_info = track_info_from_row(row_from_protocol(PROTOCOL_ROW))
    track_id = track_service.create_track_with_relationships(track_info)

    session.expire_all()
    assert _names(track_repo, track_id) == CREDITS
    assert _related(track_repo, track_id) == CREDITS


def test_enrichment_hands_the_refs_back_in_the_order_it_got_them(library):
    """The hop that does network work, and so the one most able to reorder.

    Disabled without an artwork provider, which is the headless case — so it is
    given one that answers nothing, to exercise the loop rather than skip it.
    """
    from app.importer.artist_enrichment import enrich_artist_refs
    from app.plugins.providers import row_from_protocol
    from tests.fakes.providers import FakeArtwork, FakeRegistry

    track_service, _, _, _, _ = library

    refs = row_from_protocol(PROTOCOL_ROW)["artist_refs"]
    enriched = enrich_artist_refs(
        refs,
        track_service=track_service,
        registry=FakeRegistry(artwork=FakeArtwork()),
        cover_service=object(),
    )
    assert [r["name"] for r in enriched] == CREDITS


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------


def test_rearranging_by_id_sticks(library):
    """What dragging a chip in the editor comes down to."""
    track_service, _, track_repo, artist_repo, session = library
    track_id = _make(track_service, "One", "Ada, Bea, Cyd")

    ids = [artist_repo.get_by_name(n).id for n in ("Cyd", "Ada", "Bea")]
    track_service.update_track_metadata(track_id, {"artist_ids": ids})

    # Read back through a session that has forgotten everything, so the answer
    # comes from the database rather than a collection still held in memory.
    session.expire_all()
    assert _names(track_repo, track_id) == ["Cyd", "Ada", "Bea"]
    assert _related(track_repo, track_id) == ["Cyd", "Ada", "Bea"]


def test_a_reorder_alone_changes_the_string_the_file_tag_is_written_from(library):
    """`Track.artist` is what the Opus ARTIST tag is restated from after a save.

    Restated *after* the commit, from a collection the session has expired and
    re-read — so this is also the check that the order survives a round trip to
    the database rather than lingering in memory.
    """
    track_service, _, _, artist_repo, _ = library
    track_id = _make(track_service, "One", "Ada, Zara")

    ids = [artist_repo.get_by_name(n).id for n in ("Zara", "Ada")]
    result = track_service.update_track_metadata(track_id, {"artist_ids": ids})
    assert result.track.artist == "Zara, Ada"


def test_an_artist_added_later_lands_at_the_end(library):
    """Adding does not disturb an arrangement already made."""
    track_service, _, track_repo, artist_repo, session = library
    track_id = _make(track_service, "One", "Ada, Bea")

    ids = [artist_repo.get_by_name(n).id for n in ("Bea", "Ada")]
    track_service.update_track_metadata(track_id, {"artist_ids": ids})
    session.expire_all()

    session.add(Artist(name="Cyd"))
    session.commit()
    ids.append(artist_repo.get_by_name("Cyd").id)
    track_service.update_track_metadata(track_id, {"artist_ids": ids})
    session.expire_all()

    assert _names(track_repo, track_id) == ["Bea", "Ada", "Cyd"]


def test_removing_an_artist_leaves_the_rest_in_order(library):
    track_service, _, track_repo, artist_repo, session = library
    track_id = _make(track_service, "One", "Cyd, Ada, Bea")

    keep = [artist_repo.get_by_name(n).id for n in ("Cyd", "Bea")]
    track_service.update_track_metadata(track_id, {"artist_ids": keep})
    session.expire_all()

    assert _names(track_repo, track_id) == ["Cyd", "Bea"]


# ---------------------------------------------------------------------------
# Splitting
# ---------------------------------------------------------------------------


def test_a_split_lands_the_new_artists_in_the_slot_the_old_one_held(library):
    """A split corrects who the credits name, not how important they were.

    "Bea/Cyd" credited second becomes two artists still credited second. Landing
    them at the end would quietly re-rank every track the compound name was on,
    which is a worse mistake than the one being fixed.
    """
    track_service, artist_service, track_repo, artist_repo, session = library
    track_id = _make(track_service, "One", "Ada, Bea/Cyd, Dot")

    compound = artist_repo.get_by_name("Bea/Cyd")
    assert artist_service.split(int(compound.id), ["Bea", "Cyd"]).error == ""
    session.expire_all()

    assert _names(track_repo, track_id) == ["Ada", "Bea", "Cyd", "Dot"]


def test_a_split_target_the_track_already_credits_keeps_its_place(library):
    """Splitting "A/B" where the track already credits "A" is the ordinary case."""
    track_service, artist_service, track_repo, artist_repo, session = library
    track_id = _make(track_service, "One", "Ada, Bea, Ada/Cyd")

    compound = artist_repo.get_by_name("Ada/Cyd")
    assert artist_service.split(int(compound.id), ["Ada", "Cyd"]).error == ""
    session.expire_all()

    # Ada is not mentioned twice, and does not move to where the compound stood.
    assert _names(track_repo, track_id) == ["Ada", "Bea", "Cyd"]


# ---------------------------------------------------------------------------
# Genres are not artists
# ---------------------------------------------------------------------------


def test_genres_are_untouched_by_any_of_this(library):
    """`_fetch_related` is shared, and a genre list has no order to protect.

    A set of tags, where "first" means nothing — so they keep the default
    ordering rather than gaining a column that would never be arranged.
    """
    track_service, _, track_repo, _, _ = library
    track_id = track_service.create_track_with_relationships(
        {"title": "One", "artists": "Ada", "duration_seconds": 200,
         "genres": "House, Techno"}
    )
    assert set(track_repo.fetch_genres([track_id])[track_id].split(", ")) == {
        "House", "Techno"
    }
