"""How the queue reports rows leaving it.

Removal used to be one thing. Now the model distinguishes two, because the GUI
draws them differently: a row the user took out is thrown off the right-hand
edge of the list, and a row that simply finished playing is not — it went
nowhere anyone sent it.

The distinction has to be made here rather than in the GUI, because by the time
Qt's row signals arrive there is nothing left in them to tell the two apart.

The other half is ``clearAll``. Emptying the queue is one act and has to reach
the GUI as one, or the list animates ten departures for a single press of Clear.

``removeAtRows`` is the third: a set of rows named by the caller rather than
read off the selection, which is what a row pulled off the list by its grip
needs — that gesture carries the selection only when it grabbed part of one.
"""
from __future__ import annotations

import pytest

from app.events.event_bus import EventBus
from app.queue.queue_manager import QueueManager
from app.viewmodels.queue_view_model import QueueViewModel


@pytest.fixture
def vm(qt_app, cache, library):
    """A queue view model holding tracks 0..4."""
    mgr = QueueManager()
    mgr.set_queue([{"id": i} for i in range(5)])
    return QueueViewModel(mgr, EventBus(), cache)


# ------------------------------------------------------------------- clearAll


def test_clear_all_empties_the_queue(vm):
    vm.clearAll()

    assert vm.rowCount() == 0
    assert vm._mgr.queue_entries() == []


def test_clear_all_drops_the_selection_with_it(vm):
    vm.setSelectedMultiple([vm.uidAt(r) for r in range(vm.rowCount())])
    assert vm.selectedRows(), "precondition: something is selected"

    vm.clearAll()

    assert vm.selectedRows() == [], "a selection of nothing is not a selection"


def test_clear_all_arrives_as_one_reset_not_five_removals(vm):
    """The whole point of the slot.

    A walk down ``removeAt`` would be five ``rowsRemoved`` signals, and the list
    would animate five separate departures for one press of Clear.
    """
    resets = []
    removals = []
    vm.modelReset.connect(lambda: resets.append(True))
    vm.rowsRemoved.connect(lambda *a: removals.append(a))

    vm.clearAll()

    assert len(resets) == 1
    assert removals == [], "no per-row signal survived"


def test_clear_all_on_an_empty_queue_says_nothing(vm):
    vm.clearAll()
    resets = []
    vm.modelReset.connect(lambda: resets.append(True))

    vm.clearAll()

    assert resets == [], "nothing left to clear, so nothing to announce"


# ------------------------------------------------------- advance vs. removal


def test_advancing_names_the_row_that_is_leaving(vm):
    """The uid, not the row.

    A row is named before it goes, so by the time the GUI acts on it every index
    has already shifted underneath — which is exactly the case a row number
    cannot survive.
    """
    named = []
    vm.advance_removed.connect(named.append)
    leaving = vm.uidAt(0)

    vm.notify_removed(0)

    assert named == [leaving]


def test_advancing_names_the_row_while_it_still_exists(vm):
    """Emitted *before* the removal, or there would be no uid left to read."""
    seen = []
    vm.advance_removed.connect(lambda uid: seen.append(vm.uidAt(0)))

    vm.notify_removed(0)

    assert seen == [seen[0]] and seen[0] is not None, "the row was still there"


def test_removing_by_hand_is_not_an_advance(vm):
    """The distinction that makes the signal worth having."""
    named = []
    vm.advance_removed.connect(named.append)

    vm.removeAt(0)

    assert named == [], "the user did this, not playback"


def test_removing_a_selection_is_not_an_advance(vm):
    named = []
    vm.advance_removed.connect(named.append)
    vm.setSelectedMultiple([vm.uidAt(r) for r in range(vm.rowCount())])

    vm.removeSelected()

    assert named == []
    assert vm.rowCount() == 0


# ----------------------------------------------------------------- removeAtRows
#
# The slot behind pulling a queue row off the right-hand edge by its grip. It
# takes rows rather than reading the selection, because that gesture does not
# always agree with it: a grip on an unselected row carries only that row.


def test_remove_at_rows_takes_out_what_it_is_given(vm):
    surviving = [vm.uidAt(r) for r in (0, 2, 4)]

    vm.removeAtRows([1, 3])

    assert [vm.uidAt(r) for r in range(vm.rowCount())] == surviving


def test_a_contiguous_run_is_one_signal(vm):
    """The reason the slot groups at all.

    Three adjacent rows leaving in one pair is one departure the list can
    animate; three pairs is three, staggered, for a single gesture.
    """
    removals = []
    vm.rowsRemoved.connect(lambda *a: removals.append(a))

    vm.removeAtRows([1, 2, 3])

    assert len(removals) == 1
    _, first, last = removals[0]
    assert (first, last) == (1, 3)


def test_a_split_selection_is_one_signal_per_run(vm):
    """Descending, so each run's indices are still valid when its turn comes."""
    removals = []
    vm.rowsRemoved.connect(lambda *a: removals.append(a))

    vm.removeAtRows([0, 1, 3])

    assert [(f, l) for _, f, l in removals] == [(3, 3), (0, 1)]


def test_rows_arrive_from_qml_so_the_slot_does_not_trust_them(vm):
    """Unsorted, duplicated and out of range, all in one call."""
    vm.removeAtRows([3, 1, 1, 99, -1])

    assert vm.rowCount() == 3, "row 1 went once and the nonsense went nowhere"


def test_remove_at_rows_on_nothing_says_nothing(vm):
    removals = []
    vm.rowsRemoved.connect(lambda *a: removals.append(a))

    vm.removeAtRows([])

    assert removals == []
    assert vm.rowCount() == 5


def test_remove_at_rows_ignores_the_selection(vm):
    """A grip on an unselected row carries that row and leaves the rest alone.

    ``removeSelected`` cannot serve the gesture for exactly this reason, and a
    slot that quietly fell back to the selection would take out four rows on a
    pull that grabbed one.
    """
    picked = [vm.uidAt(r) for r in (0, 1, 2)]
    vm.setSelectedMultiple(picked)

    vm.removeAtRows([4])

    assert vm.rowCount() == 4
    assert vm.selectedRows() == [0, 1, 2], "the selection is where it was"


def test_a_removed_row_takes_its_selection_with_it(vm):
    vm.setSelectedMultiple([vm.uidAt(r) for r in (1, 3)])

    vm.removeAtRows([1])

    assert vm.selectedRows() == [2], "row 3 of five is row 2 of four"


def test_pulling_a_row_off_by_hand_is_not_an_advance(vm):
    """It is a removal like any other the user made — see the exit transition."""
    named = []
    vm.advance_removed.connect(named.append)

    vm.removeAtRows([2])

    assert named == []


def test_remove_selected_still_goes_through_the_same_walk(vm):
    """One implementation, so the menu and the gesture cannot drift apart."""
    vm.setSelectedMultiple([vm.uidAt(r) for r in (1, 2, 3)])
    removals = []
    vm.rowsRemoved.connect(lambda *a: removals.append(a))

    vm.removeSelected()

    assert len(removals) == 1, "still grouped"
    assert vm.rowCount() == 2
