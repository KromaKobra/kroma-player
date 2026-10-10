"""Reading the ``LYRICS`` tag out of an Opus file.

Reading only. Kroma still *writes* this tag — see
``docs/architecture/decisions/0008-lyrics-are-mirrored-into-the-database.md`` —
but that write belongs to
:class:`~app.services.lyrics_service.LyricsService`, which does it after the
database row so the two cannot land in the wrong order. Putting a writer here
would be a second way to do it, reachable without the row.

Two callers, both in :mod:`app.lyrics.workers`' orbit: the one-off walk that
copies a whole library's tags into the table, and the lazy fallback that copies
a single track's the moment it is played. Neither is on the read path once a
track's row exists.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def read_lyrics_tag(path: str | Path) -> Optional[str]:
    """Return the ``LYRICS`` comment in the Opus file at *path*, or ``None``.

    Swallows everything. A file that is missing, unreadable, not Opus or simply
    untagged is a track without lyrics, which is an ordinary state — the sweep
    walks a whole library through here and must not stop on the first oddity it
    meets.
    """
    try:
        from mutagen.oggopus import OggOpus

        audio = OggOpus(str(path))
        if audio.tags:
            values = audio.tags.get("LYRICS")
            if values:
                return values[0]
    except Exception as exc:  # noqa: BLE001 — an unreadable file has no lyrics
        logger.debug("Could not read the LYRICS tag from %s: %s", path, exc)
    return None
