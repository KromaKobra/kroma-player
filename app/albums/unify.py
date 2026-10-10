"""The pure core of album-artwork unification: the plan, and one file's rewrite.

No Qt and no SQLAlchemy here — see the package docstring. What this module knows
is how to turn "keep this cover, on these tracks" into a frozen plan, and how to
apply that plan to one file. What it deliberately does not know is where the
track ids came from, which of them the user ticked, or what to write back to the
library afterwards.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

from app.importer.tagging import (
    build_picture_block_from_bytes,
    extract_embedded_picture,
    update_opus_tags,
)

logger = logging.getLogger(__name__)


class UnifyError(Exception):
    """Unification could not be carried out."""


class UnifyCancelled(Exception):
    """Unification stopped early at the user's request."""


@dataclass(frozen=True)
class UnifyPlan:
    """Exactly what a unify run will do, decided before any of it happens.

    Frozen, and built on the UI thread from data the user has just looked at, so
    the run cannot drift from what was confirmed. ``picture_block`` in
    particular is resolved *here* rather than per track: every file is getting
    the same picture, and re-deriving it inside the loop would mean the fifth
    file could receive something the first did not.

    Attributes:
        album_id: The album being repaired. Carried so the caller can write the
            result back without holding state of its own across the thread hop.
        album_name: For the progress message, and for the ``ALBUM`` tag on any
            file whose tag disagrees with the library.
        cover_hash: The content address every listed track will end up carrying.
        picture_block: The base64 ``METADATA_BLOCK_PICTURE`` payload to embed.
        track_paths: ``(track_id, path)`` for each file to rewrite, in order.
    """

    album_id: int
    album_name: str
    cover_hash: str
    picture_block: str
    track_paths: Sequence[tuple[int, Path]] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        return len(self.track_paths)


def master_bytes(
    keeper_path: Optional[Path],
    cover_hash: str,
    read_cached: Optional[Callable[[str], Optional[bytes]]],
) -> bytes:
    """The picture to embed, preferring the file that already has it.

    Public because unify is not the only thing that copies one cover onto a
    track: promoting an album to primary in the track editor does it too, for one
    file rather than many, and the order of preference below must be the same
    both times or the two would embed different pictures for the same hash.

    Two sources, in order:

    1. The embedded picture of a track already carrying this cover. That is the
       original the library was built from, at whatever resolution it arrived —
       ``app/artwork/__init__.py`` is explicit that the file is the source of
       truth and ``covers.db`` is the cache in front of it.
    2. The cache, when there is no such file — which happens when the cover came
       from an online catalogue and was never embedded in anything.

    Falling back the other way round would quietly downgrade every unify to the
    cache's largest tier.
    """
    if keeper_path is not None:
        try:
            data = extract_embedded_picture(keeper_path)
            if data:
                return data
        except Exception:  # noqa: BLE001 — a missing or unreadable file is not fatal
            logger.debug("Could not read embedded art from %s", keeper_path, exc_info=True)

    if read_cached is not None:
        data = read_cached(cover_hash)
        if data:
            return data

    raise UnifyError("That artwork could not be read back to copy it")


def build_plan(
    *,
    album_id: int,
    album_name: str,
    cover_hash: str,
    track_paths: Sequence[tuple[int, Path]],
    keeper_path: Optional[Path] = None,
    read_cached: Optional[Callable[[str], Optional[bytes]]] = None,
) -> UnifyPlan:
    """Freeze a unify run. Raises :class:`UnifyError` if it cannot be described.

    Raising rather than returning an empty plan when the artwork cannot be read:
    a run that silently rewrote nothing would report success and leave the album
    exactly as broken as it was.
    """
    if not cover_hash:
        raise UnifyError("Choose which artwork to keep")

    data = master_bytes(keeper_path, cover_hash, read_cached)
    return UnifyPlan(
        album_id=int(album_id),
        album_name=album_name or "",
        cover_hash=cover_hash,
        picture_block=build_picture_block_from_bytes(data),
        track_paths=tuple((int(t), Path(p)) for t, p in track_paths),
    )


def rewrite_track(plan: UnifyPlan, path: Path) -> None:
    """Embed the plan's picture into one Opus file.

    The ``ALBUM`` tag goes in alongside it. A track being unified onto this
    album's cover is a track this album claims, and a file whose tag still names
    something else would be re-read as the other album on the next import.
    """
    update_opus_tags(path, {"ALBUM": plan.album_name}, plan.picture_block)
