"""The lyrics editor's arithmetic: inserting, re-timing, and splitting a paste.

`app/lyrics/editing.py` is three pure functions with no I/O behind them, and
they exist as a module rather than as JavaScript in the editor for exactly this
reason — the rules are the only genuinely non-obvious part of the feature, and
they are cheap to be exhaustive about here.

The rule worth stating plainly, because two of the four cases look inconsistent
until you say it: **a line inserted at either end of a document is not
interpolated.** At the top it takes 00:00.00, because a line put above
everything else starts where the track does. At the bottom it takes one second
after the last, because there is nothing below to aim at and any other number
would be invented. Only a line landing *between* two timed lines is a midpoint.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.lyrics.editing import (  # noqa: E402
    insertion_times,
    sorted_index,
    split_pasted,
    times_for_insert,
)


# ==================================================================
# insertion_times — the four cases
# ==================================================================


def test_one_line_between_two_lands_halfway():
    assert insertion_times(10.0, 20.0, 1) == [15.0]


def test_several_lines_between_two_spread_evenly_across_the_gap():
    """A pasted block must not stack every line on the same midpoint.

    Three lines pasted into a ten-second gap are three lines of lyrics, and
    giving them all 00:15 would make the highlight jump straight past two of
    them.
    """
    assert insertion_times(10.0, 20.0, 3) == [12.5, 15.0, 17.5]


def test_a_line_at_the_top_starts_where_the_track_does():
    """Not the midpoint of nothing and `after` — 00:00.00 exactly.

    A line put above everything else has nothing before it to come after, and
    the only honest answer is the start of the track.
    """
    assert insertion_times(None, 20.0, 1) == [0.0]


def test_several_lines_at_the_top_start_at_zero_and_spread_up():
    times = insertion_times(None, 30.0, 3)

    assert times[0] == 0.0
    assert times == pytest.approx([0.0, 10.0, 20.0])


def test_a_line_at_the_end_lands_one_second_after_the_last():
    assert insertion_times(10.0, None, 1) == [11.0]


def test_several_lines_at_the_end_march_one_second_apart():
    assert insertion_times(10.0, None, 3) == [11.0, 12.0, 13.0]


def test_a_document_with_no_timings_gains_none():
    """Reordering or extending an unsynced document must not quietly sync it.

    A line with an invented timecode is worse than one with none: `is_synced()`
    would start answering yes, and the pane would switch to a timed view built
    on numbers nobody chose.
    """
    assert insertion_times(None, None, 2) == [None, None]


def test_neighbours_in_the_wrong_order_do_not_produce_a_descending_run():
    """Bad data — a document whose timestamps go backwards — gets the same
    answer as the end of the list rather than times that would then sort
    themselves somewhere nobody asked for."""
    assert insertion_times(20.0, 10.0, 2) == [21.0, 22.0]


def test_asking_for_nothing_returns_nothing():
    assert insertion_times(10.0, 20.0, 0) == []


# ==================================================================
# times_for_insert — finding the neighbours
# ==================================================================


def test_the_neighbours_are_the_nearest_lines_that_actually_have_a_time():
    """Not simply the adjacent ones.

    A part-timed document is ordinary — a paste leaves untimed rows behind —
    and a line dropped below one of them should still place itself against the
    last real timestamp above rather than deciding there is nothing up there.
    """
    times = [10.0, None, None, 20.0]

    assert times_for_insert(times, 2, 1) == [15.0]


def test_inserting_at_the_very_top_and_the_very_end():
    times = [10.0, 20.0]

    assert times_for_insert(times, 0, 1) == [0.0]
    assert times_for_insert(times, 2, 1) == [21.0]


def test_a_moved_line_is_not_its_own_neighbour():
    """Which is why the caller blanks its time first. With the blank in place
    the answer comes from the lines around it; without one it would come from
    where the line used to be."""
    moved_to_the_middle = [10.0, None, 20.0]

    assert times_for_insert(moved_to_the_middle, 1, 1) == [15.0]


# ==================================================================
# sorted_index
# ==================================================================


def test_a_line_already_in_order_does_not_move():
    """The caller's signal to leave the list alone."""
    assert sorted_index([1.0, 5.0, 9.0, 13.0], 3) == 3


def test_a_line_retyped_to_an_earlier_time_moves_up():
    assert sorted_index([1.0, 5.0, 9.0, 3.0], 3) == 1


def test_a_line_retyped_to_a_later_time_moves_down():
    assert sorted_index([20.0, 5.0, 9.0, 13.0], 0) == 3


def test_a_line_with_no_time_has_no_opinion_about_order():
    """An untimed row is an anchor, not a thing to be sorted. Moving it would
    reorder words the user never gave a position to."""
    assert sorted_index([1.0, 5.0, None, 13.0], 2) == 2


def test_untimed_lines_do_not_displace_the_one_being_sorted():
    assert sorted_index([1.0, None, 9.0, 3.0], 3) == 1


def test_a_time_equal_to_its_neighbour_stays_after_it():
    """Retyping a time a line already had must not make it hop over an
    identical neighbour — nothing changed, so nothing should move."""
    assert sorted_index([1.0, 5.0, 5.0], 2) == 2


def test_an_index_off_the_end_is_returned_untouched():
    assert sorted_index([1.0, 2.0], 7) == 7


# ==================================================================
# split_pasted
# ==================================================================


@pytest.mark.parametrize("text,expected", [
    ("a\nb\nc", ["a", "b", "c"]),
    ("a\r\nb\r\nc", ["a", "b", "c"]),          # copied on Windows
    ("a\rb\rc", ["a", "b", "c"]),              # copied off something very old
    ("one", ["one"]),
    ("", []),
    ("   ", []),
])
def test_a_block_breaks_on_every_newline_flavour(text, expected):
    assert split_pasted(text) == expected


def test_leading_and_trailing_blanks_are_dropped():
    """A selection that ran one line too far is not a request for an empty
    row at the end of the editor."""
    assert split_pasted("\n\nverse one\nverse two\n\n") == ["verse one", "verse two"]


def test_a_blank_line_between_verses_survives():
    """It is part of the lyric sheet, and dropping it would silently reformat
    what was handed over."""
    assert split_pasted("one\n\ntwo") == ["one", "", "two"]


def test_each_line_is_stripped():
    assert split_pasted("  one  \n\ttwo\t") == ["one", "two"]
