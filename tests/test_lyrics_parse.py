"""Reading and writing a lyric document.

`app/lyrics/parse.py` is pure functions with no I/O behind them, which makes
this the cheap place to be exhaustive — every odd document a provider or an old
file tag can produce gets asked here rather than discovered in front of a user
with a playhead running.

The property that matters most is the pairing: `parse_lrc` returns two parallel
lists, and `LyricsViewModel._on_position_changed()` indexes one with the other.
A line kept without its timestamp, or vice versa, would put the wrong words on
screen for the rest of the song.

The second half of the file is the writing side, which the lyrics editor added.
Its property is the round trip: whatever `parse_lrc` reads out, `format_lrc` has
to be able to put back, or every save would quietly reshape the document it was
handed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.lyrics.parse import (  # noqa: E402
    format_lrc,
    format_timecode,
    is_synced,
    parse_lrc,
    parse_lrc_header,
    parse_timecode,
)

LRC = """[00:12.00]The first line
[00:15.30]The second line
[01:02.75]Past a minute
"""

PLAIN = """The first line
The second line
Past a minute
"""


# ============================================================== is_synced


@pytest.mark.parametrize("text", [
    LRC,
    "[00:01]only one line",
    "[ar:Someone]\n[ti:Something]\n[00:04.00]the words",   # metadata header first
    "trailing junk\n[03:22.10]still LRC",
    "[0:0]",                             # unpadded, but a timestamp all the same
])
def test_timestamped_documents_are_synced(text):
    assert is_synced(text) is True


@pytest.mark.parametrize("text", [
    PLAIN,
    "",
    "   \n\n  ",
    "[ar:Someone]\n[ti:Something]",      # LRC metadata, but no timed lines
    "[verse 1]\nsome words",             # the square brackets songs actually use
])
def test_everything_else_is_not(text):
    assert is_synced(text) is False


def test_none_and_empty_do_not_raise():
    assert is_synced("") is False


# ============================================================== parse_lrc


def test_lines_and_timestamps_come_back_paired_and_in_order():
    timestamps, lines = parse_lrc(LRC)

    assert lines == ["The first line", "The second line", "Past a minute"]
    assert timestamps == [12.0, 15.3, 62.75]
    assert len(timestamps) == len(lines)


def test_minutes_are_folded_into_seconds():
    timestamps, _lines = parse_lrc("[02:30.00]halfway")

    assert timestamps == [150.0]


def test_whole_second_timestamps_are_accepted():
    """Plenty of providers emit no fractional part at all."""
    timestamps, lines = parse_lrc("[00:09]nine seconds in")

    assert timestamps == [9.0]
    assert lines == ["nine seconds in"]


def test_an_empty_line_keeps_its_timestamp():
    """An instrumental break is a real timed line with no words in it."""
    timestamps, lines = parse_lrc("[00:05.00]\n[00:09.00]back in")

    assert timestamps == [5.0, 9.0]
    assert lines == ["", "back in"]


def test_untimed_lines_are_dropped_rather_than_guessed_at():
    timestamps, lines = parse_lrc(
        "[ar:Someone]\n\n[00:04.00]the words\nstray text\n[00:08.00]more words"
    )

    assert lines == ["the words", "more words"]
    assert timestamps == [4.0, 8.0]


def test_plain_text_parses_to_nothing():
    assert parse_lrc(PLAIN) == ([], [])


def test_an_empty_document_parses_to_nothing():
    assert parse_lrc("") == ([], [])


@pytest.mark.parametrize("text", [
    "[aa:bb.cc]not a time",
    "[00:]truncated",
    "[:30.00]no minutes",
    "[-1:00.00]negative",
])
def test_malformed_timestamps_are_dropped_not_raised(text):
    """A bad document must degrade to "no synced lyrics", never to a traceback."""
    assert parse_lrc(text) == ([], [])


def test_surrounding_whitespace_is_stripped_from_both_ends():
    timestamps, lines = parse_lrc("  [00:03.00]   spaced out   ")

    assert timestamps == [3.0]
    assert lines == ["spaced out"]


# ==================================================================
# Writing one back
# ==================================================================


def test_a_document_survives_being_read_and_written():
    """The round trip is the contract the editor rests on.

    Every save re-serialises what was parsed, so anything `format_lrc` cannot
    reproduce is something the editor silently changes about documents nobody
    asked it to touch.
    """
    timestamps, lines = parse_lrc(LRC)

    assert parse_lrc(format_lrc(timestamps, lines)) == (timestamps, lines)


def test_lines_with_no_time_are_written_as_plain_text():
    """What makes one serialiser enough for both kinds of document.

    An unsynced document edited in the editor comes back with every timecode
    field blank, and it has to store as something `is_synced()` answers no to —
    otherwise the reader shows a timestamped document with no timestamps.
    """
    document = format_lrc([None, None], ["one", "two"])

    assert document == "one\ntwo"
    assert not is_synced(document)


def test_a_negative_time_means_the_same_as_none():
    """QML has no comfortable null inside a QVariantList, so the view model
    spells "no time" as -1 and this is where that lands."""
    assert format_lrc([-1, 4.0], ["one", "two"]) == "one\n[00:04.00]two"


def test_a_mixed_document_keeps_its_untimed_lines_in_the_file():
    """`parse_lrc` will not scan to them, but they are still the user's words
    and dropping them here would be losing text on the way to disk."""
    document = format_lrc([1.0, None, 5.0], ["one", "two", "three"])

    assert document.split("\n")[1] == "two"


def test_the_header_block_is_put_back_on_the_front():
    header = parse_lrc_header("[ar:Someone]\n[ti:Something]\n[00:04.00]words")

    assert format_lrc([4.0], ["words"], header=header) == (
        "[ar:Someone]\n[ti:Something]\n[00:04.00]words"
    )


@pytest.mark.parametrize("text,expected", [
    ("[ar:Someone]\n[00:04.00]words", "[ar:Someone]"),
    ("[ar:A]\n\n[ti:B]\n[00:04.00]words", "[ar:A]\n[ti:B]"),
    ("[offset:+500]\n[00:04.00]words", "[offset:+500]"),
    ("[00:04.00]words", ""),
    ("plain text\n[ar:Someone]", ""),          # not leading, so not a header
    ("", ""),
])
def test_only_the_leading_metadata_block_counts_as_a_header(text, expected):
    """A `[ar:]` halfway down a document is a line, not a header. Hoisting one
    would reorder somebody's lyrics."""
    assert parse_lrc_header(text) == expected


def test_a_timestamp_never_rounds_into_a_second_that_does_not_exist():
    """59.999 formatted naively rounds to "60.00" and writes a time no parser
    should accept. The arithmetic goes through whole centiseconds to stop it."""
    assert format_timecode(59.999) == "[01:00.00]"
    assert format_timecode(0) == "[00:00.00]"
    assert format_timecode(-5) == "[00:00.00]"


@pytest.mark.parametrize("text,expected", [
    ("1:23.45", 83.45),
    ("01:23.45", 83.45),
    ("1:23", 83.0),
    ("83.4", 83.4),
    ("[00:05.00]", 5.0),      # still wearing the brackets it was copied with
    ("1:23,45", 83.45),       # a decimal comma
    ("", None),
    ("   ", None),
    ("junk", None),
    ("quarter past", None),
    ("-1:00", None),
])
def test_a_typed_timecode_reads_back_as_seconds_or_not_at_all(text, expected):
    """`None` is what the editor turns into a refusal. Anything unparseable
    quietly becoming zero would move a line to the top of the song."""
    assert parse_timecode(text) == expected


def test_what_is_written_can_be_typed_back_in():
    """`format_timecode` and `parse_timecode` are two halves of one field: the
    editor shows the first and reads the second."""
    for seconds in (0.0, 1.5, 62.75, 3599.99):
        assert parse_timecode(format_timecode(seconds)) == pytest.approx(seconds, abs=0.005)
