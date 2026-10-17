"""Pure image operations for cover artwork.

This module is intentionally free of Qt, SQLAlchemy, and app-wide imports so it
stays trivially testable and reusable. Everything here works on raw ``bytes`` and
returns raw ``bytes`` (AVIF) or hex strings (hashes).

AVIF is used for every encoded artifact (both the master embedded into the Opus
file and the cached thumbnails). AVIF gives markedly smaller files than JPEG at
the same perceptual quality, which keeps the cache tiny and portable.
"""

from __future__ import annotations

import hashlib
import io

from PIL import Image

# ----------------------------------------------------------------------------
# Encoder tuning. `quality` is 0-100 (higher = better/larger). `speed` is 0-10
# (higher = faster encode / slightly larger).
#
# `speed` is an encoder effort dial, not a quality dial: it trades CPU for
# compression efficiency, so lowering it buys smaller files at the same visual
# quality rather than better-looking ones. The master sat at 4 and cost ~2.1s
# per import, which was a third of the time to stash a track. At 6 the same
# image encodes in ~0.7s and lands 1.7% larger — worth it on a path that runs
# once per imported song. `quality` is what governs how the cover actually
# looks, and it is unchanged.
# ----------------------------------------------------------------------------
THUMB_QUALITY = 60
LARGE_QUALITY = 72
MASTER_QUALITY = 82
_THUMB_SPEED = 6
_MASTER_SPEED = 6

_avif_ready = False


def ensure_avif_support() -> None:
    """Verify Pillow can read and write AVIF; raise a clear error otherwise.

    Pillow >= 12 bundles libavif in its binary wheels and auto-registers the
    plugin. As a fallback we try the external ``pillow-avif-plugin``. Called once
    lazily before the first encode/decode.
    """
    global _avif_ready
    if _avif_ready:
        return
    try:
        from PIL import AvifImagePlugin  # noqa: F401  (registers AVIF on import)
    except Exception:
        try:
            import pillow_avif  # noqa: F401  (optional external fallback)
        except Exception as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "AVIF support is unavailable. Install Pillow>=12 (bundles libavif) "
                "or the 'pillow-avif-plugin' package."
            ) from exc
    _avif_ready = True


def content_hash(data: bytes) -> str:
    """Return a short, stable content address for image bytes.

    BLAKE2b at 16 bytes (32 hex chars) is fast and collision-safe for this scale.
    Identical source artwork → identical hash → shared thumbnail (dedup).
    """
    return hashlib.blake2b(data, digest_size=16).hexdigest()


def _center_square(im: Image.Image) -> Image.Image:
    """Crop ``im`` to a centered square (no scaling)."""
    width, height = im.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    return im.crop((left, top, left + side, top + side))


def to_square_avif(source: bytes, max_size: int, quality: int, *, speed: int = _THUMB_SPEED) -> bytes:
    """Center-crop to square, downscale to at most ``max_size``, encode AVIF.

    Accepts any Pillow-readable input (JPEG/PNG/WebP/AVIF). Never upscales: if the
    source square is smaller than ``max_size`` it is kept at its native size, so we
    never invent detail or waste bytes.

    Args:
        source: Raw bytes of the source image.
        max_size: Maximum side length of the output square in pixels.
        quality: AVIF quality (0-100).
        speed: AVIF encoder speed (0-10).

    Returns:
        AVIF-encoded bytes of the square cover.
    """
    ensure_avif_support()
    with Image.open(io.BytesIO(source)) as im:
        im = im.convert("RGB")
        im = _center_square(im)
        if im.width > max_size:
            im = im.resize((max_size, max_size), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        im.save(buffer, format="AVIF", quality=quality, speed=speed)
        return buffer.getvalue()


def make_master(source: bytes) -> bytes:
    """Encode the canonical master cover (embedded into the Opus file)."""
    from app.artwork import ORIGINAL_SIZE

    return to_square_avif(source, ORIGINAL_SIZE, MASTER_QUALITY, speed=_MASTER_SPEED)


def make_thumbnail(source: bytes, size: int) -> bytes:
    """Encode a cache thumbnail at the given tier size."""
    from app.artwork import LARGE_SIZE

    quality = LARGE_QUALITY if size >= LARGE_SIZE else THUMB_QUALITY
    return to_square_avif(source, size, quality, speed=_THUMB_SPEED)


def make_playlist_covers(sources: list[bytes | None], sizes: tuple[int, ...]) -> dict[int, bytes]:
    """Compose four center-cropped tiles in reading order into AVIF tiers.

    Use the existing in-process encoder instead of starting an ffmpeg process
    for each cover. Empty tiles retain their position and use a neutral fill.
    """
    from app.artwork import LARGE_SIZE

    if len(sources) != 4 or not sizes or any(size <= 0 or size % 2 for size in sizes):
        raise ValueError("Playlist covers require four sources and an even size")
    ensure_avif_support()
    size = max(sizes)
    canvas = Image.new("RGB", (size, size), (40, 40, 44))
    half = size // 2
    for index, source in enumerate(sources):
        if source is None:
            continue
        with Image.open(io.BytesIO(source)) as image:
            tile = _center_square(image.convert("RGB"))
            tile = tile.resize((half, half), Image.Resampling.LANCZOS)
            canvas.paste(tile, ((index % 2) * half, (index // 2) * half))
    # Decode/crop each source once, then downsample the finished canvas for lists.
    tiers = {}
    for tier in sizes:
        image = canvas if tier == size else canvas.resize((tier, tier), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        quality = LARGE_QUALITY if tier >= LARGE_SIZE else THUMB_QUALITY
        image.save(buffer, format="AVIF", quality=quality, speed=_THUMB_SPEED, max_threads=2)
        tiers[tier] = buffer.getvalue()
    return tiers
