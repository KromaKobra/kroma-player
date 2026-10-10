"""Searching the network for a track's lyrics.

Kept in one place so the three paths that want lyrics for a track — a fresh
fetch from an audio source, a metadata refresh, and the mic button in Now
Playing — cannot drift from one another. Whatever a fetch would have embedded
is what a refresh writes and what the button finds. Lyrics are the core's: no
plugin is asked for them.

No Qt and no database, so it can be called straight from a worker thread or
from a test with :func:`lookup_lyrics` stubbed.
"""
from __future__ import annotations

import logging
from typing import Optional

import syncedlyrics

logger = logging.getLogger(__name__)


def lookup_lyrics(title: str, artist: str) -> Optional[str]:
    """Search for lyrics matching *title* and *artist*. ``None`` if there are none.

    *artist* may be the comma-separated list a track carries; only the lead
    artist is used, because a query naming four collaborators matches nothing.

    Never raises. ``syncedlyrics.search()`` reaches several providers over the
    network and any of them can fail in its own way, and on the fetch path an
    exception here would take a fetch that had otherwise succeeded down with
    it. A failed lookup is "no lyrics", which is the outcome
    ``docs/architecture/cross-cutting/degradation.md`` records for this feature:
    the track keeps everything else it has.
    """
    first_artist = (artist or "").split(",")[0].strip()
    query = f"{title or ''} {first_artist}".strip()
    if not query:
        return None
    try:
        return syncedlyrics.search(query)
    except Exception as exc:  # noqa: BLE001 — a lookup that fails found nothing
        logger.warning("Lyric lookup failed for %r: %s", query, exc)
        return None
