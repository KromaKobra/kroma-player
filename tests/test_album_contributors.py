"""Who is credited on an album, and in what order the chips say it.

Modelled on tests/test_albums.py, and it exists for one reason: the ordering is
the only part of this feature with any logic in it, and every bit of that logic
is invisible. The count is obvious; the tie-break is two stable sorts and an
insertion-ordered dict agreeing with each other, and nothing about the code says
so out loud. A "simplification" that adds a secondary sort key, or that sorts the
repository's rows, breaks the tie-break and breaks nothing else — no exception,
no wrong count, just a chip in the wrong place that nobody notices.

So the cases here are mostly about ties. There are two kinds, and they are broken
by two different stored orderings:

- two artists on the same number of tracks, first met on *different* tracks —
  the album's running order decides;
- two artists first met on the *same* track — that track's credit order decides.

The third case worth pinning is the visiting track. An album's artwork rules
deliberately ignore tracks the album is not primary for, and the obvious thing to
do when writing this feature was to copy that filter. Credits are the other
answer: a single visiting from another record is still on this one.

`is_main` came later and is the one judgement in the method rather than a count:
an artist on strictly more than half the record. Its two invisible halves are the
boundary — exactly half is not a majority — and the denominator, which is every
track on the album and not every track somebody is credited on. Both are the kind
of thing a reasonable-looking rewrite gets wrong silently, so both are below.
"""
from __future__ import annotations

import pytest

from app.database.connection import DatabaseManager
from app.events.event_bus import EventBus
from app.models.track import Album, Artist, Track
from app.repositories.album_repository import AlbumRepository
from app.repositories.track_repository import (
    write_album_positions,
    write_artist_positions,
)
from app.services.album_service import AlbumService


@pytest.fixture
def credits(tmp_path):
    """One album with a deliberate tie in it, plus a duet and an empty record.

    `mixtape` is arranged so that every artist count is either unique or tied
    with exactly one other, and the two tied artists are first met on tracks far
    apart — so moving one track changes the answer and nothing else does.
    """
    manager = DatabaseManager.from_path(tmp_path / "tracks.db")
    session = manager.create_session()

    ada = Artist(name="Ada")
    bo = Artist(name="Bo")
    cy = Artist(name="Cy")
    dee = Artist(name="Dee")
    zed = Artist(name="Zed")
    yan = Artist(name="Yan")
    session.add_all([ada, bo, cy, dee, zed, yan])
    session.flush()

    mixtape = Album(name="Mixtape")
    single = Album(name="Duet")
    elsewhere = Album(name="Elsewhere")
    empty = Album(name="Nothing On It")
    session.add_all([mixtape, single, elsewhere, empty])
    session.flush()

    def track(title, artists, album):
        row = Track(title=title, duration_seconds=200, artists=list(artists))
        row.albums = [album]
        session.add(row)
        return row

    one = track("One", [ada, bo], mixtape)
    two = track("Two", [cy], mixtape)
    three = track("Three", [bo, ada], mixtape)
    four = track("Four", [ada], mixtape)
    duet = track("Duet", [zed, yan], single)

    # Primary to `elsewhere`, but also on the mixtape. The case the artwork
    # code filters out and this one must not.
    guest = Track(title="Guest", duration_seconds=180, artists=[dee])
    guest.albums = [elsewhere, mixtape]
    session.add(guest)
    session.flush()

    # Credit order is the half SQLAlchemy cannot write. Without this every
    # `position` is 0 and the second tie-break below would be testing nothing.
    write_artist_positions(session, {
        int(one.id): [int(ada.id), int(bo.id)],
        int(three.id): [int(bo.id), int(ada.id)],
        int(duet.id): [int(zed.id), int(yan.id)],
    })
    write_album_positions(session, {
        int(guest.id): [int(elsewhere.id), int(mixtape.id)],
    })
    session.commit()

    repo = AlbumRepository(session)
    for album in (mixtape, single, elsewhere):
        repo.set_order(int(album.id), repo.track_ids(int(album.id)))
    session.commit()

    service = AlbumService(repo, EventBus())
    yield {
        "session": session, "repo": repo, "service": service,
        "mixtape": mixtape, "single": single, "empty": empty,
        "ada": ada, "bo": bo, "cy": cy, "dee": dee, "zed": zed, "yan": yan,
        "one": one, "two": two, "three": three, "four": four, "guest": guest,
    }
    session.close()


def names(rows):
    return [row["name"] for row in rows]


# ------------------------------------------------------------------- counting


def test_counts_every_credit_on_the_album(credits):
    service, mixtape = credits["service"], credits["mixtape"]
    counted = {r["name"]: r["track_count"]
               for r in service.contributing_artists(int(mixtape.id))}
    assert counted == {"Ada": 3, "Bo": 2, "Cy": 1, "Dee": 1}


def test_most_tracks_first(credits):
    service, mixtape = credits["service"], credits["mixtape"]
    ordered = names(service.contributing_artists(int(mixtape.id)))
    assert ordered[:2] == ["Ada", "Bo"]


def test_a_visiting_track_still_credits_its_artist(credits):
    """The album's artwork rules ignore these tracks. Its credits must not.

    Dee is on nothing but a track primary to another record. Copying the
    artwork code's primary-only filter would drop them entirely, and the track
    count beside the chips would be counting a track whose artist was nowhere.
    """
    service, mixtape = credits["service"], credits["mixtape"]
    assert "Dee" in names(service.contributing_artists(int(mixtape.id)))


def test_rows_carry_the_artist_id_and_portrait(credits):
    """The chip needs both: one to navigate with, one to draw."""
    service, mixtape = credits["service"], credits["mixtape"]
    top = service.contributing_artists(int(mixtape.id))[0]
    assert top["artist_id"] == int(credits["ada"].id)
    assert set(top) == {"artist_id", "name", "image_hash", "track_count", "is_main"}


# --------------------------------------------------------------- who is a main


def test_more_than_half_the_tracks_makes_an_artist_main(credits):
    """The mixtape is five tracks: Ada is on three of them and nobody else is.

    Bo's two are the case worth having in the same assertion — second place on
    a record is not the same question as a majority of it.
    """
    service, mixtape = credits["service"], credits["mixtape"]
    rows = service.contributing_artists(int(mixtape.id))
    assert {r["name"]: r["is_main"] for r in rows} == {
        "Ada": True, "Bo": False, "Cy": False, "Dee": False,
    }


def test_exactly_half_the_tracks_is_not_main(credits):
    """The boundary, and the only thing "more than half" actually claims.

    A two-hander where each takes one side has no main artist rather than two,
    which is the answer that keeps the flag meaning *majority* — a word that
    stops being worth anything the moment two people can both hold it against
    each other.
    """
    session, repo = credits["session"], credits["repo"]
    split = Album(name="Split")
    session.add(split)
    session.flush()
    for title, artist in (("Side A", credits["ada"]), ("Side B", credits["bo"])):
        row = Track(title=title, duration_seconds=200, artists=[artist])
        row.albums = [split]
        session.add(row)
    session.flush()
    repo.set_order(int(split.id), repo.track_ids(int(split.id)))
    session.commit()

    rows = credits["service"].contributing_artists(int(split.id))
    assert [r["track_count"] for r in rows] == [1, 1]
    assert not any(r["is_main"] for r in rows)


def test_both_halves_of_a_duet_lead_it(credits):
    """One track, two names on it: each is on the whole record, so each leads.

    The same arithmetic as the split above and the opposite answer, which is
    the pair worth reading together — one track each out of two is half, and
    one track each out of one is all of it.
    """
    rows = credits["service"].contributing_artists(int(credits["single"].id))
    assert [(r["name"], r["is_main"]) for r in rows] == [("Zed", True), ("Yan", True)]


def test_a_track_with_no_credits_still_counts_against_a_majority(credits):
    """The denominator is every track, not every track anybody is credited on.

    Counting the rows this method already has would be the obvious
    simplification and is wrong by exactly this case: a track with no artist on
    it is still a track Ada is not on. So the count comes from
    `AlbumRepository.track_count()`, which is the same number the hero prints
    beside these chips.
    """
    session, repo = credits["session"], credits["repo"]
    sparse = Album(name="Sparse")
    session.add(sparse)
    session.flush()
    for title, artists in (("One", [credits["ada"]]), ("Two", [credits["ada"]]),
                           ("Three", []), ("Four", [])):
        row = Track(title=title, duration_seconds=200, artists=list(artists))
        row.albums = [sparse]
        session.add(row)
    session.flush()
    repo.set_order(int(sparse.id), repo.track_ids(int(sparse.id)))
    session.commit()

    rows = credits["service"].contributing_artists(int(sparse.id))
    assert [(r["name"], r["track_count"]) for r in rows] == [("Ada", 2)]
    # Two of four, not two of two.
    assert not rows[0]["is_main"]


def test_the_main_artists_are_a_leading_run(credits):
    """Never a main artist behind a guest, whatever the record.

    Not a restatement of the sort: it is the consequence the GUI leans on. A
    main artist's count is more than half and every other count is at most
    half, so ordering by count alone already puts all of them first — which is
    what lets the chips draw them larger without a large face ever turning up
    behind a small one.
    """
    service = credits["service"]
    for key in ("mixtape", "single"):
        flags = [r["is_main"] for r in service.contributing_artists(int(credits[key].id))]
        assert flags == sorted(flags, reverse=True), key


# ------------------------------------------------------------------ tie-breaks


def test_a_tie_goes_to_whoever_the_running_order_meets_first(credits):
    """Cy and Dee are on one track each. Cy's is earlier, so Cy is first."""
    service, mixtape = credits["service"], credits["mixtape"]
    ordered = names(service.contributing_artists(int(mixtape.id)))
    assert ordered.index("Cy") < ordered.index("Dee")


def test_reordering_the_album_reorders_a_tie(credits):
    """The tie-break is the *stored* running order, not the order of ids.

    This is the assertion that fails if the sort grows a secondary key. Nothing
    about the library changed here except which track is first.
    """
    service, repo, mixtape = credits["service"], credits["repo"], credits["mixtape"]
    before = names(service.contributing_artists(int(mixtape.id)))
    assert before.index("Cy") < before.index("Dee")

    moved = [int(credits["guest"].id)] + [
        int(credits[key].id) for key in ("one", "two", "three", "four")
    ]
    repo.set_order(int(mixtape.id), moved)
    credits["session"].commit()

    after = names(service.contributing_artists(int(mixtape.id)))
    assert after.index("Dee") < after.index("Cy")
    # The untied pair are untouched: a tie-break is all that moved.
    assert after[:2] == ["Ada", "Bo"]


def test_a_tie_on_one_track_goes_to_the_credit_order(credits):
    """Zed and Yan share their only track, so the track's own credits decide."""
    service, single = credits["service"], credits["single"]
    assert names(service.contributing_artists(int(single.id))) == ["Zed", "Yan"]


def test_recrediting_the_track_reorders_that_tie(credits):
    """The tie-break is the *stored* credit order, not the order of ids."""
    service, session, single = credits["service"], credits["session"], credits["single"]
    duet_id = credits["repo"].track_ids(int(single.id))[0]
    write_artist_positions(session, {
        duet_id: [int(credits["yan"].id), int(credits["zed"].id)],
    })
    session.commit()

    assert names(service.contributing_artists(int(single.id))) == ["Yan", "Zed"]


# ----------------------------------------------------------------- empty cases


def test_an_album_with_no_tracks_has_no_contributors(credits):
    service, empty = credits["service"], credits["empty"]
    assert service.contributing_artists(int(empty.id)) == []


def test_an_album_that_does_not_exist_answers_empty(credits):
    """Rather than raising. The page asks before it knows what is open."""
    assert credits["service"].contributing_artists(999999) == []
