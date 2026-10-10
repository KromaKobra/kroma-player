"""Editing lyrics by hand, and retiming them against the playhead.

Until this, every lyric document in the library had been written by somebody
else — a provider search, or a `LYRICS` tag that arrived with the file — and
there was no way to correct one. Both sources are wrong often enough to matter:
a mistyped line, a chorus two seconds early, or a document with no timestamps
at all.

Two things are pinned here, and they fail in different directions.

**The editor** must not lose work. A save that is refused — a timecode the
parser cannot read — has to leave the document alone and say so, because the
alternative is a user watching their corrections vanish. And an emptied editor
has to actually *delete*, because `LyricsService.save()` treats empty content as
a no-op on purpose (so a failed search cannot wipe good lyrics), which means
clearing the box would otherwise do nothing at all.

**The retime** must not lose lines. Its output is a document, and a line the
user never reached still has to come out the other side with a time on it — its
old one if it had one, and otherwise the session is refused rather than writing
a document with holes in it.

Everything here is driven synchronously against a stub player. No threads, no
event loop: `beginRetime`/`markLine`/`undoMark` are plain slots, and the only
asynchrony in the subsystem lives in the fetch worker, which is
`tests/test_lyrics_fetch.py`'s subject.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.lyrics.parse import is_synced, parse_lrc  # noqa: E402
from app.models.track import Track, TrackLyrics  # noqa: E402
from app.repositories.lyrics_repository import LyricsRepository  # noqa: E402
from app.services.lyrics_service import (  # noqa: E402
    SOURCE_MANUAL,
    SOURCE_SEARCH,
    LyricsService,
)
from app.settings.lyrics import LyricsSettings  # noqa: E402

SYNCED = "[00:01.00]one\n[00:05.00]two\n[00:09.00]three"
PLAIN = "one\ntwo\nthree"
# A line longer than SYNCED, for the tests that have to stamp several times and
# still have a session left afterwards — the last stamp ends one by itself.
SYNCED_LONG = SYNCED + "\n[00:13.00]four"


@pytest.fixture
def service(db, event_bus):
    return LyricsService(LyricsRepository(db.session), event_bus)


@pytest.fixture
def track(db):
    return db.session.query(Track).order_by(Track.id).first()


class _Player:
    """PlayerViewModel's surface, as LyricsViewModel uses it.

    A real `QObject` because the view model connects to two of its signals, and
    a recorder for the three playback calls a retime makes — those calls *are*
    the feature (arming at zero, starting on the first press, rewinding on an
    undo), so they are asserted on rather than stubbed away.
    """

    def __new__(cls, track):
        from PySide6.QtCore import QObject, Signal

        class Player(QObject):
            current_track_changed = Signal()
            position_changed = Signal()

            def __init__(self):
                QObject.__init__(self)
                self._current_track = track
                self.position = 0.0
                self.calls: list = []

            def play(self):
                self.calls.append(("play",))

            def pause(self):
                self.calls.append(("pause",))

            def seek(self, seconds):
                self.calls.append(("seek", float(seconds)))
                self.position = float(seconds)

        return Player()


def _vm(qt_app, track, service, stored=None):
    """A view model on a visible tab, over a library whose tag port has run.

    ``tag_sweep_done`` is set for the reason `tests/test_lyrics_fetch.py` gives:
    becoming active with the walk outstanding starts one, which is a different
    test's subject.
    """
    from app.settings.store import SettingsStore
    from app.viewmodels.lyrics_view_model import LyricsViewModel

    if stored is not None:
        service.save(int(track.id), stored, SOURCE_SEARCH, write_tag=False)

    settings = LyricsSettings(SettingsStore({}, {}))
    settings.tag_sweep_done = True

    player = _Player(track)
    vm = LyricsViewModel(player, service, settings)
    # Held so the player is not collected while the view model is connected to it.
    vm._test_player = player
    vm.active = True
    return vm


def _stored(db, track) -> str | None:
    row = db.session.get(TrackLyrics, int(track.id))
    return row.content if row is not None else None


# ==================================================================
# The editor
# ==================================================================


def test_an_edited_document_is_stored_as_the_user_wrote_it(qt_app, track, service, db):
    vm = _vm(qt_app, track, service, stored=SYNCED)

    vm.saveLyrics(["00:01.00", "00:06.50", "00:09.00"], ["one", "TWO", "three"])

    timestamps, lines = parse_lrc(_stored(db, track))
    assert lines == ["one", "TWO", "three"]
    assert timestamps == [1.0, 6.5, 9.0]


def test_a_hand_written_document_is_marked_manual(qt_app, track, service, db):
    """Not derivable from the text, which is the whole reason `source` is a column.

    It is also what lets a future re-search know it would be overwriting
    somebody's work rather than a provider's guess.
    """
    vm = _vm(qt_app, track, service, stored=SYNCED)

    vm.saveLyrics(["00:01.00"], ["only"])

    assert db.session.get(TrackLyrics, int(track.id)).source == SOURCE_MANUAL


def test_lines_with_no_timecodes_come_back_as_plain_text(qt_app, track, service, db):
    """The editor is also how an unsynced document gets its words fixed.

    Every field left blank has to produce something `is_synced()` answers no to,
    or the reader would show three lines of lyrics as a timestamped document
    with no times in it.
    """
    vm = _vm(qt_app, track, service, stored=PLAIN)

    vm.saveLyrics(["", "", ""], ["one", "TWO", "three"])

    stored = _stored(db, track)
    assert stored == "one\nTWO\nthree"
    assert not is_synced(stored)


def test_an_unreadable_timecode_is_refused_and_nothing_is_written(qt_app, track, service, db):
    """Refused, not discarded — the rule docs/GUI_API.md states for the track editor.

    Treating "quarter past" as a request to clear the field would throw away a
    line's timing because of a typo somewhere else on the screen.
    """
    vm = _vm(qt_app, track, service, stored=SYNCED)
    failures: list[str] = []
    vm.edit_failed.connect(failures.append)

    vm.saveLyrics(["00:01.00", "quarter past", "00:09.00"], ["one", "two", "three"])

    assert _stored(db, track) == SYNCED
    assert len(failures) == 1
    # The message names the line, because on a hundred-line document "that is
    # not a timecode" is not an answer anyone can act on.
    assert "2" in failures[0]


def test_trailing_blank_lines_are_dropped_but_interior_ones_survive(qt_app, track, service, db):
    """A blank line between verses is part of the document; one at the end is not.

    The editor's "add a line" button leaves an empty row behind every time it is
    pressed and not filled in, so trailing blanks are ordinary rather than
    exceptional.
    """
    vm = _vm(qt_app, track, service, stored=PLAIN)

    vm.saveLyrics(["", "", "", "", ""], ["one", "", "two", "", ""])

    assert _stored(db, track) == "one\n\ntwo"


def test_emptying_the_editor_deletes_rather_than_doing_nothing(qt_app, track, service, db):
    """`save()` no-ops on empty content, so clearing has to route to `delete()`.

    Without this the box would clear, the pane would not, and the lyrics would
    still be there on the next launch.
    """
    vm = _vm(qt_app, track, service, stored=SYNCED)

    vm.saveLyrics(["", ""], ["", "   "])

    assert _stored(db, track) is None


def test_the_metadata_header_survives_a_round_trip(qt_app, track, service, db):
    """`parse_lrc` drops `[ar:]`/`[ti:]`, so an edit would silently delete them.

    Nobody would notice until another player showed the wrong artist, which is
    exactly the kind of loss that is impossible to trace back to its cause.
    """
    vm = _vm(
        qt_app, track, service,
        stored="[ar:Someone]\n[ti:Something]\n[00:01.00]one\n[00:05.00]two",
    )

    vm.saveLyrics(["00:01.00", "00:05.00"], ["ONE", "two"])

    stored = _stored(db, track)
    assert stored.startswith("[ar:Someone]\n[ti:Something]\n")
    assert parse_lrc(stored)[1] == ["ONE", "two"]


# ==================================================================
# The retime session
# ==================================================================


def test_a_session_arms_with_nothing_highlighted(qt_app, track, service):
    """Paused at 0:00, and no line lit.

    The session has not been told anything yet, so claiming a line is current
    would be claiming something nobody said. It also puts a race between
    hitting play and hearing the first lyric into the one operation whose whole
    point is timing.
    """
    vm = _vm(qt_app, track, service, stored=SYNCED)

    vm.beginRetime()

    assert vm.is_retiming
    assert vm._test_player.calls == [("pause",), ("seek", 0.0)]
    assert vm.retime_index == -1
    assert vm.current_line_index == -1


def test_the_first_press_starts_the_track_and_lights_line_one(qt_app, track, service):
    vm = _vm(qt_app, track, service, stored=SYNCED)
    vm.beginRetime()
    vm._test_player.calls.clear()

    vm.markLine()

    assert vm._test_player.calls == [("play",)]
    assert vm.retime_index == 0
    # Lit from 0:00, and stamped 0:00 — the session and the playback agree.
    assert vm.retime_times[0] == 0.0


def test_each_press_marks_where_the_line_it_lights_begins(qt_app, track, service):
    """A press means "this line starts now", so it stamps the line it is about
    to highlight — not the one already lit, whose time this would be the *end*
    of."""
    vm = _vm(qt_app, track, service, stored=SYNCED)
    vm.beginRetime()
    vm.markLine()                       # line 0 at 0.0

    vm._test_player.position = 12.5
    vm.markLine()

    assert vm.retime_index == 1
    assert vm.retime_times[1] == 12.5
    # Line one keeps the moment it began, not the moment it ended.
    assert vm.retime_times[0] == 0.0


def test_what_was_lit_during_the_session_is_lit_at_the_same_moment_on_playback(
    qt_app, track, service, db
):
    """The whole contract of a retime, and the bug it shipped with.

    Playback used to run a line behind, because a press stamped the outgoing
    line — which reads plausibly, the press does land on the moment that line
    stops, but a stored timestamp is a line's *start*. So every line wore the
    time of the one after it.
    """
    vm = _vm(qt_app, track, service, stored=SYNCED)
    vm.beginRetime()

    for moment, lit in ((0.0, 0), (12.0, 1), (20.0, 2)):
        vm._test_player.position = moment
        vm.markLine()
        if vm.is_retiming:
            assert vm.retime_index == lit, f"marking at {moment}s"

    # Marking the last line is what ends a session, so there is no index left
    # to read for the third one — reaching here is the assertion.
    assert not vm.is_retiming

    # Now replay the same track and ask the same question at the same moments.
    for moment, expected in ((5.0, 0), (13.0, 1), (21.0, 2)):
        vm._test_player.position = moment
        vm._test_player.position_changed.emit()
        assert vm.current_line_index == expected, f"at {moment}s"

    assert parse_lrc(_stored(db, track))[0] == [0.0, 12.0, 20.0]


def test_the_highlight_holds_still_between_presses(qt_app, track, service):
    """The playhead scan stands down while a session runs: the user is marking
    the line they can hear, and the stored timings are the ones being
    replaced."""
    vm = _vm(qt_app, track, service, stored=SYNCED)
    vm.beginRetime()
    vm.markLine()
    vm._test_player.position = 4.0
    vm.markLine()

    vm._test_player.position = 99.0
    vm._test_player.position_changed.emit()

    assert vm.current_line_index == vm.retime_index == 1


def test_an_undo_steps_back_and_rewinds_so_the_moment_returns(qt_app, track, service):
    """Un-stamping alone would leave the playhead past the line to be caught,
    which would mean seeking by hand — the thing a retime exists to avoid."""
    vm = _vm(qt_app, track, service, stored=SYNCED_LONG)
    vm.beginRetime()
    vm.markLine()                       # line 0 at 0.0
    vm._test_player.position = 6.0
    vm.markLine()                       # line 1 at 6.0
    vm._test_player.position = 11.0
    vm.markLine()                       # line 2 at 11.0
    vm._test_player.calls.clear()

    vm.undoMark()

    assert vm.retime_index == 1
    assert vm.retime_times[2] == -1.0
    # Back to where the line now lit begins, so line 2 comes round again.
    assert vm._test_player.calls == [("seek", 6.0)]


def test_undoing_the_first_press_takes_back_the_whole_opening_gesture(
    qt_app, track, service
):
    """It started the track as well as timing line one, so undoing it has to
    put both back — otherwise the song runs away from a line nobody has
    marked."""
    vm = _vm(qt_app, track, service, stored=SYNCED)
    vm.beginRetime()
    vm.markLine()
    vm._test_player.calls.clear()

    vm.undoMark()

    assert vm.retime_index == -1
    assert vm.retime_times[0] == -1.0
    assert vm._test_player.calls == [("pause",), ("seek", 0.0)]
    # And the next press starts the song again rather than marking into silence.
    vm.markLine()
    assert vm._test_player.calls[-1] == ("play",)


def test_an_undo_before_anything_is_marked_does_nothing(qt_app, track, service):
    vm = _vm(qt_app, track, service, stored=SYNCED)
    vm.beginRetime()
    vm._test_player.calls.clear()

    vm.undoMark()

    assert vm.retime_index == -1
    assert vm._test_player.calls == []


def test_the_last_mark_finishes_the_session_by_itself(qt_app, track, service, db):
    vm = _vm(qt_app, track, service, stored=SYNCED)
    vm.beginRetime()
    vm.markLine()
    vm._test_player.position = 6.0
    vm.markLine()
    vm._test_player.position = 11.0
    vm.markLine()

    assert not vm.is_retiming
    assert parse_lrc(_stored(db, track))[0] == [0.0, 6.0, 11.0]


def test_a_completed_session_gives_an_unsynced_document_its_timings(qt_app, track, service, db):
    """The other half of what retiming is for. `synced_lines` is empty here, so
    the session works from `plain_text` — and `retime_lines` is what a GUI has
    to draw, or it shows nothing in exactly this case."""
    vm = _vm(qt_app, track, service, stored=PLAIN)
    vm.beginRetime()

    assert list(vm.retime_lines) == ["one", "two", "three"]

    vm.markLine()
    vm._test_player.position = 4.0
    vm.markLine()
    vm._test_player.position = 8.5
    vm.markLine()

    stored = _stored(db, track)
    assert is_synced(stored)
    assert parse_lrc(stored) == ([0.0, 4.0, 8.5], ["one", "two", "three"])


def test_finishing_early_keeps_the_timings_the_rest_of_the_lines_arrived_with(
    qt_app, track, service, db
):
    """What makes a partial retime possible: fixing a drifting first verse
    should not mean sitting through the whole song."""
    vm = _vm(qt_app, track, service, stored=SYNCED)
    vm.beginRetime()
    vm.markLine()
    vm._test_player.position = 6.0
    vm.markLine()

    assert vm.can_finish_retime
    vm.finishRetime()

    # Lines 0 and 1 are the new marks; line 2 is the one it came in with.
    assert parse_lrc(_stored(db, track))[0] == [0.0, 6.0, 9.0]


def test_cancelling_leaves_the_stored_document_exactly_as_it_was(qt_app, track, service, db):
    vm = _vm(qt_app, track, service, stored=SYNCED)
    vm.beginRetime()
    vm.markLine()
    vm._test_player.position = 6.0
    vm.markLine()

    vm.cancelRetime()

    assert not vm.is_retiming
    assert _stored(db, track) == SYNCED


def test_playing_another_track_ends_the_session(qt_app, track, service, db):
    """A session belongs to one track. Left running, the next song's playhead
    would be stamped onto the last song's words — and a track ending on its own
    is the ordinary way this happens, not a deliberate one."""
    vm = _vm(qt_app, track, service, stored=SYNCED)
    vm.beginRetime()
    vm.markLine()

    vm._test_player.current_track_changed.emit()

    assert not vm.is_retiming
    assert _stored(db, track) == SYNCED


def test_closing_the_window_drops_a_session_rather_than_saving_it(qt_app, track, service, db):
    """`shutdown()` cancels the tag walk to keep what it wrote; it cancels a
    retime for the opposite reason — the work is unfinished and unapproved."""
    vm = _vm(qt_app, track, service, stored=SYNCED)
    vm.beginRetime()
    vm.markLine()

    vm.shutdown()

    assert not vm.is_retiming
    assert _stored(db, track) == SYNCED


def test_a_track_with_no_words_at_all_cannot_be_retimed(qt_app, track, service):
    vm = _vm(qt_app, track, service)
    failures: list[str] = []
    vm.edit_failed.connect(failures.append)

    vm.beginRetime()

    assert not vm.is_retiming
    assert len(failures) == 1


# ==================================================================
# The highlight, and the editor's rules
# ==================================================================


def test_nothing_is_highlighted_before_the_first_line_is_sung(qt_app, track, service):
    """Almost every song has an intro, and the first line used to be lit
    through all of it.

    The scan started at index 0, which answers "line one" for any position at
    all — so a document whose first words land at 00:12 showed them highlighted
    from the moment the track began.
    """
    vm = _vm(qt_app, track, service, stored="[00:12.00]one\n[00:20.00]two")

    vm._test_player.position = 5.0
    vm._test_player.position_changed.emit()
    assert vm.current_line_index == -1

    vm._test_player.position = 13.0
    vm._test_player.position_changed.emit()
    assert vm.current_line_index == 0


def test_seeking_back_before_the_first_line_puts_the_highlight_out_again(
    qt_app, track, service
):
    vm = _vm(qt_app, track, service, stored="[00:12.00]one\n[00:20.00]two")
    vm._test_player.position = 25.0
    vm._test_player.position_changed.emit()
    assert vm.current_line_index == 1

    vm._test_player.position = 2.0
    vm._test_player.position_changed.emit()

    assert vm.current_line_index == -1


def test_the_editor_rules_reach_qml_as_seconds(qt_app, track, service):
    """The three slots are how the GUI asks; `-1` is "no time" throughout,
    the same vocabulary `retime_times` uses."""
    vm = _vm(qt_app, track, service, stored=SYNCED)

    # A row dropped between the second and third lines.
    assert vm.insertionTimes(["00:01.00", "00:05.00", "00:09.00"], 2, 1) == [7.0]
    # …at the very top, and past the end.
    assert vm.insertionTimes(["00:01.00", "00:05.00"], 0, 1) == [0.0]
    assert vm.insertionTimes(["00:01.00", "00:05.00"], 2, 1) == [6.0]
    # An unsynced draft stays unsynced.
    assert vm.insertionTimes(["", "", ""], 1, 2) == [-1.0, -1.0]


def test_the_slots_take_the_raw_text_of_a_timecode_field(qt_app, track, service):
    """So a GUI never has to carry its own copy of the timecode parser — the
    same latitude `saveLyrics` already allows."""
    vm = _vm(qt_app, track, service, stored=SYNCED)

    assert vm.sortedIndex(["00:01.00", "00:05.00", "00:03.00"], 2) == 1
    assert vm.splitPastedLines("one\r\ntwo\n\n") == ["one", "two"]


def test_a_half_typed_timecode_reads_as_no_time_rather_than_refusing(
    qt_app, track, service
):
    """These run against a draft somebody is still typing into, where `0:` on
    its way to `01:30` is ordinary. `saveLyrics` is the strict one, because
    that text is on its way to disk."""
    vm = _vm(qt_app, track, service, stored=SYNCED)

    assert vm.sortedIndex(["00:01.00", "nonsense"], 1) == 1


def test_the_draft_reads_back_as_a_column_of_numbers(qt_app, track, service):
    """An editor that lays its lines out *in proportion* needs the timings as
    numbers on every frame, while the draft holds them as the raw text of a
    field. Parsing that text in QML would be a second copy of the timecode
    parser in the GUI, which is what these slots exist to prevent."""
    vm = _vm(qt_app, track, service, stored=SYNCED)

    assert vm.parseTimes(["00:01.00", "1:23.45", "83.4"]) == [1.0, 83.45, 83.4]


def test_a_line_with_no_time_reads_back_as_minus_one(qt_app, track, service):
    """The same vocabulary the other three slots use, and the same latitude: a
    blank field and a half-typed stamp are both ordinary in a draft somebody is
    still working on, and neither is an error."""
    vm = _vm(qt_app, track, service, stored=SYNCED)

    assert vm.parseTimes(["", "0:", "nonsense", "-1", "00:02.00"]) == [
        -1.0, -1.0, -1.0, -1.0, 2.0,
    ]
