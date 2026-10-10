"""Playing a row from partway down the queue, as the model reports it.

This used to arrive at a view as ``queue_reset``: true, and useless. Nothing is
created or destroyed except the track that was playing, and only when it was
listened to — the rest is the same rows in a different order, so a reset threw
away the one thing worth saying, which is that the track now at the top is the
one that was at row 7.

It is reported as a walk instead: the removal first, then the moves that bring
the chosen rows to the front. Every step has to be a valid model state, because
a view animates through them, and the walk has to land exactly where the manager
already is — nothing re-reads the manager on the way, so a walk that drifts
leaves the model quietly lying about its own contents. That last property is
what most of this file is about.
"""
from __future__ import annotations

import itertools

import pytest

from app.events.event_bus import EventBus
from app.queue.queue_manager import QueueManager
from app.viewmodels.queue_view_model import QueueViewModel


@pytest.fixture
def vm(qt_app, cache, library):
    """A queue view model holding tracks 0..5."""
    mgr = QueueManager()
    mgr.set_queue([{"id": i} for i in range(6)])
    return QueueViewModel(mgr, EventBus(), cache)


def model_ids(vm) -> list[int]:
    return [vm.trackIdAt(row) for row in range(vm.rowCount())]


def manager_ids(vm) -> list[int]:
    return [e["id"] for e in vm._mgr.queue_entries()]


def play(vm, rows, listened):
    """Do what PlayerViewModel._apply_mutation does, and hand back the mutation."""
    mutation = vm._mgr.play_at(rows, listened)
    if mutation.queue_moved is not None:
        vm.notify_played(mutation.queue_removed_index, mutation.queue_moved)
    elif mutation.queue_reset:
        vm.notify_reset()
    return mutation


# ------------------------------------------------------------- what it reports


def test_playing_a_row_is_reported_as_a_walk_not_a_reset(vm):
    mutation = vm._mgr.play_at([3], listened=True)

    assert not mutation.queue_reset
    assert mutation.queue_removed_index == 0, "the track that was playing"
    assert mutation.queue_moved == [(2, 0)], "row 3, less the row already removed"


def test_a_track_that_had_barely_started_is_not_removed(vm):
    """It drops in behind the new current track, so nothing leaves the queue."""
    mutation = vm._mgr.play_at([3], listened=False)

    assert mutation.queue_removed_index is None
    assert mutation.queue_moved == [(3, 0)], "no removal above it to adjust for"


def test_playing_the_row_already_at_the_front_reports_nothing(vm):
    mutation = vm._mgr.play_at([0], listened=True)

    assert mutation.queue_moved is None
    assert mutation.queue_removed_index is None
    assert not mutation.queue_reset


def test_playing_a_block_already_at_the_front_reports_nothing(vm):
    """Nothing moved and nothing left, so there is nothing for a view to do."""
    mutation = vm._mgr.play_at([0, 1], listened=True)

    assert mutation.queue_moved is None
    assert not mutation.queue_reset


# ----------------------------------------------------------- what the walk does


def test_the_walk_lands_where_the_manager_already_is(vm):
    play(vm, [3], listened=True)

    assert model_ids(vm) == manager_ids(vm) == [3, 1, 2, 4, 5]


def test_the_walk_carries_a_block_in_its_own_order(vm):
    play(vm, [4, 2], listened=True)

    assert model_ids(vm) == manager_ids(vm) == [2, 4, 1, 3, 5]


def test_the_walk_never_drifts_from_the_manager(qt_app, cache, library):
    """The property the whole design rests on, over every selection of six rows.

    ``notify_played`` maintains the vectors by hand, a step at a time, because
    ``_pull`` would read the end of the walk into the middle of it. Nothing
    reconciles afterwards, so if the arithmetic is wrong anywhere the model
    simply disagrees with the manager and says nothing about it.

    One test rather than a parametrised hundred: it is one property, and
    standing a fresh library up for each case costs more than the whole rest of
    this file.
    """
    drifted = []
    for count in (1, 2, 3):
        for rows in itertools.combinations(range(6), count):
            for listened in (True, False):
                mgr = QueueManager()
                mgr.set_queue([{"id": i} for i in range(6)])
                model = QueueViewModel(mgr, EventBus(), cache)

                play(model, list(rows), listened)

                if model_ids(model) != manager_ids(model):
                    drifted.append(
                        f"rows={list(rows)} listened={listened}: "
                        f"model {model_ids(model)} vs manager {manager_ids(model)}")

    assert drifted == []


# ------------------------------------------------------- what it says on the way


def test_the_outgoing_track_is_named_as_leaving_under_its_own_steam(vm):
    """Same signal as an advance, because it is the same kind of departure.

    The track was playing and is now in history; nobody threw it out. A view
    that fades one and flies the other needs to be told which this is.
    """
    named = []
    vm.advance_removed.connect(named.append)
    leaving = vm.uidAt(0)

    play(vm, [3], listened=True)

    assert named == [leaving]


def test_nothing_is_named_when_nothing_leaves(vm):
    named = []
    vm.advance_removed.connect(named.append)

    play(vm, [3], listened=False)

    assert named == [], "it only moved down a slot"


def test_the_chosen_row_is_announced_as_moved_not_rebuilt(vm):
    moves, resets = [], []
    vm.rowsMoved.connect(lambda *a: moves.append(a))
    vm.modelReset.connect(lambda: resets.append(True))

    play(vm, [3], listened=True)

    assert resets == [], "a reset would cost the view every delegate"
    assert len(moves) == 1


def test_a_row_keeps_its_uid_across_the_walk(vm):
    """Which is what lets a view follow it rather than redraw it."""
    chosen = vm.uidAt(3)
    kept = vm.uidAt(5)

    play(vm, [3], listened=True)

    assert vm.uidAt(0) == chosen, "it travelled, it was not replaced"
    assert vm.uidAt(vm.rowCount() - 1) == kept
