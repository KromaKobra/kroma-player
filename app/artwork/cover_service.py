"""Cover orchestration: source image / Opus file → deduplicated thumbnails.

This is the single entry point the rest of the app uses to populate the cache.
It deliberately knows nothing about Qt or the database — callers pass in bytes or
a file path and receive a stable ``cover_hash`` back, which they persist on the
``Track`` row.

Two ingest paths:
- ``ingest_source`` is used at download time, when we already hold the freshly
  fetched artwork bytes. It produces the canonical AVIF master (for embedding)
  *and* the cache thumbnails in one pass.
- ``ingest_opus`` is used by the backfill, reading artwork already embedded in an
  existing Opus file and producing only the thumbnails.
"""

from __future__ import annotations

from pathlib import Path

from app.artwork import LARGE_SIZE, THUMB_SIZE
from app.artwork import image_ops
from app.artwork.cover_store import CoverStore
from app.importer.tagging import extract_embedded_picture

_TIER_SIZES = (THUMB_SIZE, LARGE_SIZE)


class CoverService:
    """Generates and caches deduplicated cover thumbnails."""

    def __init__(self, store: CoverStore) -> None:
        self._store = store

    @property
    def store(self) -> CoverStore:
        """The blob store behind this service.

        Public because copying one cover onto a file is not an ingest and cannot
        go through the methods below: the picture already exists and already has
        a hash, and what the caller needs is to *read it back*. The file that
        carries it is the preferred source — see ``master_bytes`` in
        ``app/albums/unify.py`` — but when no file carries it yet, an album
        identified from an online catalogue being the ordinary case, the cache is the
        only source there is.

        Named without the underscore deliberately. Both callers reached for
        ``getattr(service, "store", None)`` and silently got ``None`` for as long
        as this did not exist, which cost them the fallback entirely and showed
        up as "that artwork could not be read back to copy it" on the one path
        that had no file to fall back *from*.
        """
        return self._store

    # ------------------------------------------------------------------
    # Ingest paths
    # ------------------------------------------------------------------

    def ingest_source(self, source_image: bytes) -> tuple[str, bytes]:
        """Process freshly downloaded artwork.

        Args:
            source_image: Raw bytes of any Pillow-readable image (JPEG/PNG/WebP).

        Returns:
            ``(cover_hash, master_avif_bytes)`` — the hash to store on the track
            and the canonical AVIF master to embed in the Opus file.
        """
        master = image_ops.make_master(source_image)
        cover_hash = image_ops.content_hash(master)
        self._ensure_tiers(cover_hash, master)
        return cover_hash, master

    def ingest_opus(self, opus_path: Path | str) -> str | None:
        """Process artwork already embedded in an Opus file (backfill path).

        Returns the cover hash, or None if the file has no embedded artwork.
        """
        picture = extract_embedded_picture(Path(opus_path))
        if not picture:
            return None
        cover_hash = image_ops.content_hash(picture)
        self._ensure_tiers(cover_hash, picture)
        return cover_hash

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_tiers(self, cover_hash: str, source: bytes) -> None:
        """Generate any missing thumbnail tiers for ``cover_hash``.

        Skips work entirely when the smallest tier already exists, which makes the
        dedup cheap: the second track of an album never re-encodes anything.
        """
        if self._store.has(cover_hash, THUMB_SIZE):
            return
        for size in _TIER_SIZES:
            if not self._store.has(cover_hash, size):
                self._store.put(cover_hash, size, image_ops.make_thumbnail(source, size))
