"""Albums as entities: resolution, conflicts, artwork agreement, split, sweep.

Modelled on tests/test_artists.py. The cases that matter here and have no artist
counterpart are the two the album feature turns on:

- an album is identified by (name, lead artist), so two records really can share
  a name and must not merge;
- a track's artwork belongs to the album it is *primary* for, so a track merely
  visiting an album must never be counted as a disagreement or offered up to be
  rewritten.
"""
from __future__ import annotations

import pytest

from app.database.connection import DatabaseManager
from app.events.event_bus import EventBus
from app.models.track import Album, Artist, Track
from app.repositories.album_repository import AlbumRepository
from app.repositories.track_repository import write_album_positions
from app.services.album_service import AlbumService, artwork_disagrees
from tests.fakes.providers import RELEASE, VIDEO, held_id, id_rows


@pytest.fixture
def albums(tmp_path):
    """A library with two same-named records, and one visiting track."""
    manager = DatabaseManager.from_path(tmp_path / "tracks.db")
    session = manager.create_session()

    queen = Artist(name="Queen")
    abba = Artist(name="ABBA")
    session.add_all([queen, abba])
    session.flush()

    hits_q = Album(name="Greatest Hits", cover_hash="aaa")
    hits_a = Album(name="Greatest Hits", cover_hash="bbb")
    # Its sleeve is its own primary member's cover, which is what a healthy
    # album looks like: an album whose sleeve none of its tracks carries is a
    # disagreement in its own right — see the foreign-sleeve tests below.
    comp = Album(name="Party Mix", cover_hash="qqq")
    session.add_all([hits_q, hits_a, comp])
    session.flush()

    def track(title, artist, album, cover, video=None):
        row = Track(
            title=title, duration_seconds=200, artists=[artist],
            cover_hash=cover, external_id_rows=id_rows("track", {VIDEO: video} if video else {}),
        )
        row.albums = [album]
        session.add(row)
        return row

    track("Bohemian", queen, hits_q, "aaa", "vidA")
    track("Somebody", queen, hits_q, "aaa", "vidB")
    # The odd one out: same album, different artwork.
    odd = track("Radio Ga Ga", queen, hits_q, "ccc")
    track("Dancing Queen", abba, hits_a, "bbb")

    # A visitor: belongs to Party Mix, also appears on Queen's Greatest Hits.
    visitor = Track(title="Guest", duration_seconds=180, artists=[queen], cover_hash="qqq")
    visitor.albums = [comp, hits_q]
    session.add(visitor)
    session.commit()

    repo = AlbumRepository(session)
    write_album_positions(session, {int(visitor.id): [int(comp.id), int(hits_q.id)]})
    for album in (hits_q, hits_a, comp):
        repo.set_order(int(album.id), repo.track_ids(int(album.id)))
    session.commit()

    bus = EventBus()
    service = AlbumService(repo, bus)
    yield {
        "session": session, "repo": repo, "service": service, "bus": bus,
        "hits_q": hits_q, "hits_a": hits_a, "comp": comp,
        "queen": queen, "abba": abba, "odd": odd, "visitor": visitor,
    }
    session.close()


# ----------------------------------------------------------------- resolution


def test_two_records_may_share_a_name(albums):
    """`albums.name` is not unique, and that is the point of the whole design."""
    repo = albums["repo"]
    assert albums["hits_q"].id != albums["hits_a"].id
    assert albums["hits_q"].name == albums["hits_a"].name


def test_get_or_create_keys_on_name_and_artist(albums):
    """Two "Greatest Hits" by different artists resolve to different rows."""
    repo = albums["repo"]
    queen_id, abba_id = int(albums["queen"].id), int(albums["abba"].id)

    assert repo.get_or_create("Greatest Hits", queen_id).id == albums["hits_q"].id
    assert repo.get_or_create("Greatest Hits", abba_id).id == albums["hits_a"].id


def test_an_unknown_artist_mints_a_new_album(albums):
    """A name that exists but for nobody we know is a different record."""
    repo, session = albums["repo"], albums["session"]
    stranger = Artist(name="Stranger")
    session.add(stranger)
    session.flush()

    fresh = repo.get_or_create("Greatest Hits", int(stranger.id))
    assert fresh.id not in (albums["hits_q"].id, albums["hits_a"].id)


# -------------------------------------------------------------------- primary


def test_track_album_reads_the_primary(albums):
    """`Track.album` is the primary album's name, not a comma-joined list."""
    visitor = albums["visitor"]
    assert visitor.album == "Party Mix"
    assert [a.name for a in visitor.albums] == ["Party Mix", "Greatest Hits"]


def test_set_primary_promotes_and_refuses_a_non_member(albums):
    service, session, visitor = albums["service"], albums["session"], albums["visitor"]

    assert service.set_primary(int(visitor.id), int(albums["hits_q"].id)) is True
    session.refresh(visitor)
    assert visitor.album == "Greatest Hits"

    # Not on that album at all — joining and choosing a home are two decisions.
    assert service.set_primary(int(visitor.id), int(albums["hits_a"].id)) is False


# -------------------------------------------------------------------- artwork


def test_artwork_disagrees_ignores_missing_covers():
    """A track with no artwork is a different problem, not a disagreement."""
    assert artwork_disagrees(["a", "b"]) is True
    assert artwork_disagrees(["a", "a", ""]) is False
    assert artwork_disagrees([""]) is False


def test_a_visiting_track_is_never_counted_as_a_disagreement(albums):
    """The rule the whole artwork flow turns on.

    The visitor carries "qqq" while Queen's Greatest Hits is otherwise "aaa"
    and "ccc". It belongs to Party Mix, so its cover is correct and must not
    appear in the album's own accounting.
    """
    service = albums["service"]
    album_id = int(albums["hits_q"].id)

    details = service.details(album_id)
    assert details["track_count"] == 4        # three members plus the visitor
    assert details["primary_count"] == 3      # the visitor is not one

    hashes = {h for group in service.cover_groups(album_id)
              for h in [group["cover_hash"]]}
    assert "qqq" not in hashes
    assert service.visiting_track_ids(album_id) == [int(albums["visitor"].id)]


def test_needs_attention_follows_the_primary_members_only(albums):
    service = albums["service"]
    assert service.details(int(albums["hits_q"].id))["needs_attention"] is True
    # ABBA's record has one track and therefore one cover.
    assert service.details(int(albums["hits_a"].id))["needs_attention"] is False
    # Party Mix's only primary member is the visitor — one cover, no argument.
    assert service.details(int(albums["comp"].id))["needs_attention"] is False


def test_a_sleeve_none_of_the_tracks_carries_is_a_disagreement(albums):
    """The hole a distinct count cannot see.

    An album identified against a catalogue holds the release's own artwork,
    which no file has ever been tagged with. Every track disagrees with the
    sleeve shown on its page while the covers among them agree perfectly, so
    counting only the tracks reports a healthy album.
    """
    service, session = albums["service"], albums["session"]
    album_id = int(albums["hits_a"].id)

    # ABBA's record: one track, one cover, and a sleeve that matches it.
    assert service.details(album_id)["needs_attention"] is False

    session.get(Album, album_id).cover_hash = "ddd"
    session.commit()
    assert service.details(album_id)["needs_attention"] is True
    assert next(r for r in service.list_all() if r["id"] == album_id)[
        "needs_attention"
    ] is True


def test_an_album_with_no_sleeve_of_its_own_is_not_in_disagreement(albums):
    """Absence is not disagreement — the rule `artwork_disagrees` already had."""
    service, session = albums["service"], albums["session"]
    album_id = int(albums["hits_a"].id)

    session.get(Album, album_id).cover_hash = None
    session.commit()
    assert service.details(album_id)["needs_attention"] is False


def test_cover_groups_offers_the_albums_own_sleeve_as_a_repair(albums):
    """A disagreement the warning can point at, the repair must be able to reach."""
    service, session = albums["service"], albums["session"]
    album_id = int(albums["hits_q"].id)
    album = session.get(Album, album_id)
    album.cover_hash = "ddd"
    session.commit()

    groups = service.cover_groups(album_id)
    own = [g for g in groups if g["is_album_cover"]]
    assert [g["cover_hash"] for g in own] == ["ddd"]
    assert own[0]["track_ids"] == [], "no track carries it yet — that is the point"
    # Unidentified, so the fewest-files rule still decides the default keeper.
    assert groups[0]["cover_hash"] == "aaa"

    # Identified, and the sleeve came from the release rather than from a file.
    album.external_id_rows = id_rows("album", {RELEASE: "MPREb_x"})
    session.commit()
    assert service.cover_groups(album_id)[0]["cover_hash"] == "ddd"


def test_cover_groups_puts_the_biggest_first_and_never_leads_with_nothing(albums):
    service, session = albums["service"], albums["session"]
    groups = service.cover_groups(int(albums["hits_q"].id))
    assert [g["cover_hash"] for g in groups] == ["aaa", "ccc"]
    assert len(groups[0]["track_ids"]) == 2

    # Strip the artwork off the two-track group and the empty one must still
    # not become the default keeper.
    for track_id in groups[0]["track_ids"]:
        session.get(Track, track_id).cover_hash = None
    session.commit()
    assert service.cover_groups(int(albums["hits_q"].id))[0]["cover_hash"] == "ccc"


def test_unify_only_touches_the_tracks_it_is_given(albums):
    """Selection is an input, never widened."""
    service, session = albums["service"], albums["session"]
    odd_id = int(albums["odd"].id)

    changed = service.apply_unified_cover(int(albums["hits_q"].id), "aaa", [odd_id])
    assert changed == 1
    assert session.get(Track, odd_id).cover_hash == "aaa"
    # The visitor was not named, so it keeps its own record's cover.
    assert session.get(Track, int(albums["visitor"].id)).cover_hash == "qqq"
    assert service.details(int(albums["hits_q"].id))["needs_attention"] is False


# ---------------------------------------------------------------------- split


def test_split_makes_a_same_named_sibling_and_moves_the_tracks(albums):
    service, repo = albums["service"], albums["repo"]
    source_id = int(albums["hits_q"].id)
    odd_id = int(albums["odd"].id)

    result = service.split(source_id, [odd_id])
    assert result.error == ""
    assert result.album.name == "Greatest Hits"
    assert int(result.album.id) != source_id
    # Seeded from the tracks that moved — the artwork that differed.
    assert result.album.cover_hash == "ccc"

    assert repo.track_ids(int(result.album.id)) == [odd_id]
    assert odd_id not in repo.track_ids(source_id)
    # Both sides renumbered densely.
    assert [
        r["position"] for r in repo.cover_rows(source_id)
    ] == list(range(len(repo.track_ids(source_id))))


def test_split_refuses_to_empty_the_source(albums):
    service, repo = albums["service"], albums["repo"]
    source_id = int(albums["hits_q"].id)
    result = service.split(source_id, repo.track_ids(source_id))
    assert "nothing left behind" in result.error
    assert repo.track_count(source_id) == 4


def test_split_carries_no_release_id_across(albums):
    """A release id names one record; after a split at most one is still it."""
    service = albums["service"]
    source_id = int(albums["hits_q"].id)
    service.update(source_id, ids={RELEASE: "MPREb_original"})

    result = service.split(source_id, [int(albums["odd"].id)])
    assert held_id(result.album, RELEASE) is None


# ------------------------------------------------------------------ conflicts


def test_a_duplicate_release_id_is_refused_and_writes_nothing(albums):
    service, session = albums["service"], albums["session"]
    service.update(int(albums["hits_q"].id), ids={RELEASE: "MPREb_one"})

    result = service.update(
        int(albums["hits_a"].id), name="Renamed", ids={RELEASE: "MPREb_one"}
    )
    assert "already belongs to" in result.error
    session.refresh(albums["hits_a"])
    # Neither half of the edit landed.
    assert held_id(albums["hits_a"], RELEASE) is None
    assert albums["hits_a"].name == "Greatest Hits"


def test_a_shared_name_is_not_a_conflict(albums):
    """Unlike artists, renaming into an existing name is perfectly legal."""
    service = albums["service"]
    result = service.update(int(albums["comp"].id), name="Greatest Hits")
    assert result.error == ""


def test_resolve_external_id_never_overwrites(albums):
    service = albums["service"]
    queen_id = int(albums["queen"].id)
    service.update(int(albums["hits_q"].id), ids={RELEASE: "MPREb_first"})

    service.resolve_external_id("Greatest Hits", queen_id, RELEASE, "MPREb_second")
    assert held_id(albums["hits_q"], RELEASE) == "MPREb_first"


# ---------------------------------------------------------------------- sweep


def test_sweep_removes_an_emptied_album_and_announces_it(albums):
    service, repo, bus = albums["service"], albums["repo"], albums["bus"]
    seen: list[int] = []
    bus.subscribe("album.deleted", lambda album_id: seen.append(album_id))

    comp_id = int(albums["comp"].id)
    service.remove_tracks(comp_id, [int(albums["visitor"].id)])

    assert repo.get_by_id(comp_id) is None
    assert comp_id in seen


def test_removing_a_primary_album_promotes_the_next(albums):
    """Otherwise the track is left with no row at album_position 0 at all."""
    service, session, visitor = albums["service"], albums["session"], albums["visitor"]
    service.remove_tracks(int(albums["comp"].id), [int(visitor.id)])

    session.refresh(visitor)
    assert visitor.album == "Greatest Hits"


def test_identifiable_tracks_come_in_running_order(albums):
    service = albums["service"]
    sources = service.identify_sources(int(albums["hits_q"].id))
    assert [s["external_ids"][VIDEO] for s in sources] == ["vidA", "vidB"]
    # The album's name travels with each track; a search hint is the plugin's
    # to build, not the core's.
    assert sources[0]["album"] == "Greatest Hits"
    assert sources[0]["title"] == "Bohemian"


def test_unify_with_no_tracks_moves_the_sleeve_alone(albums):
    """The other direction, and the one the dialog could not reach.

    Every track agrees and it is the album row that is wrong. There is no file
    to rewrite, so an empty track list is the honest description of the work —
    not a reason to refuse it.
    """
    service, session = albums["service"], albums["session"]
    album_id = int(albums["hits_a"].id)
    album = session.get(Album, album_id)
    album.cover_hash = "ddd"
    session.commit()
    assert service.details(album_id)["needs_attention"] is True

    changed = service.apply_unified_cover(album_id, "bbb", [])

    assert changed == 0, "no track was rewritten, and it should say so"
    assert session.get(Album, album_id).cover_hash == "bbb"
    assert service.details(album_id)["needs_attention"] is False


def test_an_explicit_keeper_overrules_a_browse_id(albums):
    """`seed_cover_from_tracks` refuses an identified album; this does not.

    The difference is who decided. Seeding guesses from whatever a file happened
    to be tagged with, and a browse id is better evidence than that. A user who
    picked a cover out of the dialog looked at both.
    """
    service, session = albums["service"], albums["session"]
    album_id = int(albums["hits_a"].id)
    album = session.get(Album, album_id)
    album.external_id_rows = id_rows("album", {RELEASE: "MPREb_x"})
    album.cover_hash = "ddd"
    session.commit()

    assert service.seed_cover_from_tracks(album_id) == ""
    assert session.get(Album, album_id).cover_hash == "ddd"

    service.apply_unified_cover(album_id, "bbb", [])
    assert session.get(Album, album_id).cover_hash == "bbb"


def test_seeding_ignores_a_visiting_track(albums):
    """Seeding from a visitor is how an album gets a sleeve none of its own carry.

    "Radio Ga Ga" belongs to Queen's Greatest Hits and carries that record's
    artwork, correctly. Put it first in Party Mix's running order and the old
    first-track-with-a-hash rule reads it — manufacturing the exact disagreement
    the warning exists to report.
    """
    service, session, repo = albums["service"], albums["session"], albums["repo"]
    comp_id = int(albums["comp"].id)
    odd_id = int(albums["odd"].id)

    # Party Mix's own track, filed *behind* the visitor so only the primary
    # filter can tell the two apart.
    own = Track(title="Filler", duration_seconds=150,
                artists=[albums["queen"]], cover_hash="ppp")
    own.albums = [session.get(Album, comp_id)]
    session.add(own)
    session.commit()

    repo.add_tracks(comp_id, [odd_id])          # lands at album_position 1
    repo.set_order(comp_id, [odd_id, int(own.id)])
    session.get(Album, comp_id).cover_hash = None
    session.commit()

    rows = {r["track_id"]: r["is_primary"] for r in repo.cover_rows(comp_id)}
    assert rows[odd_id] is False, "the fixture must make it an actual visitor"

    assert service.seed_cover_from_tracks(comp_id) == "ppp"
    assert session.get(Album, comp_id).cover_hash == "ppp"


# ---------------------------------------------------------------- completeness
#
# The one fact about an album that cannot be worked out from the library, so the
# tests below are as much about *when it is not written* as about when it is: a
# flag that sets itself on weak evidence is worse than no flag, because the user
# has no way to tell a checked record from one that got lucky.


def _mark_complete(albums, album_id):
    """Set the flag the way a real check would, and prove it landed."""
    members = albums["repo"].track_ids(album_id)
    assert albums["service"].record_release_check(
        album_id, release_count=len(members), owned_track_ids=members
    )
    assert albums["session"].get(Album, album_id).is_complete is True
    return members


def test_a_release_the_album_matches_exactly_is_marked_complete(albums):
    _mark_complete(albums, int(albums["hits_q"].id))


def test_an_album_longer_than_the_release_is_not_complete(albums):
    """An extra track is a thing to show the user, not to read past.

    Every track the release names is here, so conditions one and two both pass
    — and the record still has a fourth song the release never mentioned. A
    checkmark saying "this is the whole record" would be describing an album
    that is something else.
    """
    service, repo, session = albums["service"], albums["repo"], albums["session"]
    album_id = int(albums["hits_q"].id)
    members = repo.track_ids(album_id)
    assert len(members) == 4, "the fixture changed under this test"

    service.record_release_check(
        album_id, release_count=3, owned_track_ids=members[:3]
    )

    assert session.get(Album, album_id).is_complete is False


def test_a_track_the_release_names_and_we_lack_leaves_it_incomplete(albums):
    service, repo, session = albums["service"], albums["repo"], albums["session"]
    album_id = int(albums["hits_q"].id)
    members = repo.track_ids(album_id)

    service.record_release_check(
        album_id, release_count=len(members), owned_track_ids=members[:-1]
    )

    assert session.get(Album, album_id).is_complete is False


def test_a_track_owned_but_filed_on_another_record_does_not_count(albums):
    """The intersection is the whole point of `record_release_check`.

    `owned_track_ids` answers "is this recording in the library"; the rule asks
    "is it on *this* album". Here the counts line up perfectly — two tracks on
    the record, two of the release's tracks owned — and one of those two lives
    on a different album entirely. Without the intersection this would tick.
    """
    service, repo, session = albums["service"], albums["repo"], albums["session"]
    comp_id = int(albums["comp"].id)
    repo.add_tracks(comp_id, [int(albums["odd"].id)])
    assert len(repo.track_ids(comp_id)) == 2

    elsewhere = int(albums["hits_a"].id)
    owned = [int(albums["visitor"].id)] + repo.track_ids(elsewhere)

    service.record_release_check(comp_id, release_count=2, owned_track_ids=owned)

    assert session.get(Album, comp_id).is_complete is False


def test_a_fresh_listing_clears_a_flag_that_no_longer_holds(albums):
    """A listing just read beats one read a month ago."""
    service, session = albums["service"], albums["session"]
    album_id = int(albums["hits_q"].id)
    members = _mark_complete(albums, album_id)

    # The same record, re-read, now naming a track nobody has.
    service.record_release_check(
        album_id, release_count=len(members) + 1, owned_track_ids=members
    )

    assert session.get(Album, album_id).is_complete is False


def test_an_empty_listing_leaves_the_flag_exactly_as_it_was(albums):
    """A failed fetch is evidence of neither answer, and must un-tick nothing."""
    service, session = albums["service"], albums["session"]
    album_id = int(albums["hits_q"].id)
    _mark_complete(albums, album_id)

    assert service.record_release_check(
        album_id, release_count=0, owned_track_ids=[]
    ) is False

    assert session.get(Album, album_id).is_complete is True


def test_a_check_that_changes_nothing_announces_nothing(albums):
    """Re-checking a complete record is the ordinary case, not a repaint."""
    service, bus = albums["service"], albums["bus"]
    album_id = int(albums["hits_q"].id)
    members = _mark_complete(albums, album_id)

    seen: list = []
    bus.subscribe("album.updated", lambda album: seen.append(album))

    assert service.record_release_check(
        album_id, release_count=len(members), owned_track_ids=members
    ) is False
    assert seen == []


# --- and the membership rule that takes it away again -----------------------


def test_adding_a_track_clears_the_flag(albums):
    repo, session = albums["repo"], albums["session"]
    album_id = int(albums["hits_q"].id)
    _mark_complete(albums, album_id)

    # A track off the other record, joining this one.
    joining = repo.track_ids(int(albums["hits_a"].id))[0]
    repo.add_tracks(album_id, [joining])

    assert session.get(Album, album_id).is_complete is False


def test_removing_a_track_clears_the_flag(albums):
    repo, session = albums["repo"], albums["session"]
    album_id = int(albums["hits_q"].id)
    members = _mark_complete(albums, album_id)

    repo.remove_tracks(album_id, [members[0]])

    assert session.get(Album, album_id).is_complete is False


def test_a_split_clears_both_records(albums):
    """Neither half is the release the flag was checked against."""
    service, session = albums["service"], albums["session"]
    album_id = int(albums["hits_q"].id)
    members = _mark_complete(albums, album_id)

    result = service.split(album_id, members[:2])
    assert result.error == ""

    assert session.get(Album, album_id).is_complete is False
    assert session.get(Album, int(result.album.id)).is_complete is False


def test_reordering_does_not_clear_the_flag(albums):
    """A running order is not a membership, and reorder is how the flag is *set*."""
    repo, session = albums["repo"], albums["session"]
    album_id = int(albums["hits_q"].id)
    members = _mark_complete(albums, album_id)

    repo.set_order(album_id, list(reversed(members)))

    assert session.get(Album, album_id).is_complete is True


def test_making_a_track_primary_does_not_clear_the_flag(albums):
    """It rearranges the *track's* list of albums. No album gains or loses one."""
    repo, session = albums["repo"], albums["session"]
    hits_id = int(albums["hits_q"].id)
    comp_id = int(albums["comp"].id)
    _mark_complete(albums, hits_id)
    _mark_complete(albums, comp_id)

    assert repo.set_primary(int(albums["visitor"].id), hits_id) is True

    assert session.get(Album, hits_id).is_complete is True
    assert session.get(Album, comp_id).is_complete is True


def _track_service(albums):
    """A TrackService on the fixture's session, sharing its album repository.

    Two of the paths that change an album's membership never touch
    AlbumRepository at all — they assign `track.albums` through the ORM, and a
    delete loses its rows to ON DELETE CASCADE — so they have to be driven
    through the service that owns them.
    """
    from app.repositories.artist_repository import ArtistRepository
    from app.repositories.genre_repository import GenreRepository
    from app.repositories.track_repository import TrackRepository
    from app.services.track_service import TrackService

    session = albums["session"]
    return TrackService(
        TrackRepository(session), ArtistRepository(session),
        GenreRepository(session), albums["bus"],
        album_repo=albums["repo"],
    )


def test_deleting_a_track_clears_the_records_it_was_on(albums):
    """The cascade nothing in Python sees, so the ids are read before the delete."""
    session = albums["session"]
    hits_id = int(albums["hits_q"].id)
    comp_id = int(albums["comp"].id)
    _mark_complete(albums, hits_id)
    _mark_complete(albums, comp_id)

    # The visitor is on both, so one delete has to reach both records.
    assert _track_service(albums).delete_track(int(albums["visitor"].id)) is True

    assert session.get(Album, hits_id).is_complete is False
    assert session.get(Album, comp_id).is_complete is False


def test_naming_a_new_album_clears_the_one_the_track_joins(albums):
    """`_apply_albums` assigns the relationship directly; the repo never sees it.

    It adds a primary without evicting anything, so exactly one record changes
    size — the one being joined. Greatest Hits keeps the track and keeps its
    flag, which is the same asymmetry the method itself is built around.
    """
    session = albums["session"]
    hits_id = int(albums["hits_q"].id)
    comp_id = int(albums["comp"].id)
    _mark_complete(albums, hits_id)
    _mark_complete(albums, comp_id)

    _track_service(albums).update_track_metadata(
        int(albums["odd"].id), {"album": "Party Mix"}
    )

    assert session.get(Album, comp_id).is_complete is False, "the record it joined"
    assert session.get(Album, hits_id).is_complete is True, "nothing left this one"


def test_replacing_a_tracks_album_list_clears_both_sides(albums):
    """`_apply_album_refs` is the chip field: the list it is given *is* the answer.

    So a track can leave one record and join another in a single write, and the
    symmetric difference is what has to be cleared — not the union, or the album
    it was on before and is still on would lose a flag it has not stopped
    earning.
    """
    session = albums["session"]
    hits_q_id = int(albums["hits_q"].id)
    hits_a_id = int(albums["hits_a"].id)
    _mark_complete(albums, hits_q_id)
    _mark_complete(albums, hits_a_id)

    _track_service(albums).update_track_metadata(
        int(albums["odd"].id),
        {"album_refs": [{"id": hits_a_id, "name": "Greatest Hits"}]},
    )

    assert session.get(Album, hits_q_id).is_complete is False, "it left this one"
    assert session.get(Album, hits_a_id).is_complete is False, "it joined this one"


def test_re_saving_a_track_that_did_not_move_leaves_the_flag_alone(albums):
    """Only a track genuinely joining is a membership change.

    An ordinary metadata fix re-writes the album field with the value it already
    had, and a record must not lose its checkmark to a typo correction in a
    title.
    """
    session = albums["session"]
    hits_a_id = int(albums["hits_a"].id)
    _mark_complete(albums, hits_a_id)
    member = albums["repo"].track_ids(hits_a_id)[0]

    _track_service(albums).update_track_metadata(
        member, {"title": "Dancing Queen (2024 Remaster)", "album": "Greatest Hits"}
    )

    assert session.get(Album, hits_a_id).is_complete is True


# ---------------------------------------------------------------- the claim
#
# The other half of what a release listing proves. `split_owned` is the line
# both features are drawn on — completeness counts what is on the album, the
# claim is offered over what is not — and `claim_tracks` is what accepting it
# writes.
#
# One assertion carries this whole feature: the claimed track keeps the record
# it belongs to. Everything else here is the arrangement around it.


def _claiming_service(albums):
    """The album service with the track lookup a reorder needs.

    The fixture's own service has none — nothing else above reorders — and
    `claim_tracks` ends in one, because putting a track on a record and putting
    it in the right *place* on that record are a single gesture.
    """
    service = albums["service"]
    service.track_service = _track_service(albums)
    return service


def _release(*titles):
    """A release listing, in the shape `fetch_album_tracks` answers with.

    Party Mix's members carry no external id — a compilation assembled out of
    local rips — so a reorder over this exercises the title pass, which is the
    pass that exists for exactly that album.
    """
    return [
        {"title": title, "external_ids": {}, "alternate_ids": {}, "duration": 200}
        for title in titles
    ]


def test_split_owned_divides_the_library_by_this_albums_membership(albums):
    """The one statement of "on the album or not", asked of both answers."""
    service = albums["service"]
    visitor_id, odd_id = int(albums["visitor"].id), int(albums["odd"].id)

    on_album, off_album = service.split_owned(
        int(albums["comp"].id), [visitor_id, odd_id]
    )

    assert on_album == [visitor_id]
    assert off_album == [odd_id]


def test_split_owned_drops_the_gaps_and_keeps_the_order(albums):
    """`owned_track_ids` answers positionally, so the input is full of holes.

    A row the library has nothing for carries `None`, a recording named twice by
    one release answers twice, and the order is the release's — which is what
    lets the caller name the tracks off its own rows.
    """
    service = albums["service"]
    odd_id = int(albums["odd"].id)
    other_id = albums["repo"].track_ids(int(albums["hits_a"].id))[0]

    on_album, off_album = service.split_owned(
        int(albums["comp"].id),
        [None, odd_id, int(albums["visitor"].id), other_id, odd_id, 0],
    )

    assert on_album == [int(albums["visitor"].id)]
    assert off_album == [odd_id, other_id]


def test_a_claimed_track_keeps_the_album_it_belongs_to(albums):
    """The central assertion of the feature.

    "Radio Ga Ga" is Queen's Greatest Hits' track and appears on the
    compilation. Claiming it must leave it filed where it lives — the claim
    joins a record, it does not move house.
    """
    service, session = _claiming_service(albums), albums["session"]
    comp_id, odd_id = int(albums["comp"].id), int(albums["odd"].id)

    assert service.claim_tracks(
        comp_id, [odd_id], _release("Radio Ga Ga", "Guest")
    ) == 1

    odd = session.get(Track, odd_id)
    assert odd.album == "Greatest Hits", "its primary album moved"
    positions = {
        row["track_id"]: row["is_primary"] for row in albums["repo"].cover_rows(comp_id)
    }
    assert positions[odd_id] is False, "the claim made this album primary"


def test_a_track_with_no_album_at_all_gains_this_one(albums):
    """A track with albums must have a primary; a track with none gets one.

    The one case where a claim does decide where a track lives, because nothing
    else had.
    """
    service, session = _claiming_service(albums), albums["session"]
    homeless = Track(title="Orphan", duration_seconds=200, artists=[albums["queen"]])
    session.add(homeless)
    session.commit()
    comp_id = int(albums["comp"].id)

    service.claim_tracks(comp_id, [int(homeless.id)], _release("Guest", "Orphan"))

    session.refresh(homeless)
    assert homeless.album == "Party Mix"


def test_the_claim_leaves_the_album_in_the_releases_order(albums):
    """The reorder is why the listing is passed through rather than the ids.

    Appended alone, a claimed track lands last whatever the record says. The
    caller is still holding the release, so placing it properly costs nothing.
    """
    service = _claiming_service(albums)
    comp_id, odd_id = int(albums["comp"].id), int(albums["odd"].id)

    service.claim_tracks(comp_id, [odd_id], _release("Radio Ga Ga", "Guest"))

    assert albums["repo"].track_ids(comp_id) == [odd_id, int(albums["visitor"].id)]


def test_a_claim_that_completes_a_record_leaves_it_ticked(albums):
    """The trap the call order exists to repair.

    `AlbumRepository.add_tracks` clears the flag on any membership change, which
    is right in general and wrong for the one write that can only ever make a
    record *more* whole. The reorder re-decides it from an exact match count.
    """
    service, session = _claiming_service(albums), albums["session"]
    comp_id, odd_id = int(albums["comp"].id), int(albums["odd"].id)

    service.claim_tracks(comp_id, [odd_id], _release("Radio Ga Ga", "Guest"))

    assert session.get(Album, comp_id).is_complete is True


def test_a_claim_against_a_listing_that_matches_nothing_does_not_tick(albums):
    """The fallback path, and the only answer it is entitled to give.

    Nothing on the album is on that release, so there is no evidence the record
    is whole — and the flag `add_tracks` cleared stays cleared.
    """
    service, session = _claiming_service(albums), albums["session"]
    comp_id, odd_id = int(albums["comp"].id), int(albums["odd"].id)
    _mark_complete(albums, comp_id)

    service.claim_tracks(comp_id, [odd_id], _release("Something Else Entirely"))

    assert albums["repo"].track_ids(comp_id) == [int(albums["visitor"].id), odd_id]
    assert session.get(Album, comp_id).is_complete is False


def test_claiming_a_track_the_album_already_holds_adds_nothing(albums):
    """The count is what the toast quotes, so it says what was actually written."""
    service = _claiming_service(albums)

    assert service.claim_tracks(
        int(albums["comp"].id), [int(albums["visitor"].id)], _release("Guest")
    ) == 0
