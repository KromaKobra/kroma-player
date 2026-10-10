"""The proportional lyric timeline, driven through the real QML.

`tests/test_lyrics_editor_qml.py` pins the draft's row operations — insert,
remove, move, re-sort. This pins what the timeline adds on top of them: a view
whose *geometry is the timing*, where a block is as tall as its line is long
and a boundary between two blocks is the start time of the one beneath it.

Only the parts that are arithmetic are pinned here. How a block looks is not a
thing a test can hold still, but where it is and how tall it is are, and so is
what a drag on a boundary is allowed to do — which is the one gesture in the
editor that can quietly corrupt a document if its clamp is wrong.

Two things about the timeline are load-bearing for these tests:

**The scale rule is a multiplication, not a layout pass.** `y = t * pxPerSec`
everywhere, so `blockY()`, `blockHeight()` and `clampBoundary()` answer without
a window having laid anything out — which is what makes them assertable.

**The track has no audio.** `playerVM.duration` is therefore `0`, and the
timeline's `extent` guard is the only reason there is anything to lay out at
all. That is not an artefact of the harness: a track whose file the backend has
not read reports exactly the same thing.

The harness itself is `tests/test_lyrics_editor_qml.py`'s — a real 1600x900
window over a throwaway vault with a track playing, imported rather than copied
the way `tests/test_delegate_reuse.py` imports its backend.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from test_lyrics_editor_qml import editor  # noqa: E402,F401

# 10s of intro, then three lines. The last one has no line after it, so it runs
# to the end of the timeline — which is the case the `extent` guard covers.
SYNCED = "[00:10.00]one\n[00:16.00]two\n[00:24.00]three"
PLAIN = "one\ntwo\nthree"

# `extent` with no duration to go on: the last timing plus five seconds.
EXTENT = 29.0
# The zoom the editor opens at.
PX = 25.0
# A zoom at which 29 seconds is taller than the viewport, so there is
# somewhere to scroll to — the follow tests have nothing to say otherwise.
TALL = 100.0


def _ask(obj, name, *args):
    """Invoke a QML function and read its answer back.

    `_call` in the editor's own suite throws the return value away, which is
    all it needs; everything the timeline is asked here is a question.
    """
    from PySide6.QtCore import Q_ARG, Q_RETURN_ARG, QMetaObject, Qt

    value = QMetaObject.invokeMethod(
        obj, name, Qt.ConnectionType.DirectConnection,
        Q_RETURN_ARG("QVariant"),
        *[Q_ARG("QVariant", arg) for arg in args],
    )
    # A QML function hands back a JS value, which arrives wrapped.
    if hasattr(value, "toVariant"):
        value = value.toVariant()
    return value


@pytest.fixture
def timeline(editor):
    """The timeline component itself, for the questions only it can answer."""
    from PySide6.QtCore import QObject

    obj = editor.view.findChild(QObject, "lyricsTimeline")
    assert obj is not None
    return obj


@pytest.fixture
def flick(editor):
    """Its Flickable — where `contentHeight` and the scroll position live."""
    from PySide6.QtCore import QObject

    obj = editor.view.findChild(QObject, "lyricsTimelineFlick")
    assert obj is not None
    return obj


def _seconds(editor):
    """The draft's numeric column.

    A `var` property holding a JS array comes back as a `QJSValue` when QML
    built the array itself — which a boundary drag does, because it replaces
    the array rather than writing into it.
    """
    value = editor.view.property("draftSeconds")
    if hasattr(value, "toVariant"):
        value = value.toVariant()
    return [float(v) for v in value]


def _play_to(editor, timeline, seconds):
    """Put the playhead at *seconds*, without the glide it would arrive with.

    The playhead interpolates between the position updates the backend sends,
    which is what makes it crawl rather than step; `snap()` is the same escape
    a seek takes, and it is the only way to have a definite `playheadY` in a
    test that is not driving a frame clock.
    """
    editor.lyric_vm._player_vm._position = seconds
    editor.lyric_vm._player_vm.position_changed.emit()
    _ask(timeline, "snap")
    editor.settle()


# ==================================================================
# Which editor a document gets
# ==================================================================


def test_a_synced_document_opens_in_the_timeline(editor):
    editor.open_with(SYNCED)

    assert editor.view.property("timelineDraft") is True


def test_a_document_with_no_timings_keeps_the_row_editor(editor):
    """There is nothing to lay out in proportion. A retime pass is what gives
    such a document its first set of timings, and the notice above the rows
    says so."""
    editor.open_with(PLAIN)

    assert editor.view.property("timelineDraft") is False


def test_the_editor_a_draft_opened_in_does_not_change_underneath_it(editor):
    """`timelineDraft` is decided once, in `beginEdit()`, rather than bound to
    `is_synced` — otherwise typing the first timecode into a plain document
    would swap the editor out from under the cursor mid-word."""
    editor.open_with(PLAIN)
    editor.type_time(0, "00:03.00")

    assert editor.view.property("timelineDraft") is False


def test_a_synced_draft_can_be_switched_to_the_rows_and_back(editor):
    """Two editors over one draft. The rows are still the faster way to retype
    a batch of words, and nothing about the document decides which of the two
    that job wants."""
    editor.open_with(SYNCED)

    editor.call("toggleEditorMode")
    assert editor.view.property("timelineDraft") is False

    editor.call("toggleEditorMode")
    assert editor.view.property("timelineDraft") is True


def test_switching_editors_leaves_the_draft_exactly_as_it_was(editor):
    editor.open_with(SYNCED)

    editor.call("toggleEditorMode")

    assert editor.rows == [
        ("00:10.00", "one"), ("00:16.00", "two"), ("00:24.00", "three"),
    ]
    assert _seconds(editor) == [10.0, 16.0, 24.0]


def test_a_draft_with_no_timings_has_no_editor_to_switch_to(editor):
    """There is nothing to lay out in proportion, so the button is not offered
    and the toggle refuses rather than opening an empty timeline."""
    editor.open_with(PLAIN)
    assert editor.view.property("canUseTimeline") is False

    editor.call("toggleEditorMode")

    assert editor.view.property("timelineDraft") is False


# ==================================================================
# The scale rule
# ==================================================================


def test_a_block_is_as_tall_as_its_line_is_long(editor, timeline):
    """The whole point: six seconds of song is six seconds of screen, so
    judging a timing is looking at it rather than reading a number."""
    editor.open_with(SYNCED)

    assert _ask(timeline, "blockY", 0) == 10.0 * PX
    assert _ask(timeline, "blockHeight", 0) == 6.0 * PX
    assert _ask(timeline, "blockY", 1) == 16.0 * PX
    assert _ask(timeline, "blockHeight", 1) == 8.0 * PX


def test_the_last_block_runs_to_the_end_of_the_timeline(editor, timeline):
    """It has no line beneath it to end against, so it ends where the timeline
    does — not at a height of nothing."""
    editor.open_with(SYNCED)

    assert _ask(timeline, "blockHeight", 2) == (EXTENT - 24.0) * PX


def test_a_track_with_no_duration_still_has_a_timeline(editor, timeline, flick):
    """`playerVM.duration` is 0 for a track whose audio the backend has not
    read. Without the guard the content would be zero-tall, every block would
    be zero-tall, and the editor would look broken rather than empty."""
    editor.open_with(SYNCED)

    assert timeline.property("extent") == EXTENT
    assert flick.property("contentHeight") == EXTENT * PX


def test_zooming_in_scales_everything_by_the_same_number(editor, timeline, flick):
    """There is one time-to-pixel mapping and it is a multiplication, so a
    zoom is one property and nothing else has to be told about it."""
    editor.open_with(SYNCED)

    _ask(timeline, "zoomIn")

    assert timeline.property("pxPerSec") == pytest.approx(PX * 1.25)
    assert _ask(timeline, "blockY", 1) == pytest.approx(16.0 * PX * 1.25)
    assert flick.property("contentHeight") == pytest.approx(EXTENT * PX * 1.25)


def test_the_zoom_stops_at_both_ends(editor, timeline):
    editor.open_with(SYNCED)

    for _ in range(20):
        _ask(timeline, "zoomOut")
    assert timeline.property("pxPerSec") == timeline.property("minPxPerSec")

    for _ in range(40):
        _ask(timeline, "zoomIn")
    assert timeline.property("pxPerSec") == timeline.property("maxPxPerSec")


def test_zooming_is_not_mistaken_for_the_user_scrolling(editor, timeline):
    """Every scroll this component makes itself has to be invisible to the
    follow detector, and a zoom makes two of them — its own anchoring write,
    and the follow that re-runs inside it when the playhead is re-placed at
    the new scale. A flag would have the inner one clear the guard on the
    outer, and following would switch itself off every time somebody zoomed."""
    editor.open_with(SYNCED)
    timeline.setProperty("pxPerSec", TALL)
    _play_to(editor, timeline, 12.0)
    assert timeline.property("follow") is True

    _ask(timeline, "zoomIn")
    editor.settle()

    assert timeline.property("follow") is True


def test_a_zoom_keeps_the_moment_under_the_pointer_under_the_pointer(
    editor, timeline, flick,
):
    """Otherwise zooming into a dense section is a way to lose your place."""
    editor.open_with(SYNCED)
    timeline.setProperty("pxPerSec", TALL)
    flick.setProperty("contentY", 300.0)
    editor.settle()

    # 200px down the viewport is (300 + 200) / 100 = 5s.
    _ask(timeline, "zoomAt", 1, 200.0)

    assert timeline.property("pxPerSec") == pytest.approx(TALL * 1.25)
    assert flick.property("contentY") == pytest.approx(5.0 * TALL * 1.25 - 200.0)


# ==================================================================
# Dragging a boundary
# ==================================================================


def test_a_boundary_retimes_only_the_block_beneath_it(editor):
    """A boundary *is* the start time of the block below it, so moving one
    grows one block, shrinks the other, and touches nothing else."""
    editor.open_with(SYNCED)

    editor.call("moveBoundary", 1, 13.5)

    assert editor.times == ["00:10.00", "00:13.50", "00:24.00"]
    assert _seconds(editor) == [10.0, 13.5, 24.0]


def test_a_boundary_drag_resizes_both_blocks_it_is_between(editor, timeline):
    editor.open_with(SYNCED)

    editor.call("moveBoundary", 1, 13.5)

    assert _ask(timeline, "blockHeight", 0) == pytest.approx(3.5 * PX)
    assert _ask(timeline, "blockHeight", 1) == pytest.approx(10.5 * PX)


def test_a_boundary_stops_short_of_both_its_neighbours(editor, timeline):
    """A drag never reorders. In a timeline the order *is* the time, and a line
    that has to move that far is a line to retype in the gutter, where the
    draft's own re-sort already handles it."""
    editor.open_with(SYNCED)

    assert _ask(timeline, "clampBoundary", 1, 2.0) == pytest.approx(10.05)
    assert _ask(timeline, "clampBoundary", 1, 100.0) == pytest.approx(23.95)


def test_the_first_boundary_stops_at_the_top_of_the_track(editor, timeline):
    editor.open_with(SYNCED)

    assert _ask(timeline, "clampBoundary", 0, -30.0) == pytest.approx(0.05)


def test_the_last_boundary_stops_short_of_the_end_of_the_timeline(editor, timeline):
    editor.open_with(SYNCED)

    assert _ask(timeline, "clampBoundary", 2, 1000.0) == pytest.approx(EXTENT - 0.05)


def test_a_clamped_drag_leaves_every_other_timecode_alone(editor, timeline):
    """Pulled hard past the line below it, and the line below it has not
    moved — the drag ran out of room instead of pushing anything along."""
    editor.open_with(SYNCED)

    editor.call("moveBoundary", 1, _ask(timeline, "clampBoundary", 1, 100.0))

    assert editor.times == ["00:10.00", "00:23.95", "00:24.00"]


# ==================================================================
# Ctrl+dragging a boundary — moving everything below it at once
# ==================================================================


def test_a_ctrl_drag_carries_every_line_below_it(editor):
    """The correction for a document that is simply late: the timings are
    right relative to each other and wrong relative to the track, so the fix
    is one number applied to all of them."""
    editor.open_with(SYNCED)

    editor.call("shiftBoundary", 0, 12.0)

    assert _seconds(editor) == [12.0, 18.0, 26.0]
    assert editor.times == ["00:12.00", "00:18.00", "00:26.00"]


def test_a_ctrl_drag_keeps_every_gap_the_size_it_was(editor):
    """Which is the whole difference from an ordinary drag: that one changes
    one gap and this one changes none of them."""
    editor.open_with(SYNCED)
    before = _seconds(editor)

    editor.call("shiftBoundary", 0, 3.0)

    after = _seconds(editor)
    gaps = [round(after[i + 1] - after[i], 6) for i in range(len(after) - 1)]
    assert gaps == [round(before[i + 1] - before[i], 6) for i in range(len(before) - 1)]


def test_a_ctrl_drag_from_the_middle_leaves_the_lines_above_it_alone(editor):
    """A song that only drifts from the second verse on is fixed by grabbing
    the boundary where it starts drifting."""
    editor.open_with(SYNCED)

    editor.call("shiftBoundary", 1, 14.0)

    assert _seconds(editor) == [10.0, 14.0, 22.0]


def test_a_ctrl_drag_still_cannot_cross_the_line_above_it(editor, timeline):
    """The line above is standing still, so it is a wall in exactly the way an
    ordinary drag's is."""
    editor.open_with(SYNCED)

    assert _ask(timeline, "clampShift", 1, 2.0) == pytest.approx(10.05)
    assert _ask(timeline, "clampShift", 0, -30.0) == pytest.approx(0.05)


def test_a_ctrl_drag_has_no_wall_beneath_it(editor, timeline):
    """Everything below is coming too, so there is nothing down there to run
    into — and dragging past the end of the track is how a document gets
    pushed out to where a longer master actually starts."""
    editor.open_with(SYNCED)

    assert _ask(timeline, "clampShift", 1, 500.0) == pytest.approx(500.0)


def test_a_ctrl_drag_asked_for_the_time_it_started_at_puts_everything_back(editor):
    """Which is what a cancelled gesture does. Stating the drag as an absolute
    time rather than as a delta is what makes the undo a repeat of the same
    call rather than a second code path."""
    editor.open_with(SYNCED)

    editor.call("shiftBoundary", 0, 18.0)
    editor.call("shiftBoundary", 0, 10.0)

    assert _seconds(editor) == [10.0, 16.0, 24.0]


def test_a_ctrl_drag_gives_no_line_a_timing_it_did_not_have(editor):
    """An untimed line has nothing to offset, and offsetting it anyway would
    quietly sync a document nobody asked to sync."""
    editor.open_with(SYNCED)
    editor.type_time(1, "")

    editor.call("shiftBoundary", 0, 12.0)

    assert _seconds(editor) == [12.0, -1.0, 26.0]


# ==================================================================
# The numeric column
# ==================================================================


def test_the_numeric_column_is_the_draft_read_as_numbers(editor):
    editor.open_with(SYNCED)

    assert _seconds(editor) == [10.0, 16.0, 24.0]


def test_it_tracks_an_insert(editor):
    editor.open_with(SYNCED)

    editor.call("insertLines", 1, [""])

    assert _seconds(editor) == [10.0, 13.0, 16.0, 24.0]


def test_it_tracks_a_remove(editor):
    editor.open_with(SYNCED)

    editor.call("removeLine", 1)

    assert _seconds(editor) == [10.0, 24.0]


def test_it_tracks_a_re_sort(editor):
    """A retyped timecode moves its row, and the layout has to follow it there
    rather than leaving a block behind."""
    editor.open_with(SYNCED)

    editor.type_time(2, "00:05.00")

    assert editor.words == ["three", "one", "two"]
    assert _seconds(editor) == [5.0, 10.0, 16.0]


def test_a_line_with_its_timecode_cleared_is_still_reachable(editor, timeline):
    """`-1` is "no time", and a blanked field is an ordinary thing to find in a
    draft somebody is halfway through. It has nowhere of its own to be, so it
    goes to the top where it can still be typed into."""
    editor.open_with(SYNCED)

    editor.type_time(1, "")

    # No time is no opinion about order, so the line stays where it was.
    assert _seconds(editor) == [10.0, -1.0, 24.0]
    assert _ask(timeline, "blockY", 1) == 0.0


# ==================================================================
# Following the playhead
# ==================================================================


def test_the_user_scrolling_stops_the_view_following(editor, timeline, flick):
    """A flick, the wheel and the scrollbar all reach `contentY` by different
    routes — SmoothWheel writes it directly, so `onMovementStarted` never fires
    for it. `contentY` itself is the one place they converge."""
    editor.open_with(SYNCED)
    timeline.setProperty("pxPerSec", TALL)
    assert timeline.property("follow") is True

    flick.setProperty("contentY", 420.0)
    editor.settle()

    assert timeline.property("follow") is False


def test_the_target_button_puts_the_playhead_back_in_view(editor, timeline, flick):
    editor.open_with(SYNCED)
    timeline.setProperty("pxPerSec", TALL)
    _play_to(editor, timeline, 20.0)
    flick.setProperty("contentY", 0.0)
    editor.settle()
    assert timeline.property("follow") is False

    timeline.setProperty("follow", True)
    editor.settle()

    top = flick.property("contentY")
    assert top <= 20.0 * TALL <= top + flick.property("height")


def test_a_seek_snaps_the_playhead_rather_than_gliding_to_it(editor, timeline):
    """Position updates arrive a few times a second and the playhead
    interpolates between them, which is what makes it crawl instead of step. A
    seek is a jump and has to read as one."""
    editor.open_with(SYNCED)
    editor.lyric_vm._player_vm._position = 20.0
    editor.lyric_vm._player_vm.position_changed.emit()

    _ask(timeline, "snap")

    assert timeline.property("playheadY") == pytest.approx(20.0 * PX)
    assert timeline.property("follow") is True


# ==================================================================
# Nothing above emits a QML warning
# ==================================================================


def test_the_timeline_runs_without_a_single_qml_warning(editor, timeline):
    """The same assertion the row editor's suite makes, over the operations
    this file adds. `duration` is 0 throughout, so an unguarded division would
    be caught here."""
    editor.open_with(SYNCED)
    editor.call("moveBoundary", 1, 13.5)
    editor.call("shiftBoundary", 0, 4.0)
    editor.call("insertLines", 1, [""])
    editor.call("removeLine", 0)
    editor.type_time(1, "00:02.00")
    editor.call("toggleEditorMode")
    editor.call("toggleEditorMode")
    _ask(timeline, "zoomIn")
    _ask(timeline, "zoomOut")
    _ask(timeline, "snap")

    assert editor.warnings == []
