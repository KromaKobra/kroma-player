"""What the lyrics editor does to a list of lines, as arithmetic.

Pure, like :mod:`app.lyrics.parse` beside it — no I/O, no Qt, no database. The
split is by subject rather than by convenience: ``parse`` owns the *document
format*, this owns the three questions an editor asks that the format has no
opinion about.

- **A line was inserted. What time does it get?** :func:`insertion_times`
- **A line was re-timed. Where does it belong now?** :func:`sorted_index`
- **A block was pasted. Where are the line breaks?** :func:`split_pasted`

They live here rather than in the view model, and certainly rather than in QML,
because they are the only genuinely non-obvious part of the editor and the only
part worth a test. ``LyricsViewModel`` exposes each as a one-line slot so a
replacement GUI writing its own editor gets these rules instead of inventing
different ones.

Times are seconds, and ``None`` means "this line has no time" — the same
vocabulary :func:`app.lyrics.parse.format_lrc` reads.
"""
from __future__ import annotations

import re
from typing import List, Optional, Sequence

# Any newline a clipboard can carry. A block copied out of a browser on Windows
# arrives with CRLF and one copied off a very old Mac with bare CR, and both
# have to break in the same places as the LF the rest of the app assumes.
_NEWLINE_RE = re.compile(r'\r\n|\r|\n')

# How far apart lines land when there is nothing below them to aim at. One
# second is short enough that a run of them stays adjacent in the document and
# long enough that they are individually clickable, and it is what "one second
# after the last entry" means when several arrive at once.
_TRAILING_GAP = 1.0


def insertion_times(
    before: Optional[float],
    after: Optional[float],
    count: int = 1,
) -> List[Optional[float]]:
    """The times for *count* lines inserted between two neighbours.

    Four cases, and the first and last are deliberately not symmetrical with
    the middle — the rule is the one that reads right at each end of a
    document rather than the one that is uniform:

    ================================  ==========================================
    Neither neighbour has a time      every new line has none either: an
                                      unsynced document stays unsynced, and
                                      reordering it is just reordering it
    Nothing timed above (the top)     start at ``0.0`` and spread up to
                                      *after*, so a single line inserted at the
                                      top lands at exactly 00:00.00
    Nothing timed below (the end)     one second after the last, then two, then
                                      three
    Between two timed lines           spread evenly across the gap — one line
                                      between 10s and 20s lands at 15s, three
                                      land at 12.5, 15 and 17.5
    ================================  ==========================================

    Args:
        before: The nearest timed line above the insertion point, or ``None``.
        after: The nearest timed line below it, or ``None``.
        count: How many lines are going in.

    Returns:
        *count* times, ascending, or *count* ``None`` s.
    """
    if count <= 0:
        return []
    if before is None and after is None:
        return [None] * count

    if before is None:
        # The top. `0.0` first, because a line put above everything else starts
        # where the track does — there is nothing before it to come after.
        span = float(after)  # type: ignore[arg-type]
        if span <= 0:
            return [0.0 + index * _TRAILING_GAP for index in range(count)]
        step = span / count
        return [index * step for index in range(count)]

    if after is None or float(after) <= float(before):
        # The end — or two neighbours in the wrong order, which is bad data and
        # gets the same answer rather than a descending run that would then
        # sort itself somewhere nobody asked for.
        return [float(before) + (index + 1) * _TRAILING_GAP for index in range(count)]

    step = (float(after) - float(before)) / (count + 1)
    return [float(before) + (index + 1) * step for index in range(count)]


def times_for_insert(
    times: Sequence[Optional[float]],
    index: int,
    count: int = 1,
) -> List[Optional[float]]:
    """:func:`insertion_times`, given the whole list and where the lines go.

    *index* is the position the new lines take in the list as it will look
    afterwards, so ``0`` puts them at the top and ``len(times)`` at the end.

    The neighbours are the nearest lines that actually *have* a time, not
    simply the adjacent ones. That matters as soon as a document is part-timed:
    dropping a line below an untimed one should still place it against the last
    real timestamp above, rather than deciding there is nothing up there and
    starting again from zero.

    A line being *moved* is its own neighbour unless something is done about
    it, so the caller blanks its time before asking.
    """
    before: Optional[float] = None
    for value in reversed(list(times[:index])):
        if value is not None:
            before = value
            break

    after: Optional[float] = None
    for value in list(times[index:]):
        if value is not None:
            after = value
            break

    return insertion_times(before, after, count)


def sorted_index(times: Sequence[Optional[float]], index: int) -> int:
    """Where the line at *index* belongs once its time has been changed.

    Only the one line moves. Nothing else is re-timed and nothing untimed is
    disturbed, because a typed timecode is a statement about a single line and
    treating it as a request to reflow the document would lose work the user
    did not offer up.

    Lines with no time are anchors: the moved line is placed relative to the
    timed lines only, and an untimed line keeps whichever timed lines it was
    already sitting between.

    Returns:
        The destination index, in the list as it will look *after* the move.
        Equal to *index* when the line is already where it belongs, which is
        the caller's signal to do nothing.
    """
    if index < 0 or index >= len(times):
        return index

    moved = times[index]
    if moved is None:
        # No time is no opinion about order.
        return index

    rest = list(times[:index]) + list(times[index + 1:])

    # The destination is after every timed line that is not later than this
    # one. `<=` rather than `<` keeps a line that was retyped to a time it
    # already had from jumping over its identical neighbour.
    destination = 0
    for position, other in enumerate(rest):
        if other is not None and float(other) <= float(moved):
            destination = position + 1
    return destination


def split_pasted(text: str) -> List[str]:
    """A pasted block as the lines it should become.

    Leading and trailing blanks go — they are how a selection ends up with a
    stray newline on it, not something anybody meant to paste. Interior blanks
    stay, because a blank line between verses is a real part of a lyric sheet
    and dropping it would silently reformat what was handed over.

    Returns an empty list for an empty or all-whitespace paste, which is the
    caller's signal that there is nothing to do.
    """
    lines = [line.strip() for line in _NEWLINE_RE.split(text or "")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return lines
