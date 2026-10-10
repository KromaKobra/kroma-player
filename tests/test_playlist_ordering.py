"""Rearranging and emptying a playlist, one row signal at a time.

A playlist is an order somebody chose, and now its row numbers are grab handles:
drag to rearrange, drag right to take a track out. Both of those are gestures the
GUI animates, and an animation is only possible if the model says *what* changed.

That is the whole subject of this file. The playlist model used to answer every
mutation with ``set_ids`` — a full reset, which reaches a view as "the list is now
something else": every delegate rebuilt, the scroll position gone, and no
departure or move to draw. Removing one of twelve rows looked exactly like
replacing all twelve. So the assertions here are mostly about which signal came
out, not just about the contents afterwards, and `modelReset` is the one that must
not.

The second subject is subtler and is why the mutating slots can be bracketed at
all. ``EventBus.emit`` is a plain function call, and ``PlaylistService`` emits per
track while removing them — so a service call made inside a ``beginRemoveRows``
re-enters the model through its own subscription and, unguarded, opens a
``beginResetModel`` in the middle of a removal that has not been closed. See
``_mutating``.
"""
from __future__ import annotations

import pytest

from app.models.playlist import PlaylistTrack
from app.models.track import Track
from app.repositories.playlist_repository import PlaylistRepository
from app.services.playlist_service import PlaylistService
from app.viewmodels.playlist_tracks_view_model import PlaylistTracksViewModel


@pytest.fixture
def playlist(qt_app, db, cache, event_bus, library):
    """A view model over a real playlist of eight tracks, in a known order."""
    repository = PlaylistRepository(db.session)
    service = PlaylistService(repository, event_bus)
    playlist_id = service.create_playlist("A list")

    track_ids = [t.id for t in db.session.query(Track).order_by(Track.id).limit(8)]
    service.add_tracks(playlist_id, track_ids)

    vm = PlaylistTracksViewModel(service, event_bus, cache)
    vm.load(playlist_id)
    assert vm.trackIds() == track_ids, "precondition: append order round-trips"
    return vm, service, repository, playlist_id, track_ids


def _positions(session, playlist_id):
    """The raw position column, in order — the thing `set_order` rewrites."""
    return [
        row.position
        for row in session.query(PlaylistTrack)
        .filter_by(playlist_id=playlist_id)
        .order_by(PlaylistTrack.position.asc())
    ]


class Signals:
    """Everything the model said, in the order it said it."""

    def __init__(self, vm):
        self.resets: list = []
        self.removed: list = []
        self.moved: list = []
        vm.modelReset.connect(lambda: self.resets.append(True))
        vm.rowsRemoved.connect(lambda _p, first, last: self.removed.append((first, last)))
        vm.rowsMoved.connect(
            lambda _p, first, last, _dp, dest: self.moved.append((first, last, dest))
        )


# ------------------------------------------------------------------ no resets


def test_removing_rows_is_not_a_reset(playlist):
    """The difference between a row leaving and the list being replaced.

    Nothing else in the suite would catch a regression to ``set_ids`` here: the
    contents afterwards are identical either way, and only the signal says
    whether the GUI can draw the departure.
    """
    vm, _service, _repo, _pid, ids = playlist
    said = Signals(vm)

    vm.removeAtRows([2])

    assert said.resets == [], "a removal arrived as a reset"
    assert said.removed == [(2, 2)]
    assert vm.trackIds() == ids[:2] + ids[3:]


def test_reordering_is_not_a_reset(playlist):
    vm, _service, _repo, _pid, ids = playlist
    said = Signals(vm)

    vm.moveRows([5], 1)

    assert said.resets == [], "a reorder arrived as a reset"
    assert said.moved, "nothing said the row had moved"
    assert vm.trackIds() == [ids[0], ids[5], ids[1], ids[2], ids[3], ids[4], ids[6], ids[7]]


def test_the_models_own_write_does_not_come_back_around(playlist):
    """The guard that lets the brackets exist.

    The service emits ``playlist.track_removed`` per track, synchronously, from
    inside the removal. Unguarded, this model answers its own echo with a full
    re-read — which here would be a reset opened inside an unclosed
    ``beginRemoveRows``.
    """
    vm, _service, _repo, _pid, ids = playlist
    said = Signals(vm)

    vm.removeAtRows([0, 1, 2])

    assert said.resets == [], "the echo re-read the playlist"
    assert said.removed == [(0, 2)], "one run, one signal"
    assert vm.trackIds() == ids[3:]


def test_another_writer_still_reaches_the_open_playlist(playlist):
    """The guard must not deafen the model to changes it did not make."""
    vm, service, _repo, pid, ids = playlist

    service.remove_tracks(pid, [ids[4]])

    assert vm.trackIds() == ids[:4] + ids[5:], "an outside removal was ignored"


# ------------------------------------------------------------------- grouping


def test_a_contiguous_run_is_one_signal(playlist):
    vm, _service, _repo, _pid, ids = playlist
    said = Signals(vm)

    vm.removeAtRows([2, 3, 4])

    assert said.removed == [(2, 4)]
    assert vm.trackIds() == ids[:2] + ids[5:]


def test_a_split_selection_is_one_signal_per_run(playlist):
    """Descending, so each run's indices are still valid when its turn comes."""
    vm, _service, _repo, _pid, ids = playlist
    said = Signals(vm)

    vm.removeAtRows([0, 1, 5])

    assert said.removed == [(5, 5), (0, 1)]
    assert vm.trackIds() == [ids[2], ids[3], ids[4], ids[6], ids[7]]


def test_rows_arrive_from_qml_so_the_slot_does_not_trust_them(playlist):
    """Unsorted, duplicated and out of range, all in one call."""
    vm, _service, _repo, _pid, ids = playlist

    vm.removeAtRows([3, 1, 1, 99, -1])

    assert vm.trackIds() == [ids[0], ids[2], ids[4], ids[5], ids[6], ids[7]]


def test_remove_at_rows_on_nothing_says_nothing(playlist):
    vm, _service, _repo, _pid, ids = playlist
    said = Signals(vm)

    vm.removeAtRows([])

    assert said.removed == [] and said.resets == []
    assert vm.trackIds() == ids


def test_remove_at_rows_ignores_the_selection(playlist):
    """A grip on an unselected row carries that row and leaves the rest alone."""
    vm, _service, _repo, _pid, ids = playlist
    vm.setSelectedMultiple([vm.uidAt(row) for row in (0, 1, 2)])

    vm.removeAtRows([7])

    assert vm.trackIds() == ids[:7]
    assert vm.selectedRows() == [0, 1, 2], "the selection is where it was"


# ---------------------------------------------------------------- persistence


def test_a_new_order_survives_reopening_the_playlist(playlist):
    vm, _service, _repo, _pid, ids = playlist

    vm.moveRows([0], 4)
    arranged = vm.trackIds()
    assert arranged != ids, "precondition: something moved"

    vm.reload()

    assert vm.trackIds() == arranged


def test_reordering_works_from_the_gaps_a_removal_leaves(playlist):
    """Positions are handed out as ``max+1`` and are never renumbered on removal.

    So a playlist that has been edited for a while is sparse — ``0, 2, 3, 6`` —
    and a reorder that assumed dense input would rearrange the wrong rows. The
    fix is that ``set_order`` rewrites the block rather than patching it, which
    this proves by reordering a playlist that has holes in it.
    """
    vm, _service, repository, pid, _ids = playlist

    vm.removeAtRows([1, 4])
    holed = _positions(repository.session, pid)
    assert holed != list(range(len(holed))), f"precondition: gaps, got {holed}"

    surviving = vm.trackIds()
    vm.moveRows([0], 3)
    arranged = vm.trackIds()

    assert sorted(arranged) == sorted(surviving), "no row appeared or vanished"
    assert arranged == [surviving[1], surviving[2], surviving[3], surviving[0],
                        surviving[4], surviving[5]]

    assert _positions(repository.session, pid) == list(range(len(surviving))), (
        "set_order patched the gaps instead of renumbering the block"
    )

    vm.reload()
    assert vm.trackIds() == arranged, "the new order did not survive"


def test_removals_persist(playlist):
    vm, _service, _repo, _pid, ids = playlist

    vm.removeAtRows([1, 2])
    vm.reload()

    assert vm.trackIds() == [ids[0]] + ids[3:]


# -------------------------------------------------------------- moveRows shape


def test_a_straddling_block_lands_contiguously(playlist):
    """`to` is indexed against the list with the moved rows already taken out."""
    vm, _service, _repo, _pid, ids = playlist

    vm.moveRows([1, 3, 6], 2)

    rest = [ids[0], ids[2], ids[4], ids[5], ids[7]]
    assert vm.trackIds() == rest[:2] + [ids[1], ids[3], ids[6]] + rest[2:]


def test_a_destination_past_the_end_is_clamped(playlist):
    vm, _service, _repo, _pid, ids = playlist

    vm.moveRows([0, 1], 99)

    assert vm.trackIds() == ids[2:] + [ids[0], ids[1]]


def test_moving_a_row_onto_itself_costs_nothing(playlist):
    """Qt refuses a move to a row's own position and returns False from
    ``beginMoveRows``, which would leave the ``endMoveRows`` after it unbalanced.
    """
    vm, _service, _repo, _pid, ids = playlist
    said = Signals(vm)

    vm.moveTrack(3, 3)
    vm.moveTrack(99, 0)
    vm.moveTrack(-1, 0)

    assert said.moved == [] and said.resets == []
    assert vm.trackIds() == ids


def test_the_grid_is_told_when_a_playlist_is_rearranged(playlist):
    """A playlist's cover is whatever sits at position 0."""
    vm, _service, _repo, pid, _ids = playlist
    heard: list = []
    vm._playlist_service.event_bus.subscribe(
        "playlist.reordered", lambda playlist_id: heard.append(playlist_id)
    )

    vm.moveRows([4], 0)

    assert heard == [pid]
