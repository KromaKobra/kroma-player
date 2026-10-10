"""Artists as entities: listing, searching, editing, and taking apart.

The behaviours here are the ones the feature exists for, so they are tested at
the seam a user's action actually crosses — the service — rather than at the
repository underneath it. Where a repository method is checked directly it is
because it is the one place the invariant lives.
"""
from __future__ import annotations

import pytest

from app.events.event_bus import EventBus
from conftest import make_track

from app.models.track import Artist, Track
from app.repositories.artist_repository import ArtistRepository
from app.repositories.genre_repository import GenreRepository
from app.repositories.track_repository import TrackRepository
from app.services.artist_service import (
    ArtistService,
    looks_malformed,
    propose_split,
)
from app.services.track_service import TrackService
from tests.fakes.providers import CHANNEL, held_id


@pytest.fixture
def artists(tmp_path):
    """A small library with the shapes this feature has to cope with.

    Deliberately not the 1200-track `db` fixture: every assertion here is about
    a handful of named artists, and counting them among 40 generated ones would
    make the failures unreadable.
    """
    from app.database.connection import DatabaseManager

    manager = DatabaseManager.from_path(tmp_path / "tracks.db")
    session = manager.create_session()

    rows = {
        name: Artist(name=name)
        for name in ("Skrillex", "skrillex-lower", "person1/person2", "Nero", "Orphan")
    }
    session.add_all(rows.values())
    session.flush()

    tracks = [
        make_track(session, title="Bangarang", album="Bangarang EP", duration_seconds=215,
              artists=[rows["Skrillex"]]),
        make_track(session, title="Kyoto", album="Bangarang EP", duration_seconds=190,
              artists=[rows["Skrillex"]]),
        make_track(session, title="Promises", album="Welcome Reality", duration_seconds=240,
              artists=[rows["person1/person2"]]),
        make_track(session, title="Guilt", album="Welcome Reality", duration_seconds=200,
              artists=[rows["person1/person2"], rows["Nero"]]),
    ]
    session.add_all(tracks)
    session.commit()

    bus = EventBus()
    artist_repo = ArtistRepository(session)
    track_service = TrackService(
        TrackRepository(session), artist_repo, GenreRepository(session), bus
    )
    service = ArtistService(artist_repo, bus, track_service=track_service)
    yield service, artist_repo, session, rows
    session.close()


# ---------------------------------------------------------------------------
# Recognising a compound name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, expected",
    [
        ("person1/person2", ["person1", "person2"]),
        ("A feat. B", ["A", "B"]),
        ("A ft B", ["A", "B"]),
        ("Jack Ü ft. Kiesza", ["Jack Ü", "Kiesza"]),
        ("A vs. B", ["A", "B"]),
        ("A, B", ["A", "B"]),
        # Nothing to split.
        ("Skrillex", []),
        ("feature", []),
        # The same artist typed twice is one artist.
        ("A & A", []),
    ],
)
def test_propose_split(name, expected):
    assert propose_split(name) == expected


@pytest.mark.parametrize(
    "name, flagged",
    [
        ("person1/person2", True),
        ("A feat. B", True),
        ("A vs. B", True),
        # "&" is far too common in real names to flag; a comma already splits
        # correctly on the way in, so neither is a sign of anything wrong.
        ("Above & Beyond", False),
        ("A, B", False),
        ("Skrillex", False),
    ],
)
def test_looks_malformed(name, flagged):
    assert looks_malformed(name) is flagged


def test_a_legitimate_name_with_a_slash_is_only_ever_suggested(artists):
    """AC/DC is why splitting is never automatic.

    The name trips the same separator "person1/person2" does, and no rule can
    tell them apart — so the split is proposed and the user decides. Nothing in
    the import path acts on this.
    """
    assert looks_malformed("AC/DC") is True
    assert propose_split("AC/DC") == ["AC", "DC"]

    service, repo, session, _ = artists
    session.add(Artist(name="AC/DC"))
    session.commit()
    # Flagged, but untouched: no sweep, list or search rewrites it.
    assert repo.get_by_name("AC/DC") is not None


# ---------------------------------------------------------------------------
# Listing and searching
# ---------------------------------------------------------------------------


def test_list_all_counts_tracks_and_keeps_artists_with_none(artists):
    service, _, _, _ = artists
    by_name = {row["name"]: row for row in service.list_all()}

    assert by_name["Skrillex"]["track_count"] == 2
    assert by_name["person1/person2"]["track_count"] == 2
    assert by_name["Nero"]["track_count"] == 1
    # An artist nothing points at is debris the user needs to see, not hide.
    assert by_name["Orphan"]["track_count"] == 0
    assert by_name["person1/person2"]["needs_attention"] is True
    assert by_name["Skrillex"]["needs_attention"] is False


def test_list_all_defaults_to_the_biggest_catalogues_first(artists):
    """What the artist wall opens on, and the order it is worth opening on."""
    service, _, _, _ = artists
    counts = [row["track_count"] for row in service.list_all()]
    assert counts == sorted(counts, reverse=True)
    # The two-track artists lead; the ones nothing credits are last.
    assert counts[0] == 2
    assert counts[-1] == 0


@pytest.mark.parametrize(
    "sort_field, key",
    [
        ("trackCount", lambda row: row["track_count"]),
        ("name", lambda row: row["name"].lower()),
        ("dateAdded", lambda row: row["id"]),
    ],
)
@pytest.mark.parametrize("ascending", [True, False])
def test_list_all_orders_by_every_key_in_both_directions(artists, sort_field, key, ascending):
    service, _, _, _ = artists
    keys = [key(row) for row in service.list_all(sort_field, ascending)]
    assert keys == sorted(keys, reverse=not ascending)


def test_equal_rows_tiebreak_with_the_sort_rather_than_against_it(artists):
    """Reversing the direction reverses the whole order, ties included.

    Skrillex and person1/person2 both have two tracks. A tiebreak pinned one way
    would leave them in the same order both times, so the artist at the top of
    the wall would not change when the user pressed the chevron — which is the
    one thing pressing it is supposed to do.
    """
    service, _, _, _ = artists
    descending = [r["name"] for r in service.list_all("trackCount", ascending=False)]
    ascending = [r["name"] for r in service.list_all("trackCount", ascending=True)]
    assert descending == list(reversed(ascending))


def test_an_unknown_sort_key_falls_back_rather_than_raising(artists):
    """A preference left over from another GUI shows a library, not an error."""
    service, _, _, _ = artists
    assert service.list_all("whatever") == service.list_all("trackCount")


def test_search_ranks_prefix_above_contains(artists):
    service, _, session, _ = artists
    session.add(Artist(name="The Skrillex Tribute"))
    session.commit()

    names = [row["name"] for row in service.search("skrillex")]
    # "Skrillex" is exact, "skrillex-lower" is a prefix, the tribute merely
    # contains it.
    assert names[0] == "Skrillex"
    assert names.index("skrillex-lower") < names.index("The Skrillex Tribute")


def test_search_is_case_insensitive_and_bounded(artists):
    service, _, _, _ = artists
    assert [r["name"] for r in service.search("SKRILLEX", limit=1)] == ["Skrillex"]
    assert service.search("   ") == []


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------


def test_rename_refuses_a_name_another_artist_holds(artists):
    service, repo, _, rows = artists
    result = service.update(rows["Nero"].id, name="Skrillex")

    assert result.artist is None
    assert "Skrillex" in result.error
    # Nothing written: a refused save must not half-apply.
    assert repo.get_by_id(rows["Nero"].id).name == "Nero"


def test_rename_collision_is_case_insensitive(artists):
    """The collision that actually bites is capitals.

    ``artists.name`` is UNIQUE and exact, so "skrillex" beside "Skrillex" is
    permitted by the database and is exactly the split-catalogue bug this
    feature exists to stop.
    """
    service, _, _, rows = artists
    assert "already exists" in service.update(rows["Nero"].id, name="SKRILLEX").error


def test_renaming_to_your_own_name_is_allowed(artists):
    service, _, _, rows = artists
    result = service.update(rows["Nero"].id, name="Nero ")
    assert result.error == ""
    assert result.artist.name == "Nero"


def test_an_external_id_belongs_to_one_artist(artists):
    service, _, _, rows = artists
    assert service.update(rows["Skrillex"].id, ids={CHANNEL: "UCabcdefghij"}).error == ""

    result = service.update(rows["Nero"].id, ids={CHANNEL: "UCabcdefghij"})
    assert result.artist is None
    assert "Skrillex" in result.error


def test_blank_name_is_refused(artists):
    service, _, _, rows = artists
    assert service.update(rows["Nero"].id, name="   ").error != ""


def test_resolve_external_id_never_overwrites_one_already_set(artists):
    """A passing search result does not get to overrule a confirmed id."""
    service, repo, _, rows = artists
    service.update(rows["Skrillex"].id, ids={CHANNEL: "UCoriginal01"})

    service.resolve_external_id("Skrillex", CHANNEL, "UCsomethingelse")
    assert held_id(repo.get_by_id(rows["Skrillex"].id), CHANNEL) == "UCoriginal01"


def test_resolve_external_id_backfills_an_artist_that_has_none(artists):
    service, repo, _, rows = artists
    service.resolve_external_id("Nero", CHANNEL, "UCnero000001")
    assert held_id(repo.get_by_id(rows["Nero"].id), CHANNEL) == "UCnero000001"


def test_resolve_external_id_will_not_claim_an_id_another_artist_holds(artists):
    service, repo, _, rows = artists
    service.update(rows["Skrillex"].id, ids={CHANNEL: "UCshared00001"})

    service.resolve_external_id("Nero", CHANNEL, "UCshared00001")
    assert held_id(repo.get_by_id(rows["Nero"].id), CHANNEL) is None


# ---------------------------------------------------------------------------
# Splitting — the repair this feature exists for
# ---------------------------------------------------------------------------


def test_split_reassigns_every_track_and_removes_the_original(artists):
    service, repo, _, rows = artists
    compound_id = rows["person1/person2"].id

    assert service.split(compound_id, ["person1", "person2"]).error == ""

    assert repo.get_by_id(compound_id) is None
    p1 = repo.get_by_name("person1")
    p2 = repo.get_by_name("person2")
    assert p1 is not None and p2 is not None
    # Both tracks the compound row carried, now credited to both real artists.
    assert repo.track_count(p1.id) == 2
    assert repo.track_count(p2.id) == 2


def test_split_folds_into_an_artist_that_already_exists(artists):
    """The merge case, arrived at from the side.

    Splitting into a name the library already knows reuses that row rather than
    minting a near-duplicate, so an artist's catalogue comes out whole. This is
    why there is no separate merge tool.
    """
    service, repo, _, rows = artists
    existing_nero = rows["Nero"].id

    service.split(rows["person1/person2"].id, ["person1", "Nero"])

    assert repo.get_by_id(existing_nero) is not None
    # Nero already had "Guilt"; it gains "Promises" and does not gain "Guilt" twice.
    assert repo.track_count(existing_nero) == 2


def test_split_does_not_duplicate_a_track_already_crediting_the_target(artists):
    service, repo, session, rows = artists
    service.split(rows["person1/person2"].id, ["Nero"])

    guilt = session.query(Track).filter(Track.title == "Guilt").one()
    assert [a.name for a in guilt.artists] == ["Nero"]


def test_split_into_nothing_is_refused(artists):
    service, repo, _, rows = artists
    assert service.split(rows["person1/person2"].id, []).error != ""
    assert repo.get_by_id(rows["person1/person2"].id) is not None


def test_split_into_only_yourself_is_refused(artists):
    service, repo, _, rows = artists
    result = service.split(rows["Skrillex"].id, ["Skrillex"])
    assert "same artist" in result.error
    assert repo.get_by_id(rows["Skrillex"].id) is not None


def test_naming_the_source_among_the_targets_keeps_it(artists):
    """Splitting "A & B" into "A & B" and "B" is a real thing to want.

    The compound name is sometimes a genuine act *and* a collaboration, so the
    user may want both rows. Deleting the one they just named would destroy what
    they asked to keep.
    """
    service, repo, _, rows = artists
    compound_id = rows["person1/person2"].id

    result = service.split(compound_id, ["person1/person2", "person1"])

    assert result.error == ""
    survivor = repo.get_by_id(compound_id)
    assert survivor is not None, "the artist the user named was deleted"
    # It keeps its tracks, and person1 gains them alongside.
    assert repo.track_count(compound_id) == 2
    assert repo.track_count(repo.get_by_name("person1").id) == 2


def test_a_surviving_source_is_not_announced_as_deleted(artists):
    """An open editor closes on `artist.deleted`, so a false one closes a live page."""
    service, _, _, rows = artists
    compound_id = rows["person1/person2"].id
    deleted: list[int] = []
    service.event_bus.subscribe("artist.deleted", deleted.append)

    service.split(compound_id, ["person1/person2", "person1"])

    assert deleted == []


# ---------------------------------------------------------------------------
# Orphans
# ---------------------------------------------------------------------------


def test_sweep_removes_artists_nothing_credits(artists):
    service, repo, _, rows = artists
    # Two of them: the one named for the purpose, and the lower-case near-
    # duplicate, which is exactly the kind of row that used to accumulate.
    assert service.sweep_orphans() == 2
    assert repo.get_by_name("Orphan") is None
    assert repo.get_by_name("skrillex-lower") is None
    # And leaves everyone who still has a track.
    assert repo.get_by_name("Skrillex") is not None


def test_editing_a_track_sweeps_the_artist_it_abandoned(artists):
    """Nothing used to delete an artist row, so every edit left debris."""
    service, repo, session, rows = artists
    track_service = service.track_service

    nero_id = rows["Nero"].id
    guilt = session.query(Track).filter(Track.title == "Guilt").one()
    track_service.update_track_metadata(guilt.id, {"artist_ids": [rows["person1/person2"].id]})

    assert repo.get_by_id(nero_id) is None


def test_a_sweep_keeps_an_artist_who_merely_lost_one_track(artists):
    service, repo, session, rows = artists
    track_service = service.track_service

    skrillex_id = rows["Skrillex"].id
    kyoto = session.query(Track).filter(Track.title == "Kyoto").one()
    track_service.update_track_metadata(kyoto.id, {"artist_ids": [rows["Nero"].id]})

    assert repo.get_by_id(skrillex_id) is not None
    assert repo.track_count(skrillex_id) == 1
