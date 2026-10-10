"""Relevance scoring for a search that returns artists and tracks together.

Interleaving two kinds of thing in one list only works if they can be compared,
and "compared" here means one number per row from one scale. That is all this
module is: a score, and the rule for ordering rows that tie.

The scale is deliberately coarse — six bands, not a continuous similarity — because
what a user is doing when they type three letters is looking for a *specific*
thing, and the useful distinctions are all of the form "this one starts with what
I typed and that one merely contains it". A finer metric would reorder results on
edits that should not have moved anything.

Artists sit one band above the track band they are level with. An artist row is a
route to everything they made, so when a query matches an artist's name and a
track's title equally well, the artist is the more useful answer — it contains the
other. That is the whole of the bias, and it is why the bands interleave rather
than putting all artists on top: a track whose title is exactly the query still
outranks an artist whose name merely contains it.

No Qt, no database, no view model. Given rows, it orders them.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

# The bands. Artists and tracks alternate rather than separating, so relevance
# rather than kind decides the order.
SCORE_ARTIST_EXACT = 100
SCORE_TRACK_EXACT = 90
SCORE_ARTIST_PREFIX = 80
SCORE_TRACK_PREFIX = 70
SCORE_ARTIST_CONTAINS = 60
SCORE_TRACK_CONTAINS = 50
SCORE_ALBUM_CONTAINS = 40

def _score(haystack: str, needle: str, exact: int, prefix: int, contains: int) -> int:
    """Band *haystack* against *needle*, or 0 when it does not match at all."""
    if not haystack or not needle:
        return 0
    text = haystack.strip().lower()
    if text == needle:
        return exact
    if text.startswith(needle):
        return prefix
    if needle in text:
        return contains
    return 0


def score_artist(name: str, query: str) -> int:
    """How well an artist's name answers *query*."""
    return _score(
        name, query.strip().lower(),
        SCORE_ARTIST_EXACT, SCORE_ARTIST_PREFIX, SCORE_ARTIST_CONTAINS,
    )


def score_track(title: str, album: str, query: str) -> int:
    """How well a track answers *query*, on its title or failing that its album.

    The title is tried first and its worst band still beats an album match, because
    an album name matching is a much weaker signal about this particular track — it
    matches every other track on the record just as well.
    """
    needle = query.strip().lower()
    by_title = _score(
        title, needle, SCORE_TRACK_EXACT, SCORE_TRACK_PREFIX, SCORE_TRACK_CONTAINS
    )
    if by_title:
        return by_title
    return SCORE_ALBUM_CONTAINS if needle and needle in (album or "").lower() else 0


def rank(
    artists: Iterable[Dict[str, Any]],
    tracks: Iterable[tuple],
    query: str,
) -> List[Dict[str, Any]]:
    """Order artist and track rows into one relevance-sorted list.

    Args:
        artists: ``{id, name, image_hash, track_count, external_ids}`` dicts.
        tracks: ``(id, title, album)`` tuples, as ``quick_search_rows`` returns.
        query: what the user typed.

    Returns:
        Row descriptors: ``{"kind": "artist"|"track", ...}``, most relevant first.
        Every input row appears exactly once — the database has already decided
        what matches, and this only decides the order. Nothing is truncated:
        "Play all" over the results has to mean all of them.
    """
    needle = (query or "").strip().lower()
    artist_rows = list(artists)
    track_rows = list(tracks)

    scored: List[tuple] = []
    for artist in artist_rows:
        # Ties break on catalogue size: between two artists matched equally well,
        # the one with more of the library behind them is the more likely answer.
        scored.append((
            score_artist(artist.get("name", ""), needle),
            int(artist.get("track_count") or 0),
            {"kind": "artist", "artist": artist},
        ))
    for track_id, title, album in track_rows:
        # Negated so that, at equal score, a lower id sorts first — the library's
        # own order, which is the order every other list uses as its tiebreak.
        scored.append((
            score_track(title or "", album or "", needle),
            -int(track_id),
            {"kind": "track", "id": int(track_id), "title": title or "", "album": album or ""},
        ))

    # Sorted through a key rather than on the tuples themselves. Two artists can
    # share both a score and a track count, and comparing the tuples would fall
    # through to comparing the dicts behind them, which raises.
    scored.sort(key=lambda row: (-row[0], -row[1]))
    return [row[2] for row in scored]
