"""Album-art subsystem for Kroma Player.

This package owns everything related to cover artwork:

- ``image_ops``      — pure Pillow helpers (square-crop, downscale, AVIF encode, hashing).
- ``cover_store``    — content-addressed SQLite blob cache for small AVIF thumbnails.
- ``cover_service``  — orchestration: turn a source image (or an Opus file) into
                       deduplicated thumbnails and return a stable ``cover_hash``.
- ``cover_provider`` — Qt ``QQuickImageProvider`` that serves thumbnails to QML.
- ``playlist_artwork`` — background four-track grids, shared and cached in covers.db.
- ``local_art_provider`` — the same for art embedded in files being imported,
                       which are not in the library yet and so have no hash.

Design contract
---------------
* For **album art**, the source of truth is the artwork embedded in each Opus
  file (``METADATA_BLOCK_PICTURE``). That part of this package is a *derived,
  disposable cache* that can be deleted and rebuilt at any time.
* For an **artist portrait** there is no file to rebuild from — an artist is not
  a recording and has nothing on disk carrying its picture. What stands in for
  one is the artist's external id in ``tracks.db``, which is durable, and which
  the artist editor can re-fetch the portrait from on demand. So the cache is still
  rebuildable, but for portraits it takes a network round trip rather than a
  local read, and a portrait set for an artist with no external id is the one
  thing here that deleting ``covers.db`` genuinely loses.
* Thumbnails are keyed by the content hash of their source bytes, so tracks that
  share an album cover share a single stored thumbnail (dedup). Portraits live in
  the same table under the same rule, which means an artist whose picture *is*
  one of their album covers shares that row — and is why deciding a hash is dead
  means asking every table that names one. See
  ``TrackService._referenced_cover_hashes``.
  Generated playlist grids also have references inside ``covers.db``; the store
  preserves these during a sweep and retires them when the last playlist changes.
* Sizes are fixed tiers (``THUMB_SIZE`` for list rows, ``LARGE_SIZE`` for the
  now-playing panel). The full-resolution master is never cached here.
"""

from __future__ import annotations

# Fixed thumbnail tiers (px, longest edge — covers are square).
THUMB_SIZE = 128   # list-row delegates (renders ~40px, 2x for hi-DPI)
LARGE_SIZE = 512   # now-playing panel
ORIGINAL_SIZE = 800  # canonical master embedded into the Opus file

__all__ = ["THUMB_SIZE", "LARGE_SIZE", "ORIGINAL_SIZE"]
