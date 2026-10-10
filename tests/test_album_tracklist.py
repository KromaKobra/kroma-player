"""Arranging an album to match its release's track list.

What a release contains is a plugin's to read — the real one's tests pin which
of its catalogue's lists it reads and why — and it arrives here as rows in the
order the release runs. ``reorder_from_release`` turns that order into
``track_albums.position``.

The cases worth pinning are the ones where doing the obvious thing is wrong:
the order must survive (nothing may sort), a track the library has but that is
not on *this* album must not be dragged onto it, a track the release does not
name must end up last rather than nowhere, an alternate id names the same
recording as the row's own, and a title may never overrule an id.
"""
from __future__ import annotations

import pytest

from app.database.connection import DatabaseManager
from app.events.event_bus import EventBus
from app.models.track import Album, Artist, Track
from app.repositories.album_repository import AlbumRepository
from app.repositories.track_repository import TrackRepository, write_album_positions
from app.services.album_service import AlbumService
from tests.fakes.providers import RELEASE, VIDEO, FakeMetadata, FakeRegistry, id_rows

BROWSE_ID = "MPREb_abcdefghijklm"


# ---------------------------------------------------------------------------
# reorder_from_release — the order, applied
# ---------------------------------------------------------------------------

def _release(*rows):
    """The parts of a release listing's rows a reorder actually reads.

    A bare string is a position that carries only its own id — which is what
    every case written before alternates existed meant, and what they still
    mean now.
    """
    built = []
    for row in rows:
        if isinstance(row, str):
            row = {"id": row}
        own = row.get("id", "")
        alternates = list(row.get("alt_ids", ()))
        built.append({
            "title": row.get("title", ""),
            "external_ids": {VIDEO: own} if own else {},
            "alternate_ids": {VIDEO: alternates} if alternates else {},
            "duration": row.get("duration", 0),
        })
    return built


class StubTrackService:
    """The one read the reorder needs beyond the ids, over a real repository."""

    def __init__(self, session):
        self._repo = TrackRepository(session)

    def get_tracks_by_ids(self, ids):
        return self._repo.get_by_ids(ids)


@pytest.fixture
def record(tmp_path):
    """One album, stored in the reverse of its real running order.

    Plus a single that is on this record *and* on a compilation it is primary
    for, and a bonus track the release has never heard of.
    """
    manager = DatabaseManager.from_path(tmp_path / "tracks.db")
    session = manager.create_session()

    artist = Artist(name="Daft Punk")
    session.add(artist)
    session.flush()

    album = Album(name="Discovery", external_id_rows=id_rows("album", {RELEASE: BROWSE_ID}))
    comp = Album(name="Club Mix")
    session.add_all([album, comp])
    session.flush()

    def track(title, video, albums):
        row = Track(
            title=title, duration_seconds=200, artists=[artist],
            external_id_rows=id_rows("track", {VIDEO: video}),
        )
        row.albums = list(albums)
        session.add(row)
        return row

    # Stored backwards on purpose: arrival order, which is what a library
    # assembled one download at a time actually ends up with.
    aero = track("Aerodynamic", "vid2", [album])
    one_more = track("One More Time", "vid1", [album])
    bonus = track("Alive 2007", "vidX", [album])
    # A visitor's primary album is the compilation, not this one.
    visitor = track("Around the World", "vid9", [comp, album])
    # On the release, but the library has never downloaded it.
    session.commit()

    repo = AlbumRepository(session)
    write_album_positions(session, {int(visitor.id): [int(comp.id), int(album.id)]})
    for row in (album, comp):
        repo.set_order(int(row.id), repo.track_ids(int(row.id)))
    session.commit()

    bus = EventBus()
    service = AlbumService(repo, bus, track_service=StubTrackService(session))
    yield {
        "session": session, "repo": repo, "service": service, "bus": bus,
        "album": album, "comp": comp,
        "aero": aero, "one_more": one_more, "bonus": bonus, "visitor": visitor,
    }
    session.close()


def test_the_album_takes_the_release_order(record):
    repo, album = record["repo"], record["album"]
    before = repo.track_ids(int(album.id))
    assert before[0] == int(record["aero"].id)

    outcome = record["service"].reorder_from_release(
        int(album.id), _release("vid1", "vid2")
    )

    assert outcome.error == ""
    assert outcome.moved is True
    after = repo.track_ids(int(album.id))
    assert after[:2] == [int(record["one_more"].id), int(record["aero"].id)]


def test_a_track_the_release_does_not_name_lands_last(record):
    """And is reported, so the user is not left to notice it moved."""
    album = record["album"]

    outcome = record["service"].reorder_from_release(
        int(album.id), _release("vid1", "vid2")
    )

    # The two the release named, then the two it did not, in the order they
    # already sat in. `AlbumRepository.set_order` does that half by itself.
    assert record["repo"].track_ids(int(album.id)) == [
        int(record["one_more"].id),
        int(record["aero"].id),
        int(record["bonus"].id),
        int(record["visitor"].id),
    ]
    assert outcome.unmatched == [int(record["bonus"].id), int(record["visitor"].id)]


def test_an_id_the_library_does_not_have_is_ignored(record):
    """A missing track narrows what gets renumbered; it never inserts a gap."""
    album = record["album"]

    outcome = record["service"].reorder_from_release(
        int(album.id), _release("vid1", "vidNOTMINE", "vid2")
    )

    assert outcome.matched == [int(record["one_more"].id), int(record["aero"].id)]


def test_a_track_on_another_album_is_not_dragged_onto_this_one(record):
    """The compilation's own tracks must stay out of it."""
    album, comp = record["album"], record["comp"]
    other = Track(
        title="Stranger", duration_seconds=100,
        external_id_rows=id_rows("track", {VIDEO: "vidOther"}),
    )
    other.albums = [comp]
    record["session"].add(other)
    record["session"].commit()

    outcome = record["service"].reorder_from_release(
        int(album.id), _release("vidOther", "vid1")
    )

    assert int(other.id) not in outcome.matched
    assert int(other.id) not in record["repo"].track_ids(int(album.id))


def test_running_it_twice_reports_that_nothing_moved(record):
    album = record["album"]
    order = _release("vid1", "vid2", "vidX", "vid9")

    assert record["service"].reorder_from_release(int(album.id), order).moved is True
    second = record["service"].reorder_from_release(int(album.id), order)

    assert second.error == ""
    assert second.moved is False


def test_nothing_matching_is_refused_rather_than_silently_doing_nothing(record):
    """A wrong browse id, or a record of local rips. Both land here."""
    album = record["album"]
    before = record["repo"].track_ids(int(album.id))

    outcome = record["service"].reorder_from_release(
        int(album.id), _release("nope1", "nope2")
    )

    assert outcome.error
    assert outcome.matched == []
    assert record["repo"].track_ids(int(album.id)) == before


def test_a_track_is_recognised_under_its_alternate_id(record):
    """The library downloaded the video; the release names the audio.

    Same recording, two ids. Before the row carried both, an album like this
    matched nothing at all and the reorder refused outright.
    """
    album = record["album"]

    outcome = record["service"].reorder_from_release(int(album.id), _release(
        {"title": "One More Time", "id": "atv1", "alt_ids": ["vid1"]},
        {"title": "Aerodynamic", "id": "atv2", "alt_ids": ["vid2"]},
    ))

    assert outcome.error == ""
    assert outcome.matched == [int(record["one_more"].id), int(record["aero"].id)]


def test_a_track_that_never_came_from_a_catalogue_is_matched_by_title(record):
    """A local rip has no id to be matched by, and a reorder must still work.

    Safe only because the scope is this album's own remaining tracks — a dozen
    titles against a dozen the user has already said belong to this record.
    """
    session, album = record["session"], record["album"]
    rip = Track(title="Veridis Quo", duration_seconds=100)
    rip.albums = [album]
    session.add(rip)
    session.commit()

    outcome = record["service"].reorder_from_release(int(album.id), _release(
        {"title": "Veridis Quo", "id": ""},
        {"title": "One More Time", "id": "vid1"},
    ))

    assert outcome.matched == [int(rip.id), int(record["one_more"].id)]


def test_an_id_match_beats_a_title_match_for_the_same_track(record):
    """Both passes want One More Time. The id must get it, and get it first.

    Interleaved, the title guess at position 0 would claim the track before the
    position whose *id* names it was ever considered — and then position 1 would
    fall through to a title of its own. An id is evidence; a title is a guess.
    """
    album = record["album"]

    outcome = record["service"].reorder_from_release(int(album.id), _release(
        {"title": "One More Time", "id": ""},
        {"title": "Aerodynamic", "id": "vid1"},
    ))

    # `vid1` *is* One More Time, whatever this release calls that position, so
    # position 1 takes it and position 0 is left with nothing to claim.
    assert outcome.matched == [int(record["one_more"].id)]


def test_a_title_two_of_the_albums_tracks_share_matches_neither(record):
    """A version and its remix filed under one name. Picking one is worse than none."""
    session, album = record["session"], record["album"]
    for _ in range(2):
        row = Track(title="Short Circuit", duration_seconds=100)
        row.albums = [album]
        session.add(row)
    session.commit()

    outcome = record["service"].reorder_from_release(int(album.id), _release(
        {"title": "Short Circuit", "id": ""},
        {"title": "One More Time", "id": "vid1"},
    ))

    assert outcome.matched == [int(record["one_more"].id)]


def test_duration_tells_two_tracks_of_one_name_apart(record):
    """A version and its remix. The title cannot separate them; the length can."""
    session, album = record["session"], record["album"]
    for seconds in (215, 402):
        row = Track(title="Short Circuit", duration_seconds=seconds)
        row.albums = [album]
        session.add(row)
    session.commit()

    outcome = record["service"].reorder_from_release(int(album.id), _release(
        {"title": "Short Circuit", "id": "", "duration": 403},
        {"title": "One More Time", "id": "vid1"},
    ))

    remix = next(
        t for t in record["service"].track_service.get_tracks_by_ids(
            record["repo"].track_ids(int(album.id))
        ) if t.duration_seconds == 402
    )
    # Within the two-second tolerance, and the only candidate that is.
    assert outcome.matched == [int(remix.id), int(record["one_more"].id)]


def test_two_candidates_both_in_range_still_match_nothing(record):
    """The length has stopped separating them, so there is nothing to decide on."""
    session, album = record["session"], record["album"]
    for seconds in (215, 216):
        row = Track(title="Short Circuit", duration_seconds=seconds)
        row.albums = [album]
        session.add(row)
    session.commit()

    outcome = record["service"].reorder_from_release(int(album.id), _release(
        {"title": "Short Circuit", "id": "", "duration": 215},
        {"title": "One More Time", "id": "vid1"},
    ))

    assert outcome.matched == [int(record["one_more"].id)]


def test_a_feat_credit_in_the_title_does_not_block_a_match(record):
    """Which side spells the credit out is not information about the song."""
    session, album = record["session"], record["album"]
    rip = Track(title="Digital Love", duration_seconds=301)
    rip.albums = [album]
    session.add(rip)
    session.commit()

    outcome = record["service"].reorder_from_release(int(album.id), _release(
        {"title": "Digital Love (feat. Todd Edwards)", "id": "", "duration": 301},
    ))

    assert outcome.matched == [int(rip.id)]


def test_a_title_the_release_names_twice_matches_nothing(record):
    """The same rule from the other side, and it has to hold there too."""
    session, album = record["session"], record["album"]
    rip = Track(title="Veridis Quo", duration_seconds=100)
    rip.albums = [album]
    session.add(rip)
    session.commit()

    outcome = record["service"].reorder_from_release(int(album.id), _release(
        {"title": "Veridis Quo", "id": ""},
        {"title": "Veridis Quo", "id": ""},
        {"title": "One More Time", "id": "vid1"},
    ))

    assert int(rip.id) not in outcome.matched
    assert outcome.matched == [int(record["one_more"].id)]


def test_the_reorder_announces_itself(record):
    """`album.reordered` is what makes the open track list catch up."""
    seen: list[int] = []
    record["bus"].subscribe("album.reordered", seen.append)

    record["service"].reorder_from_release(
        int(record["album"].id), _release("vid1", "vid2")
    )

    assert seen == [int(record["album"].id)]


# ---------------------------------------------------------------------------
# What the album editor reports back
# ---------------------------------------------------------------------------

@pytest.fixture
def editor(record, qt_app):
    """The view model, opened on the identified album.

    The worker is never started — these tests hand `_on_tracklist_fetched` the
    rows it would have delivered, because what is worth pinning is the write and
    the report, not the fetch in front of them.
    """
    from app.viewmodels.album_edit_view_model import AlbumEditViewModel

    vm = AlbumEditViewModel(record["service"], registry=FakeRegistry(metadata=FakeMetadata()))
    vm.load(int(record["album"].id))
    return vm


def _fetched(*video_ids):
    return [{"external_ids": {VIDEO: v}, "title": v} for v in video_ids]


def test_the_report_names_the_tracks_the_release_did_not(record, editor):
    reports: list = []
    editor.reorder_completed.connect(lambda *args: reports.append(args))

    editor._on_tracklist_fetched(_fetched("vid1", "vid2"))

    (matched, moved, unmatched), = reports
    assert matched == 2
    assert moved is True
    assert unmatched == ["Alive 2007", "Around the World"]


def test_an_album_already_in_order_says_so_rather_than_failing(record, editor):
    errors: list[str] = []
    editor.error_occurred.connect(errors.append)
    reports: list = []
    editor.reorder_completed.connect(lambda *args: reports.append(args))
    order = ["vid1", "vid2", "vidX", "vid9"]

    editor._on_tracklist_fetched(_fetched(*order))
    editor._on_tracklist_fetched(_fetched(*order))

    assert errors == []
    assert [moved for _m, moved, _u in reports] == [True, False]


def test_a_release_nothing_matches_is_reported_as_an_error(record, editor):
    errors: list[str] = []
    editor.error_occurred.connect(errors.append)
    reports: list = []
    editor.reorder_completed.connect(lambda *args: reports.append(args))

    editor._on_tracklist_fetched(_fetched("nope1", "nope2"))

    assert reports == []
    assert errors and "release" in errors[0]


def test_reordering_without_an_id_is_refused_before_a_worker_exists(record, qt_app):
    from app.viewmodels.album_edit_view_model import AlbumEditViewModel

    vm = AlbumEditViewModel(record["service"], registry=FakeRegistry(metadata=FakeMetadata()))
    vm.load(int(record["comp"].id))       # the compilation has no id
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)

    assert vm.canReorder is False and vm.canFindTracks is False
    vm.reorderFromSource()

    assert errors == ["This album has no id to take an order from"]
    assert vm.isBusy is False


def test_reorder_and_find_tracks_need_a_plugin_that_lists_releases(record, qt_app):
    from app.viewmodels.album_edit_view_model import AlbumEditViewModel

    registry = FakeRegistry(metadata=FakeMetadata())
    vm = AlbumEditViewModel(record["service"], registry=registry)
    vm.load(int(record["album"].id))
    assert vm.canReorder is True and vm.canFindTracks is True

    registry.set(metadata_provider=None)

    assert vm.canReorder is False and vm.canFindTracks is False


# ---------------------------------------------------------------------------
# The completeness the reorder already knows
# ---------------------------------------------------------------------------
#
# A reorder has done the expensive half of a completeness check on its way past:
# `ordered` is release tracks matched to tracks *on this album*, which is exactly
# what the rule asks for and is more exact than the import tab's route to the
# same answer. These pin that it is spent rather than discarded.


def test_a_release_that_names_every_track_marks_the_album_complete(record):
    session, album = record["session"], record["album"]
    order = ["vid1", "vid2", "vidX", "vid9"]
    assert len(record["repo"].track_ids(int(album.id))) == len(order)

    record["service"].reorder_from_release(int(album.id), _release(*order))

    assert session.get(Album, int(album.id)).is_complete is True


def test_tracks_the_release_never_named_leave_it_incomplete(record):
    """The bonus track and the visitor are both still on the record."""
    session, album = record["session"], record["album"]

    outcome = record["service"].reorder_from_release(
        int(album.id), _release("vid1", "vid2")
    )
    assert len(outcome.unmatched) == 2

    assert session.get(Album, int(album.id)).is_complete is False


def test_a_release_longer_than_the_album_leaves_it_incomplete(record):
    """Four tracks here, five on the release — one of them nobody has."""
    session, album = record["session"], record["album"]

    record["service"].reorder_from_release(
        int(album.id), _release("vid1", "vid2", "vidX", "vid9", "vidMissing")
    )

    assert session.get(Album, int(album.id)).is_complete is False


def test_a_reorder_that_matches_nothing_writes_no_verdict(record):
    """It is refused outright, and a refusal must not un-tick a checked record."""
    session, album, service = record["session"], record["album"], record["service"]
    order = ["vid1", "vid2", "vidX", "vid9"]
    service.reorder_from_release(int(album.id), _release(*order))
    assert session.get(Album, int(album.id)).is_complete is True

    outcome = service.reorder_from_release(int(album.id), _release("nope1", "nope2"))
    assert outcome.error

    assert session.get(Album, int(album.id)).is_complete is True


# --- and the same flag, set by hand on the edit page -------------------------


def test_the_editor_stages_the_flag_and_saves_it(record, editor):
    session, album = record["session"], record["album"]
    assert editor.isComplete is False
    assert editor.isDirty is False

    editor.isComplete = True

    assert editor.isDirty is True, "the tick has to arm Save like any other field"
    assert session.get(Album, int(album.id)).is_complete is False, "not yet written"

    editor.save()

    assert session.get(Album, int(album.id)).is_complete is True
    assert editor.isDirty is False


def test_reverting_discards_a_staged_tick(record, editor):
    editor.isComplete = True
    editor.revert()

    assert editor.isComplete is False
    assert editor.isDirty is False
    assert record["session"].get(Album, int(record["album"].id)).is_complete is False


def test_the_editor_can_take_the_flag_back_off(record, editor):
    """A user who ticked a record wrongly has to be able to un-tick it."""
    session, album = record["session"], record["album"]
    editor.isComplete = True
    editor.save()

    editor.isComplete = False
    editor.save()

    assert session.get(Album, int(album.id)).is_complete is False
