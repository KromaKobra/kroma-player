"""The picture a user's own audio file carries, as bytes.

One reader for two callers that used to disagree. The import list draws a local
file's cover through ``image://localart/`` (:mod:`app.artwork.local_art_provider`);
the stash files the track into the library. The first read every container
mutagen knows; the second read none of them and fetched from the network
instead — so a file that matched nothing online arrived in the library with no
cover, though the list had been showing it one all along.

Both read through :func:`read_embedded_art` now, so whatever the list shows is
what the library keeps.

Ogg is the case worth calling out. FLAC exposes ``.pictures``; an Ogg Opus or
Ogg Vorbis file does not — its picture is a base64 ``METADATA_BLOCK_PICTURE``
comment — and that is the container Kroma writes everything it stores in. A
reader that missed it would miss every file a previous Kroma export produced.
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Optional

import mutagen

logger = logging.getLogger(__name__)


def art_bytes_from(audio: object) -> Optional[bytes]:
    """The first embedded picture of an already-parsed mutagen file, or None."""
    # FLAC — mutagen exposes the picture blocks directly.
    pictures = getattr(audio, "pictures", None)
    if pictures:
        return pictures[0].data

    tags = getattr(audio, "tags", None)
    if tags is None:
        return None

    # ID3 (MP3) — APIC frames.
    try:
        from mutagen.id3 import ID3
        if isinstance(tags, ID3):
            for frame in tags.values():
                if getattr(frame, "FrameID", None) == "APIC":
                    return frame.data  # type: ignore[attr-defined]
            return None
    except ImportError:
        pass

    getter = tags.get if hasattr(tags, "get") else None
    if getter is None:
        return None

    # Ogg Opus / Ogg Vorbis — a FLAC picture block, base64, as a comment.
    blocks = getter("METADATA_BLOCK_PICTURE") or getter("metadata_block_picture")
    if blocks:
        try:
            from mutagen.flac import Picture

            picture = Picture(base64.b64decode(blocks[0]))
            if picture.data:
                return picture.data
        except Exception:  # noqa: BLE001 — a damaged block is no picture
            logger.debug("Unreadable METADATA_BLOCK_PICTURE", exc_info=True)

    # MP4 / M4A — the covr atom.
    covr = getter("covr")
    if covr:
        return bytes(covr[0])

    # APEv2 (WavPack, APE, some MP3).
    for key in ("Cover Art (Front)", "Cover Art (Back)"):
        item = getter(key)
        if item:
            data = bytes(item)
            null = data.find(b"\x00")
            return data[null + 1:] if null >= 0 else data

    return None


def read_embedded_art(path: Path | str) -> Optional[bytes]:
    """The picture embedded in the file at *path*, or None. Never raises."""
    try:
        audio = mutagen.File(path)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 — unreadable files are expected here
        logger.debug("Could not read %s: %s", path, exc)
        return None
    if audio is None:
        return None
    try:
        return art_bytes_from(audio)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not extract embedded art from %s: %s", path, exc)
        return None
