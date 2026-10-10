"""When two descriptions of a song are the same **recording**.

An external id often names one *upload*, not one recording. A catalogue can mint
a fresh id for every release a track appears on — the album's audio, the album's
music video, the single, the deluxe edition — so a library holds whichever one
the user happened to fetch from, and an album read from the service can share
none of its ids with the copies already owned. Measured on a real library:
*Walkerworld* matched 2 of its 10 tracks by id, and the other eight are owned
under the singles' uploads. No amount of id collecting fixes that; the question
has to be asked a second way.

So this module answers it the other way: **same title, same length, same
artist.** Not as a replacement for the id — an id is evidence and this is a
resemblance, so every caller asks the id first and only lands here when it comes
back empty — but as the only thing that can recognise a recording across the
releases it was pressed onto.

The rule is deliberately narrow, and each of the three tests earns its place
against a measured failure:

============================  ==============================================
Loosening it this way…        …matched these, which are different recordings
============================  ==============================================
drop the artist test          Faouzia — *Hero* (175s) to
                              Alan Walker — *Hero* (176s)
strip every parenthetical     *Barcelona* (206s) to
                              *Barcelona (The Unborn Remix)* (207s)
============================  ==============================================

Both are the shape of mistake this codebase already refuses everywhere it
compares names — identifying an artist or an album matches whole names exactly,
for the reason given in ``app.importer.import_workers.IdentifyWorker``. The
difference here is that a wrong answer is *recoverable*: the worst it does is
hide a row the user wanted to fetch, where a wrong external id is written to
the database permanently. That buys the duration tolerance and the
``(feat. …)`` strip below, and nothing more.

Pure: no session, no network, no Qt. It lives in ``app.library`` because "is
this the same recording" is a question about the library, and ``app.search``,
``app.services`` and ``app.viewmodels`` have all now had to ask it.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple, Optional

#: How far two durations may differ and still be one recording, in seconds.
#:
#: A library's duration comes from the file it transcoded; the service's comes
#: from the release. They agree to the second most of the time and disagree by
#: one often enough to matter — *the cure* is 297s owned against 298s published,
#: and exact equality misses it. Two is that, with a second of headroom.
#:
#: It does not want to grow. At ±3 the measured collision count on a real
#: library starts climbing, and the pairs it adds are remixes and edits of the
#: same song rather than the same recording twice.
DURATION_TOLERANCE_SECONDS = 2

# The credited artists arrive as one joined string from several sources — a
# `get_song` payload's `author`, a playlist entry's artist blob, the library's
# own comma-joined credit. Only these separators are split on, and "/" is
# deliberately not among them: it would tear "AC/DC" in half every time it was
# written.
_AUTHOR_SPLIT = re.compile(r",\s*&\s*|,\s*|\s+&\s+")

# A trailing credit parenthetical — "(feat. X)", "[ft. X]", "(with X)". The one
# thing stripped from a title before comparing, because whether the credit is in
# the title or only in the artist field varies between releases of the same
# recording and says nothing about which recording it is.
#
# Note what is *not* here: "(Remix)", "(VIP Mix)", "(Extended)", "(Live)". Those
# are exactly what tells two recordings of near-identical length apart, and a
# rule that removed them would match a song to its own remix.
_CREDIT_SUFFIX = re.compile(
    r"\s*[\(\[]\s*(?:feat|ft|featuring|with)\b[^)\]]*[\)\]]",
    re.IGNORECASE,
)


class Recording(NamedTuple):
    """One song, reduced to what identifies it across releases.

    Built by :func:`recording_of` rather than by hand, so that every producer
    normalises the same way. A field left empty means *"unknown"*, and
    :func:`same_recording` treats unknown as "no match" rather than as a
    wildcard — see its docstring.
    """

    key: str
    duration: int
    artists: frozenset[str]


def split_artists(text: str) -> list[str]:
    """The credited artists in *text*, in order, stripped, blanks dropped.

    The single statement of the separator rule. :func:`artist_names` is the same
    thing as a set for comparison; this one keeps the order, for a caller that
    needs to pair names with something positionally.
    """
    return [name.strip() for name in _AUTHOR_SPLIT.split(text or "") if name.strip()]


def artist_names(text: str) -> set[str]:
    """The credited artists in *text*, casefolded, for comparison.

    Casefolded at *read* time because nothing normalises artist names on the way
    in: ``ArtistRepository.get_or_create`` matches exactly and case-sensitively,
    on purpose, so a library may legitimately hold both "Skrillex" and
    "skrillex" as separate rows.
    """
    return {name.casefold() for name in split_artists(text)}


def title_key(title: str) -> str:
    """A title reduced to what names the recording rather than the pressing.

    Casefolded, whitespace-trimmed, and with a trailing credit parenthetical
    removed — "Sorry (feat. ISÁK)" and "Sorry" are one recording, and which of
    the two spellings a given release uses is not information about the song.

    Everything else in the title stays, including anything in brackets that is
    not a credit. See :data:`_CREDIT_SUFFIX` for why that line is drawn there
    and not one step further.
    """
    return _CREDIT_SUFFIX.sub("", title or "").strip().casefold()


def recording_of(
    title: Any, artist: Any, duration: Any
) -> Recording:
    """Normalise one description into a :class:`Recording`.

    Takes the three fields as they arrive from anywhere — a unified search
    result, a database row, a QML metadata map — and is forgiving about their
    types, because the callers are not all Python-typed. What it will not do is
    invent: a duration that cannot be read as a number becomes 0, which
    :func:`same_recording` refuses to match on.
    """
    try:
        seconds = int(duration or 0)
    except (TypeError, ValueError):
        seconds = 0
    return Recording(
        key=title_key(str(title or "")),
        duration=max(seconds, 0),
        artists=frozenset(artist_names(str(artist or ""))),
    )


def same_recording(left: Recording, right: Recording) -> bool:
    """Whether these two descriptions name one recording.

    Three tests, all of which must pass, and **every one of them fails closed on
    missing data.** An untitled row, an untimed row, or a row crediting nobody
    matches nothing at all — rather than matching everything, which is what a
    "skip the tests we cannot run" rule would do the moment a source stopped
    supplying a field.
    """
    if not left.key or left.key != right.key:
        return False
    if not left.duration or not right.duration:
        return False
    if abs(left.duration - right.duration) > DURATION_TOLERANCE_SECONDS:
        return False
    return bool(left.artists & right.artists)


def duration_window(duration: int) -> range:
    """Every whole-second length that could be *duration*, tolerance included.

    Durations are stored as integers, so a tolerance is a small set of exact
    values rather than a range predicate — which is what lets a caller ask the
    database about a whole album's worth of candidates in one ``IN`` query
    instead of one query per track. Empty for an unknown duration, so a row that
    cannot match does not drag the whole library into the candidate set.
    """
    if duration <= 0:
        return range(0)
    low = max(duration - DURATION_TOLERANCE_SECONDS, 1)
    return range(low, duration + DURATION_TOLERANCE_SECONDS + 1)


def best_match(
    wanted: Recording, candidates: dict[int, Recording]
) -> Optional[int]:
    """The library track id in *candidates* that is *wanted*, or None.

    **An ambiguity resolves to the lowest id rather than to nothing, and this is
    the one comparison in the application where that is right.** Everywhere else
    a tie is dropped, because there the cost of guessing is a wrong answer
    written down — a wrong browse id, a wrong running order, neither of which
    anything downstream would notice. Here every candidate is a track the
    library already holds, so any of them is a true answer to "do you have
    this?", and refusing would be the only false one available. In practice the
    ties are a recording owned twice.

    Lowest id, specifically, so the answer does not depend on dictionary order:
    the same question asked twice gets the same track.
    """
    matches = [
        track_id
        for track_id, candidate in candidates.items()
        if same_recording(wanted, candidate)
    ]
    return min(matches) if matches else None
