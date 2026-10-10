"""The queue and history rules, tested against QueueManager directly.

No Qt, no database — the policy is plain list manipulation, and it is the part
that must not regress. Every test here is a sentence from the module docstring
of ``app.queue.queue_manager`` made executable.
"""
from __future__ import annotations

import pytest

from app.queue.queue_manager import QueueManager


def ids(entries) -> list[int]:
    return [e["id"] for e in entries]


@pytest.fixture
def manager() -> QueueManager:
    """A queue holding tracks 0..9, nothing in history."""
    mgr = QueueManager()
    mgr.set_queue([{"id": i} for i in range(10)])
    return mgr


# ----------------------------------------------------------------- resilience


def test_playing_from_the_bottom_leaves_everything_in_between(manager):
    """The headline rule: jumping down the queue skips nothing into history."""
    manager.play_at([8], listened=True)

    assert ids(manager.queue_entries()) == [8, 1, 2, 3, 4, 5, 6, 7, 9]
    assert ids(manager.history_entries()) == [0], "only the track that was playing"


def test_an_unlistened_track_drops_to_play_next_instead_of_being_dropped(manager):
    """A track that has barely started must not vanish, and must not be recorded."""
    manager.play_at([8], listened=False)

    assert ids(manager.queue_entries()) == [8, 0, 1, 2, 3, 4, 5, 6, 7, 9]
    assert manager.history_entries() == [], "too young to count as played"
    assert len(manager) == 10, "nothing left the queue"


def test_playing_several_rows_moves_them_to_the_front_in_display_order(manager):
    """Selecting rows 4 and 8 puts 4 in slot 0 and 8 in slot 1."""
    manager.play_at([4, 8], listened=True)

    assert ids(manager.queue_entries()) == [4, 8, 1, 2, 3, 5, 6, 7, 9]
    assert ids(manager.history_entries()) == [0]


def test_playing_several_rows_ignores_the_order_they_were_clicked(manager):
    """Selection is a set; display order is what the user sees and means."""
    manager.play_at([8, 4], listened=True)

    assert ids(manager.queue_entries())[:2] == [4, 8]


def test_playing_the_current_row_is_a_no_op(manager):
    assert manager.play_at([0], listened=True) == manager.play_at([], listened=True)
    assert ids(manager.queue_entries()) == list(range(10))
    assert manager.history_entries() == []


def test_out_of_range_rows_are_ignored(manager):
    manager.play_at([3, 99, -1], listened=False)
    assert ids(manager.queue_entries())[:2] == [3, 0]


# ------------------------------------------------------------------- advance


def test_next_records_the_play_however_short_it_was(manager):
    """The one exception to the threshold: Next means 'done with this one'."""
    manager.advance()

    assert ids(manager.queue_entries()) == list(range(1, 10))
    assert ids(manager.history_entries()) == [0]


def test_advance_on_an_empty_queue_changes_nothing():
    mgr = QueueManager()
    mutation = mgr.advance()

    assert mgr.queue_entries() == []
    assert mgr.history_entries() == []
    assert mutation.queue_removed_index is None


def test_history_does_not_record_the_same_track_twice_in_a_row():
    mgr = QueueManager()
    mgr.set_queue([{"id": 7}, {"id": 7}])

    assert mgr.advance().history_inserted is True
    assert mgr.advance().history_inserted is False, "duplicate of the top is discarded"
    assert ids(mgr.history_entries()) == [7]


# ------------------------------------------------------------------ previous


def test_previous_pulls_the_top_of_history_back_into_the_queue(manager):
    manager.advance()  # 0 -> history
    manager.previous()

    assert ids(manager.queue_entries()) == list(range(10)), "exactly as it was"
    assert manager.history_entries() == [], "the play was undone"


def test_next_and_previous_round_trip_to_exactly_where_they_started(manager):
    manager.advance()   # 0 -> history, 1 now playing
    manager.previous()  # 0 comes back, 1 shifts down to queue[1]

    assert ids(manager.queue_entries()) == list(range(10))
    assert manager.history_entries() == [], "the rewind recorded nothing"


def test_repeated_previous_stacks_every_track_it_passes_at_the_front(manager):
    """Walking back never loses anything, so Next walks straight forward again."""
    for _ in range(3):
        manager.advance()
    assert ids(manager.queue_entries()) == [3, 4, 5, 6, 7, 8, 9]
    assert ids(manager.history_entries()) == [2, 1, 0]

    for _ in range(3):
        manager.previous()

    assert ids(manager.queue_entries()) == list(range(10))
    assert manager.history_entries() == []


def test_previous_never_queues_the_same_track_twice(manager):
    """The reason a rewind records nothing.

    Were the outgoing track prepended to history as well as re-seated at
    queue[1], the very next Previous would pull that same track back out and
    the queue would hold it in two places.
    """
    for _ in range(3):
        manager.advance()

    for _ in range(3):
        manager.previous()
        queued = ids(manager.queue_entries())
        assert len(queued) == len(set(queued)), f"a track was queued twice: {queued}"


def test_previous_with_empty_history_changes_nothing(manager):
    manager.previous()

    assert ids(manager.queue_entries()) == list(range(10))
    assert manager.history_entries() == []


def test_previous_into_an_empty_queue_still_works():
    mgr = QueueManager()
    mgr.set_queue([{"id": 4}])
    mgr.advance()
    assert mgr.queue_entries() == []

    mutation = mgr.previous()

    assert ids(mgr.queue_entries()) == [4]
    assert mutation.queue_inserted_index == 0
    assert mutation.history_removed_index == 0


def test_previous_is_described_as_one_insert(manager):
    """The outgoing track never really left, so the queue only gained a row.

    It is popped and re-seated at queue[1], which is where it already was
    relative to everything else — same uid, same order below it. Describing that
    as a reset would be true but useless: a view told everything changed cannot
    show what arrived.
    """
    manager.advance()  # 0 -> history, 1 now playing
    before = manager.queue_entries()[0]["uid"]

    mutation = manager.previous()

    assert ids(manager.queue_entries()) == list(range(10))
    assert mutation.queue_inserted_index == 0
    assert mutation.history_removed_index == 0
    assert not mutation.queue_reset
    assert not mutation.history_reset
    assert manager.queue_entries()[1]["uid"] == before, "track 1 is the same row"


def test_previous_keeps_a_fully_listened_track_at_the_front_of_the_queue(manager):
    """A rewind is not "done with this one".

    However long the outgoing track played for, it is the very next thing
    ahead of the listener, so it stays in the queue rather than going to
    history. Playing it through later is what records it.
    """
    manager.advance()  # 0 -> history, 1 now playing
    playing = manager.queue_entries()[0]["uid"]

    manager.previous()

    assert ids(manager.queue_entries()) == list(range(10))
    assert manager.queue_entries()[1]["uid"] == playing
    assert manager.history_entries() == [], "nothing was recorded by the rewind"


# --------------------------------------------------------------- play_entries


def test_playing_a_history_row_leaves_it_in_history(manager):
    """A history click is a new play, not a rewind — the record stays."""
    manager.advance()
    manager.advance()
    assert ids(manager.history_entries()) == [1, 0]

    manager.play_entries([{"id": 0}], listened=True)

    assert ids(manager.queue_entries())[0] == 0
    assert ids(manager.history_entries()) == [2, 1, 0], "the old row is untouched"


def test_playing_from_outside_keeps_an_unlistened_current_track(manager):
    manager.play_entries([{"id": 42}], listened=False)

    assert ids(manager.queue_entries()) == [42, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert manager.history_entries() == []


def test_playing_several_from_outside_preserves_their_order(manager):
    manager.play_entries([{"id": 40}, {"id": 41}], listened=True)

    assert ids(manager.queue_entries())[:2] == [40, 41]
    assert ids(manager.history_entries()) == [0]


def test_play_entries_on_an_empty_queue_is_a_plain_insert():
    mgr = QueueManager()
    mutation = mgr.play_entries([{"id": 5}], listened=False)

    assert ids(mgr.queue_entries()) == [5]
    assert mutation.queue_inserted_index == 0
    assert mutation.queue_reset is False


# ------------------------------------------------------------------ insertion


def test_insert_tracks_appends_by_default(manager):
    assert manager.insert_tracks([{"id": 20}, {"id": 21}]) == (10, 11)
    assert ids(manager.queue_entries())[-2:] == [20, 21]


def test_play_next_puts_the_block_in_slots_one_and_two(manager):
    """The multi-select bug, at the level it is actually fixed."""
    assert manager.insert_tracks([{"id": 4}, {"id": 8}], index=1) == (1, 2)
    assert ids(manager.queue_entries())[:3] == [0, 4, 8]


def test_inserting_nothing_reports_nothing(manager):
    assert manager.insert_tracks([], index=1) is None
    assert len(manager) == 10


def test_insert_beyond_the_end_lands_at_the_end():
    mgr = QueueManager()
    assert mgr.insert_tracks([{"id": 1}], index=5) == (0, 0)


# ------------------------------------------------------------------- identity


def test_every_row_gets_its_own_uid(manager):
    manager.insert_tracks([{"id": 0}], index=1)
    uids = [e["uid"] for e in manager.queue_entries()]

    assert len(set(uids)) == len(uids), "two copies of a track stay distinguishable"


def test_a_track_moving_to_history_gets_a_fresh_uid(manager):
    playing_uid = manager.current()["uid"]
    manager.advance()

    assert manager.history_entries()[0]["uid"] != playing_uid


def test_selection_is_dropped_when_a_row_leaves_the_queue(manager):
    uid = manager.current()["uid"]
    manager.set_selected(uid, True)
    manager.advance()

    assert manager.is_selected(uid) is False
    assert manager.is_selected(uid, history=True) is False, "not carried across views"


def test_queue_and_history_selections_are_independent(manager):
    manager.set_selected(1, True)
    manager.set_selected(1, True, history=True)
    manager.clear_selection()

    assert manager.selected_ids(history=True) == frozenset({1})


# ------------------------------------------------------------------- capacity


def test_history_is_capped():
    mgr = QueueManager(max_history=3)
    mgr.set_queue([{"id": i} for i in range(6)])
    for _ in range(6):
        mgr.advance()

    assert ids(mgr.history_entries()) == [5, 4, 3], "newest kept, oldest dropped"


def test_lowering_the_cap_trims_immediately():
    mgr = QueueManager(max_history=10)
    mgr.set_history([{"id": i} for i in range(10)])

    mgr.max_history = 4

    assert ids(mgr.history_entries()) == [0, 1, 2, 3]


def test_set_history_respects_the_cap():
    mgr = QueueManager(max_history=2)
    mgr.set_history([{"id": i} for i in range(5)])

    assert ids(mgr.history_entries()) == [0, 1]


# ------------------------------------------------------------------- editing


def test_remove_at_drops_the_row_and_its_selection(manager):
    uid = manager.queue_entries()[3]["uid"]
    manager.set_selected(uid, True)

    assert manager.remove_at(3)["id"] == 3
    assert ids(manager.queue_entries()) == [0, 1, 2, 4, 5, 6, 7, 8, 9]
    assert manager.is_selected(uid) is False


def test_remove_at_out_of_range_is_a_no_op(manager):
    assert manager.remove_at(99) is None
    assert len(manager) == 10


def test_move_reorders_without_touching_history(manager):
    manager.move(0, 4)

    assert ids(manager.queue_entries()) == [1, 2, 3, 4, 0, 5, 6, 7, 8, 9]
    assert manager.history_entries() == []


def test_entries_are_copied_in_so_callers_cannot_mutate_the_queue():
    source = [{"id": 1}, {"id": 2}]
    mgr = QueueManager()
    mgr.set_queue(source)
    source[0]["id"] = 999

    assert ids(mgr.queue_entries()) == [1, 2]
