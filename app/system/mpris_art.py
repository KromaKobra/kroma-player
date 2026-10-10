"""Expose cached cover art as image files for ``mpris:artUrl``.

Kroma does not keep covers as files. ``app.artwork.cover_store`` holds them as
AVIF blobs in a SQLite table keyed by ``(cover_hash, size)`` and serves them to
QML through the ``image://cover/<hash>`` provider — which is exactly the wrong
shape for MPRIS, where ``mpris:artUrl`` must be a URL some other process can
open. AVIF is also a poor bet across arbitrary MPRIS consumers, so the export
is PNG.

The export is a cache of a cache: content-addressed like the store it reads
from, so a hash is decoded and written once no matter how many tracks share
that album cover, and the whole directory can be deleted at any time.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from urllib.parse import quote

from app.artwork import LARGE_SIZE

logger = logging.getLogger(__name__)

# Subdirectory of the vault's .data/ folder. Lives beside covers.db rather than
# in XDG cache so the whole derived-cache story stays in one place, per the
# contract in app/artwork/__init__.py.
CACHE_DIR_NAME = "mpris-art"


def art_url(path: Path | None) -> str | None:
    """Return a ``file://`` URL for a path, percent-encoding as needed.

    Vault paths are user-chosen and routinely contain spaces, so the URL cannot
    be built by string concatenation.
    """
    if path is None:
        return None
    return "file://" + quote(str(path))


def export_cover(store, cover_hash: str | None, cache_dir: Path) -> Path | None:
    """Write this cover to a PNG in ``cache_dir`` and return its path.

    Returns the existing file without re-decoding when the hash has already been
    exported, and ``None`` whenever the art cannot be produced — an absent hash,
    a cache miss, an undecodable blob. Callers omit ``mpris:artUrl`` in that
    case rather than pointing clients at a file that is not there.

    Artwork failures must never interrupt playback, so every error here is
    swallowed and logged at debug: a missing cover costs the user a thumbnail,
    and nothing else.

    Args:
        store: A :class:`~app.artwork.cover_store.CoverStore`.
        cover_hash: The track's content hash, or ``None``.
        cache_dir: Directory to write into; created if absent.
    """
    if not cover_hash:
        return None

    target = cache_dir / f"{cover_hash}.png"
    if target.exists():
        return target

    try:
        data = store.get(cover_hash, LARGE_SIZE)
        if data is None:
            return None

        # Imported lazily: Pillow pulls in its AVIF plugin on first use, and
        # this module is imported during startup wiring where that cost has no
        # reason to be paid until a track actually plays.
        from PIL import Image

        cache_dir.mkdir(parents=True, exist_ok=True)
        with Image.open(io.BytesIO(data)) as im:
            im.convert("RGB").save(target, format="PNG")
        return target
    except Exception:
        logger.debug("Could not export cover %s for MPRIS", cover_hash, exc_info=True)
        # A half-written file would be served forever by the exists() check above.
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        return None
