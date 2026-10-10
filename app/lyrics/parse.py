"""Reading and writing a lyric document: is it timestamped, what are the lines?

Pure, and deliberately so — no I/O, no Qt, no database. A lyric document
arrives as one string whatever produced it (a file tag, a lyric search, the
``lyrics`` table), and everything downstream needs the same two answers about
it. Keeping them here means the view model, the tests and any future consumer
agree by construction rather than by each carrying a copy of the regex.

This module used to be read-only, which was right while every document in the
library had been written by somebody else. The lyrics editor changed that: a
user who fixes a line or retimes a chorus needs the parallel lists turned back
into a document, so :func:`format_lrc` is the exact inverse of
:func:`parse_lrc` and :func:`parse_timecode` the inverse of the timestamp it
writes. They live beside the parser rather than in the view model because a
format known in two places is a format that drifts.
"""
from __future__ import annotations

import re
from typing import Optional, Sequence

# One LRC line: `[mm:ss.xx] the words`. The fractional part is optional because
# plenty of providers emit whole seconds, and the trailing group is allowed to
# be empty — an instrumental break is a real timestamped line with no words.
_LRC_TIMESTAMP_RE = re.compile(r'\[(\d+):(\d+(?:\.\d+)?)\](.*)')

# Whether the document is LRC at all. Looser than the line pattern on purpose:
# it only has to find one plausible timestamp anywhere, and a document whose
# first lines are `[ar:]`/`[ti:]` metadata still answers yes on its first real
# line.
_LRC_DETECT_RE = re.compile(r'\[\d+:\d+')

# An LRC metadata tag: `[ar:Someone]`, `[ti:Something]`, `[offset:+500]`. The
# key is alphabetic, which is the whole thing that tells it apart from a
# timestamp — `[00:12.34]` starts with digits and cannot match this.
_LRC_HEADER_RE = re.compile(r'\[[a-zA-Z#][a-zA-Z0-9_#]*:.*\]$')

# What a human may type into a timecode field. Either `mm:ss` with optional
# fraction, or a bare number of seconds. Surrounding brackets are tolerated
# because the value on screen beside the line is the one being copied.
_TIMECODE_RE = re.compile(r'^\[?(?:(\d+):)?(\d+(?:[.,]\d+)?)\]?$')


def is_synced(text: str) -> bool:
    """Whether *text* is an LRC document rather than plain lyrics.

    Derived rather than stored — see :class:`app.models.track.TrackLyrics` for
    why there is no ``synced`` column to read this off instead.
    """
    return bool(text) and bool(_LRC_DETECT_RE.search(text))


def parse_lrc(text: str) -> tuple[list[float], list[str]]:
    """Split an LRC document into ``(timestamps, lines)``, in file order.

    The two lists are parallel: ``lines[i]`` is sung at ``timestamps[i]``
    seconds. Lines that carry no timestamp — the ``[ar:]`` header block, blank
    separators, anything malformed — are dropped rather than kept with a
    guessed time, because the only consumer is a playhead scan and a line with
    no time has no position to be scanned to.

    Returns two empty lists for plain text, which is the same answer as "an LRC
    document with no parseable lines" and wants the same handling.
    """
    timestamps: list[float] = []
    lines: list[str] = []
    for line in (text or "").split('\n'):
        match = _LRC_TIMESTAMP_RE.match(line.strip())
        if match:
            minutes, seconds, content = match.groups()
            timestamps.append(int(minutes) * 60 + float(seconds))
            lines.append(content.strip())
    return timestamps, lines


def parse_lrc_header(text: str) -> str:
    """The document's leading ``[ar:]``/``[ti:]`` metadata block, verbatim.

    :func:`parse_lrc` throws these lines away, which is right for a playhead
    scan and wrong for a round trip: without them, opening a document in the
    editor and saving it back would quietly delete the artist, title and offset
    somebody else's player wrote. :func:`format_lrc` takes what this returns
    and puts it back on the front.

    Only the *leading* block counts. A `[ar:]` sitting halfway down a document
    is not a header, it is a line, and guessing otherwise would reorder it.
    """
    kept: list[str] = []
    for line in (text or "").split('\n'):
        stripped = line.strip()
        if not stripped:
            # A blank line is separation, not the end of the block. Skipped
            # rather than kept, so the block comes back out normalised.
            continue
        if not _LRC_HEADER_RE.match(stripped):
            break
        kept.append(stripped)
    return '\n'.join(kept)


def format_lrc(
    timestamps: Sequence[Optional[float]],
    lines: Sequence[str],
    header: str = "",
) -> str:
    """Turn parallel ``(timestamps, lines)`` back into one document.

    The inverse of :func:`parse_lrc`, and deliberately total where that one is
    lossy: a ``None`` timestamp writes the line with no ``[mm:ss.xx]`` prefix
    rather than dropping it. That is what lets one function serve both kinds of
    document — every timestamp ``None`` produces plain text that
    :func:`is_synced` correctly answers ``False`` to, and a mix produces an LRC
    document whose untimed lines survive in the file even though a playhead
    scan will not reach them.

    Two hundredths of a second is the resolution, which is the LRC convention
    and comfortably finer than the position updates a retime can stamp with.

    Args:
        timestamps: Seconds per line; ``None`` (or negative) for "no time".
        lines: The words, one per timestamp. Extra lines are timed ``None``.
        header: A metadata block from :func:`parse_lrc_header` to put back.

    Returns:
        The document, with no trailing newline.
    """
    out: list[str] = []
    if header.strip():
        out.extend(header.strip().split('\n'))

    for index, line in enumerate(lines):
        stamp = timestamps[index] if index < len(timestamps) else None
        text = (line or "").strip()
        if stamp is None or stamp < 0:
            out.append(text)
        else:
            out.append(f"{format_timecode(float(stamp))}{text}")
    return '\n'.join(out)


def format_timecode(seconds: float) -> str:
    """One ``[mm:ss.xx]`` stamp. Rounds to centiseconds without carrying wrong.

    The arithmetic goes through whole centiseconds rather than formatting
    minutes and seconds separately, because ``59.999`` formatted as ``%05.2f``
    rounds to the string ``60.00`` and writes a timestamp that does not exist.
    """
    centis = max(0, int(round(max(0.0, seconds) * 100)))
    minutes, remainder = divmod(centis, 6000)
    secs, hundredths = divmod(remainder, 100)
    return f"[{minutes:02d}:{secs:02d}.{hundredths:02d}]"


def parse_timecode(text: str) -> Optional[float]:
    """Read a timecode a person typed, in seconds, or ``None`` if it is not one.

    Accepts what the editor is likely to be handed: ``1:23.45``, ``1:23``,
    ``83.4``, and any of those still wearing the brackets they were copied
    with. A comma is read as a decimal point, because a keyboard laid out for
    most of Europe puts one there.

    ``None`` means "this line has no time" — the editor leaves the field blank
    for an unsynced document — and it is also the answer for anything
    unparseable, which the caller turns into a refusal rather than a silent
    zero.
    """
    stripped = (text or "").strip()
    if not stripped:
        return None
    match = _TIMECODE_RE.match(stripped)
    if not match:
        return None
    minutes, seconds = match.groups()
    try:
        value = float(seconds.replace(',', '.'))
    except ValueError:  # pragma: no cover — the regex already guaranteed this
        return None
    if minutes is not None:
        value += int(minutes) * 60
    return value
