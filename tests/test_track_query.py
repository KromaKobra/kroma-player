"""Tests for the demand-driven track list model.

These are mostly about *cost*: how many queries a thing takes, and whether
anything is read before it is painted. Those are the properties the design
rests on, and they are invisible to a test that only checks return values.
"""
from __future__ import annotations

import pytest

from app.library.fields import Tier, catalog
from app.library.roles import FIELD_TO_ROLE
from app.library.list_model import FLUSH_MS, PAGE_SIZE, SETTLE_MS, _TIER_ROLES
from app.library.track_query import TrackQuery

from conftest import TRACK_COUNT

TITLE = FIELD_TO_ROLE["title"]
ARTIST = FIELD_TO_ROLE["artist"]
BPM = FIELD_TO_ROLE["bpm"]
GENRES = FIELD_TO_ROLE["genres"]
TRACK_ID = FIELD_TO_ROLE["trackId"]
UID = FIELD_TO_ROLE["uid"]
IS_SELECTED = FIELD_TO_ROLE["isSelected"]


@pytest.fixture
def query(library):
    q = TrackQuery()
    # Constructed from Python with no event loop running, so the scheduled
    # first query has to be driven by hand. From QML, componentComplete() does
    # this once the declared properties have been assigned.
    q.refresh()
    yield q
    q.detach()


def read(q, row, role):
    return q.data(q.index(row), role)


def paint(q, first, last, role=TITLE):
    """Read a range the way a view would, then let the model catch up."""
    values = [read(q, r, role) for r in range(first, last + 1)]
    q.flush_now()
    return values


# ---------------------------------------------------------------------------
# Starting with little more than a count
# ---------------------------------------------------------------------------


def test_opening_a_list_costs_one_query_and_loads_no_rows(library, queries):
    queries.reset()
    q = TrackQuery()
    try:
        q.refresh()
        assert q.rowCount() == TRACK_COUNT
        assert q.count == TRACK_COUNT
        assert len(queries) == 1, "the id vector, and nothing else"
        assert len(library.row_cache) == 0, "no row data before anything is painted"
    finally:
        q.detach()


def test_data_never_touches_the_database(query, queries):
    queries.reset()
    values = [read(query, r, TITLE) for r in range(50)]

    assert values == [None] * 50, "first paint returns placeholders"
    assert len(queries) == 0, "data() runs during paint and must not do I/O"


def test_values_arrive_after_the_flush(query, queries):
    queries.reset()
    assert paint(query, 0, 19)[0] is None
    assert len(queries) == 1

    assert read(query, 0, TITLE) == "Track 00000"
    assert read(query, 19, TITLE) == "Track 00019"


# ---------------------------------------------------------------------------
# The viewport size never appears anywhere
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("visible_rows", [5, 20, 50])
def test_page_cost_is_the_same_whatever_the_viewport(library, queries, visible_rows):
    """5 visible rows or 50, a screenful costs the same one page fetch.

    This is the whole answer to "how does the backend know whether the GUI
    shows 50 rows or 5" — the number is not configured, and not detected.
    """
    q = TrackQuery()
    try:
        q.refresh()
        queries.reset()
        paint(q, 0, visible_rows - 1)
        assert len(queries) == 1
    finally:
        q.detach()


def test_a_page_is_fetched_once_however_many_rows_miss(query, queries):
    queries.reset()
    paint(query, 0, PAGE_SIZE - 1)
    assert len(queries) == 1, "128 misses coalesce into one fetch"


def test_scrolling_forward_prefetches_the_next_page(query):
    paint(query, 0, 10)
    # Page 0 was requested; page 1 should have come along with it.
    assert read(query, PAGE_SIZE + 5, TITLE) == f"Track {PAGE_SIZE + 5:05d}"


def test_a_row_another_list_filled_is_still_repainted(library, queries):
    """The miss earns the repaint, not the read.

    Every list shares one cache, so a row can be filled by a *different* list
    between missing here and this list's flush. ``fetch`` then has nothing to
    do and reports nothing updated — and a flush that only repaints what it
    fetched leaves the delegate showing the placeholder it was handed, with no
    signal to ever re-read. That is a row stuck on "Unknown title" until
    something resets the model.
    """
    listening = TrackQuery()
    filler = TrackQuery()
    try:
        listening.refresh()
        filler.refresh()

        changed: list[tuple[int, int]] = []
        listening.dataChanged.connect(
            lambda top, bottom, roles=None: changed.append((top.row(), bottom.row()))
        )

        assert read(listening, 5, TITLE) is None, "misses, and asks for the page"

        # Another list fills the shared cache first.
        read(filler, 5, TITLE)
        filler.flush_now()

        listening.flush_now()

        assert any(top <= 5 <= bottom for top, bottom in changed), (
            "row 5 was never told its data had arrived"
        )
        assert read(listening, 5, TITLE) == "Track 00005"
    finally:
        listening.detach()
        filler.detach()


# ---------------------------------------------------------------------------
# Only the fields the delegate actually binds
# ---------------------------------------------------------------------------


def test_only_demanded_tiers_are_read(query, queries):
    paint(query, 0, 10)
    assert read(query, 0, ARTIST) is None, "nothing bound artist yet"

    queries.reset()
    query.flush_now()
    assert len(queries) == 1
    assert read(query, 0, ARTIST) == "Artist 000"
    assert read(query, 0, GENRES) is None, "and genres still has not been asked for"


def test_a_second_scalar_field_is_free(query, queries):
    paint(query, 0, 10)
    queries.reset()

    assert read(query, 0, BPM) == 60
    assert len(queries) == 0, "scalars all arrived together"


def test_the_model_learns_which_fields_are_bound(query):
    assert query.demanded_keys == frozenset()

    paint(query, 0, 5)
    assert "title" in query.demanded_keys

    read(query, 0, ARTIST)
    assert "artist" in query.demanded_keys
    assert "isrc" not in query.demanded_keys, "never bound, never read"


def test_row_tier_fields_need_no_fetch(query, queries):
    queries.reset()
    assert read(query, 0, TRACK_ID) == query.track_ids()[0]
    assert read(query, 0, UID) == query.track_ids()[0]
    assert read(query, 0, IS_SELECTED) is False
    assert len(queries) == 0


# ---------------------------------------------------------------------------
# Re-sorting and re-filtering
# ---------------------------------------------------------------------------


def test_resorting_costs_one_query_and_reads_no_rows(query, queries):
    for row in range(0, TRACK_COUNT, PAGE_SIZE):
        paint(query, row, min(row + PAGE_SIZE - 1, TRACK_COUNT - 1))

    queries.reset()
    query.sortField = "title"
    query.sortAscending = False
    query.refresh()
    assert len(queries) == 1, "just the new id vector"

    queries.reset()
    assert read(query, 0, TITLE) == f"Track {TRACK_COUNT - 1:05d}"
    assert len(queries) == 0, "the cache is keyed by track id, so rows survive a re-sort"


def test_sort_by_a_joined_field(query):
    query.sortField = "artist"
    query.refresh()
    paint(query, 0, 5, ARTIST)
    assert read(query, 0, ARTIST) == "Artist 000"

    query.sortAscending = False
    query.refresh()
    paint(query, 0, 5, ARTIST)
    assert read(query, 0, ARTIST) == "Artist 039"


def test_descending_reverses_the_tiebreak_too(repo):
    """A coarse field leaves the tiebreak deciding what the user actually sees.

    ``stashedDate`` is a day, and a library is usually imported in a handful of
    sittings — every seeded track shares one value here, as a day's worth of
    imports does in a real one. So this is not a corner case: it is what the
    whole list looks like, and a descending sort that walks the day forwards
    reads as broken.
    """
    newest_first = repo.query_ids(None, "stashedDate", ascending=False)
    oldest_first = repo.query_ids(None, "stashedDate", ascending=True)

    assert newest_first == sorted(newest_first, reverse=True)
    assert newest_first == list(reversed(oldest_first))


def test_sort_is_case_insensitive_for_text(db, library):
    from app.models.track import Track

    session = db.session
    session.add(Track(title="aaa lowercase", duration_seconds=1))
    session.add(Track(title="AAB uppercase", duration_seconds=1))
    session.commit()

    q = TrackQuery()
    try:
        q.sortField = "title"
        q.refresh()
        paint(q, 0, 2)
        assert read(q, 0, TITLE) == "aaa lowercase"
        assert read(q, 1, TITLE) == "AAB uppercase"
    finally:
        q.detach()


def test_filters_narrow_the_id_vector(query):
    query.filters = [{"field": "album", "operator": "Equals", "value": "Album 3"}]
    query.refresh()
    assert query.count == TRACK_COUNT // 60

    query.filters = []
    query.refresh()
    assert query.count == TRACK_COUNT


def test_relationship_filter(query):
    query.filters = [{"field": "artist", "operator": "Equals", "value": "Artist 000"}]
    query.refresh()
    assert query.count == TRACK_COUNT // 40


def test_two_filters_on_the_same_relationship_do_not_double_join(query):
    query.filters = [
        {"field": "artist", "operator": "Contains", "value": "Artist"},
        {"field": "artist", "operator": "Contains", "value": "00"},
    ]
    query.refresh()
    assert query.count > 0


def test_malformed_filters_are_ignored(query):
    query.filters = [{"field": "nonsense", "operator": "??", "value": "x"}]
    query.refresh()
    assert query.count == TRACK_COUNT


# ---------------------------------------------------------------------------
# Staying correct
# ---------------------------------------------------------------------------


def test_a_missing_row_is_not_requested_forever(query, queries):
    """A stale id must stop being asked for, or every paint reschedules a fetch."""
    query._ids.append(999_999)
    stale_row = query.rowCount() - 1

    assert read(query, stale_row, TITLE) is None
    query.flush_now()

    queries.reset()
    assert read(query, stale_row, TITLE) is None
    assert len(queries) == 0
    assert not query._flush_timer.isActive()


def test_track_updated_repaints_the_row(query, db, event_bus):
    paint(query, 0, 5)
    assert read(query, 0, TITLE) == "Track 00000"

    from app.models.track import Track

    track = db.session.get(Track, query.track_ids()[0])
    track.title = "Renamed"
    db.session.commit()

    event_bus.emit("track.updated", track)
    assert read(query, 0, TITLE) is None, "invalidated, so it is asked for again"
    query.flush_now()
    assert read(query, 0, TITLE) == "Renamed"


def test_track_deleted_restates_the_id_vector(query, db, event_bus):
    victim = query.track_ids()[0]
    from app.models.track import Track

    db.session.delete(db.session.get(Track, victim))
    db.session.commit()

    event_bus.emit("track.deleted", victim)
    query.refresh()
    assert query.count == TRACK_COUNT - 1
    assert victim not in query.track_ids()


def test_out_of_range_rows_are_safe(query):
    assert read(query, -1, TITLE) is None
    assert read(query, TRACK_COUNT + 10, TITLE) is None
    assert query.uidAt(-1) is None
    assert query.trackIdAt(TRACK_COUNT + 10) is None


def test_unknown_role_returns_none(query):
    assert query.data(query.index(0), 999_999) is None


# ---------------------------------------------------------------------------
# Handing the whole list somewhere
# ---------------------------------------------------------------------------


def test_track_ids_come_back_in_display_order(query, queries):
    query.sortField = "title"
    query.refresh()
    queries.reset()

    ids = query.trackIds()

    assert ids == query.track_ids(), "display order, not id order"
    assert len(ids) == TRACK_COUNT
    assert len(queries) == 0, "the id vector is already in hand"


def test_track_ids_skips_rows_that_are_not_library_tracks(query):
    """Web results and unstashed import candidates carry an id of 0."""
    real = query.track_ids()[:3]
    query.set_ids([real[0], 0, real[1], 0, real[2]])

    assert query.trackIds() == real


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_selection_round_trip(query):
    ids = query.track_ids()
    query.setSelectedMultiple([ids[2], ids[5]])

    assert query.selectedUids() == [ids[2], ids[5]]
    assert query.selectedTrackIds() == [ids[2], ids[5]]
    assert read(query, 2, IS_SELECTED) is True
    assert read(query, 3, IS_SELECTED) is False

    query.setSelectedExclusive(ids[7])
    assert query.selectedTrackIds() == [ids[7]]

    query.clearSelection()
    assert query.selectedTrackIds() == []


def test_selection_survives_only_for_rows_still_present(query):
    ids = query.track_ids()
    query.setSelectedMultiple([ids[0], ids[1]])

    query.filters = [{"field": "album", "operator": "Equals", "value": "Album 3"}]
    query.refresh()
    assert query.selectedTrackIds() == [], "selected rows are no longer in the list"


# ---------------------------------------------------------------------------
# The catalog itself
# ---------------------------------------------------------------------------


def test_catalog_carries_no_presentation():
    """No labels, colours, widths, icons or renderers may leak back in."""
    for field in catalog():
        assert set(field) == {"key", "type", "sortable", "filterable"}


def test_every_role_name_is_a_catalog_key(query):
    names = {name.decode() for name in query.roleNames().values()}
    assert names == {f["key"] for f in catalog()}


def test_visible_range_is_only_a_hint(query, queries):
    """setVisibleRange must be optional — data() already drives loading."""
    queries.reset()
    query.setVisibleRange(0, 40)
    query.flush_now()
    assert len(queries) == 0, "nothing has been bound yet, so there is nothing to warm"

    paint(query, 0, 5)
    queries.reset()
    query.setVisibleRange(600, 640)
    query.flush_now()
    assert len(queries) == 1, "now it warms the pages for the fields in use"
    assert read(query, 600, TITLE) == "Track 00600"


# ---------------------------------------------------------------------------
# Demand goes stale
# ---------------------------------------------------------------------------
#
# A miss is a demand, but only for as long as the viewport it came from is
# still where the user is looking. Dragging a scrollbar registers one for every
# page it crosses, and by the time the flush runs the pointer is somewhere else
# entirely — so without this the model reads thousands of rows nobody will see,
# on the UI thread, sixty times a second for the length of the gesture.


def sweep(q, positions, span=40):
    """Report the viewport at each of *positions*, as a drag would."""
    for first in positions:
        q.setVisibleRange(first, first + span - 1)


def test_a_sweep_reads_only_where_it_landed(query, queries):
    paint(query, 0, 5)          # arm the demanded keys
    queries.reset()

    sweep(query, [0, 200, 400, 600, 800, 1000])
    query.flush_now()

    assert len(queries) == 1, "one read, for where the drag ended"
    assert read(query, 1000, TITLE) == "Track 01000"
    assert read(query, 400, TITLE) is None, "the pages swept past were not read"


def test_a_sweep_waits_for_the_viewport_to_settle(query):
    """Distance decides, and the interval is the only observable.

    Headless, so the timer never actually fires — which is the point: what is
    being pinned is that a sweep arms a long wait and ordinary scrolling does
    not, not what happens when either expires.
    """
    paint(query, 0, 5)

    query.setVisibleRange(0, 39)
    query.setVisibleRange(600, 639)
    assert query._flush_timer.interval() == SETTLE_MS, "a jump defers the read"

    query.setVisibleRange(610, 649)
    assert query._flush_timer.interval() == FLUSH_MS, (
        "a step of less than a viewport is reading, and reading must not be "
        "made to wait"
    )


def test_reading_is_never_deferred(query, queries):
    """Wheel scrolling moves a few rows at a time and must be unaffected."""
    paint(query, 0, 5)
    queries.reset()

    for first in range(0, 200, 10):
        query.setVisibleRange(first, first + 39)
        assert query._flush_timer.interval() == FLUSH_MS

    query.flush_now()
    assert read(query, 200, TITLE) == "Track 00200"


def test_a_sweep_does_not_pull_the_library_through_the_cache(library, query):
    """The stutter used to outlast the gesture, and this is why.

    A sweep flush inserts far more rows than the cache holds, so it evicts the
    whole of it — including the page the user is about to land on. Clamping is
    what makes the capacity mean anything.
    """
    paint(query, 0, 5)
    sweep(query, list(range(0, TRACK_COUNT - 40, 50)))
    query.flush_now()

    assert len(library.row_cache) < TRACK_COUNT, (
        "the sweep read rows it had already left behind"
    )


def test_a_replaced_id_vector_forgets_the_viewport(query, queries):
    """A re-sort or re-filter makes the recorded window meaningless.

    Left in place, the first paint after the reset is clamped against a window
    describing the *old* list, and the rows the GUI is about to ask for can fall
    outside it — a list that comes back permanently blank.
    """
    paint(query, 0, 5)
    query.setVisibleRange(1100, 1139)

    # Ids the opening paint did not warm, so the fetch below is a real one. The
    # row cache is keyed by track id and deliberately survives a re-sort, which
    # would otherwise answer this test out of memory and prove nothing.
    query.set_ids(list(range(501, 601)))
    queries.reset()

    assert paint(query, 0, 5) == [None] * 6, "first paint is still placeholders"
    assert len(queries) == 1, "the read was not clamped away by the old window"
    assert read(query, 0, TITLE) == "Track 00500"


def test_a_hint_for_the_current_viewport_is_never_stale(query, queries):
    """Clamping must not defeat the warm-ahead the hint exists for."""
    paint(query, 0, 5)
    queries.reset()

    query.setVisibleRange(600, 640)
    query.flush_now()

    assert read(query, 600, TITLE) == "Track 00600"
    assert read(query, 640, TITLE) == "Track 00640"


# ---------------------------------------------------------------------------
# dataChanged says only what could have changed
# ---------------------------------------------------------------------------


def test_a_page_read_names_only_the_roles_it_could_have_filled(query):
    """"All roles" re-fires uid, trackId and isSelected at every live delegate.

    None of those can move on a page read, and each one costs a GUI real work:
    a selection lookup, the playing-row test, and on the import list the whole
    of importState and everything hanging off it.
    """
    seen: list[list[int]] = []
    query.dataChanged.connect(lambda tl, br, roles: seen.append(list(roles)))

    paint(query, 0, 5)

    assert seen, "the fetch repainted nothing"
    for roles in seen:
        assert roles, "no roles at all means every role"
        assert set(roles) == set(_TIER_ROLES[Tier.SCALAR])
        for identity in (UID, TRACK_ID, IS_SELECTED):
            assert identity not in roles


def test_a_tier_names_every_field_it_fills_not_just_the_one_asked_for(query):
    """Tiers fill atomically, so a delegate bound to bpm must hear about title."""
    seen: list[list[int]] = []
    query.dataChanged.connect(lambda tl, br, roles: seen.append(list(roles)))

    paint(query, 0, 5, role=TITLE)

    assert any(BPM in roles for roles in seen), (
        "asking for title also landed bpm, and a row bound to it was not told"
    )


def test_an_edit_still_names_every_role(query):
    """An edit can move anything, including fields a subclass serves itself."""
    paint(query, 0, 5)

    seen: list[list[int]] = []
    query.dataChanged.connect(lambda tl, br, roles: seen.append(list(roles)))
    query.invalidate_track(query.trackIdAt(0))

    assert seen and all(roles == [] for roles in seen), (
        "an edit has no tier to derive roles from, so it must not guess"
    )
