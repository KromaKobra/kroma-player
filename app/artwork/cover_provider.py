"""QQuickImageProvider that serves cover thumbnails to QML.

QML requests artwork with ``source: "image://cover/<hash>"``. Qt resolves that
URL by calling :meth:`CoverImageProvider.requestImage` with ``id == "<hash>"``.
Because the provider is registered with ``ForceAsynchronousImageLoading``, Qt
invokes it on a worker thread, so AVIF decoding never blocks the UI/render thread
and scrolling stays smooth.

Performance layering:
1. A bounded in-memory LRU of decoded ``QImage`` objects avoids re-hitting SQLite
   and re-decoding AVIF for rows that scroll back into view.
2. Qt's own ``QPixmapCache`` (via ``cache: true`` on the QML ``Image``) reuses
   decoded pixmaps across delegates that share the same cover URL — which, thanks
   to dedup, is every track on the same album.
3. ``sourceSize`` on the QML ``Image`` caps decode resolution per tier.

The LRU is bounded by **bytes, not by entries**, and that distinction is the
whole reason scrolling a long list used to stutter. A count-bounded cache only
helps if the count exceeds the number of distinct covers in the library; below
that it never converges, and a pass over the list evicts the front of itself
before it reaches the back. Measured on a 781-track library with 667 distinct
covers against the old 256-entry bound: roughly 60% of rows missed on *every*
sweep, permanently, each miss costing a SQLite read plus a ~0.9 ms Pillow decode.

That cost lands on the UI thread even though it is not run there.
``ForceAsynchronousImageLoading`` puts ``requestImage`` on a Qt worker thread,
but this is Python — it holds the GIL for the decode, and the thread it is
taking it from is the one running the list's ``data()`` calls and QML bindings.
A cache that cannot converge therefore reads as scroll lag, not as slow artwork.

A byte budget is also what makes the bound mean the same thing across tiers: a
``THUMB_SIZE`` cover decodes to 64 KB and a ``LARGE_SIZE`` one to 1 MB, so
sixteen of the latter cost what the entire thumbnail set of a mid-sized library
does.
"""

from __future__ import annotations

import io
import threading
from collections import OrderedDict

from PIL import Image
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider

from app.artwork import LARGE_SIZE, THUMB_SIZE
from app.artwork.cover_store import CoverStore
from app.artwork.image_ops import ensure_avif_support


# Decoded-image budget. Sized to hold a mid-sized library's entire thumbnail set
# outright — 667 distinct covers at 64 KB each is ~43 MB — so that a full scroll
# warms the cache once and never pays for it again. A larger library degrades to
# ordinary LRU behaviour instead of thrashing from the first row.
DEFAULT_LRU_BYTES = 48 * 1024 * 1024


def _decode_to_qimage(data: bytes) -> QImage | None:
    """Decode AVIF bytes into a QImage (copied so it owns its buffer)."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGBA")
            width, height = im.size
            qimg = QImage(im.tobytes("raw", "RGBA"), width, height, QImage.Format.Format_RGBA8888)
            # QImage does not copy the buffer; .copy() detaches it from the
            # soon-to-be-freed Python bytes.
            return qimg.copy()
    except Exception:
        return None


class CoverImageProvider(QQuickImageProvider):
    """Serves deduplicated AVIF cover thumbnails from the :class:`CoverStore`."""

    def __init__(self, store: CoverStore, lru_bytes: int = DEFAULT_LRU_BYTES) -> None:
        super().__init__(
            QQuickImageProvider.ImageType.Image,
            QQuickImageProvider.Flag.ForceAsynchronousImageLoading,
        )
        ensure_avif_support()
        self._store = store
        self._lru: "OrderedDict[tuple[str, int], QImage]" = OrderedDict()
        self._lru_bytes = max(1, lru_bytes)
        self._bytes = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # LRU helpers
    # ------------------------------------------------------------------

    def _cache_get(self, key: tuple[str, int]) -> QImage | None:
        with self._lock:
            img = self._lru.get(key)
            if img is not None:
                self._lru.move_to_end(key)
            return img

    def _cache_put(self, key: tuple[str, int], img: QImage) -> None:
        with self._lock:
            previous = self._lru.pop(key, None)
            if previous is not None:
                self._bytes -= previous.sizeInBytes()
            self._lru[key] = img
            self._bytes += img.sizeInBytes()
            # One entry always stays, however large: evicting the image just
            # decoded would mean the next request for it decodes it again, and
            # a cache that cannot hold its own most recent answer is worse than
            # no cache at all.
            while self._bytes > self._lru_bytes and len(self._lru) > 1:
                _, evicted = self._lru.popitem(last=False)
                self._bytes -= evicted.sizeInBytes()

    # ------------------------------------------------------------------
    # Tier selection
    # ------------------------------------------------------------------

    @staticmethod
    def _tier_for(requested_size) -> int:
        """Pick the stored tier that best matches the requested render size."""
        if requested_size is not None and requested_size.width() > THUMB_SIZE:
            return LARGE_SIZE
        return THUMB_SIZE

    # ------------------------------------------------------------------
    # QQuickImageProvider override
    # ------------------------------------------------------------------

    def requestImage(self, id: str, size, requestedSize):  # noqa: A002 (Qt signature)
        cover_hash = id
        tier = self._tier_for(requestedSize)
        key = (cover_hash, tier)

        img = self._cache_get(key)
        if img is None:
            data = (
                self._store.get(cover_hash, tier)
                or self._store.get(cover_hash, THUMB_SIZE)
                or self._store.get(cover_hash, LARGE_SIZE)
            )
            if data:
                img = _decode_to_qimage(data)
            if img is None or img.isNull():
                # 1x1 transparent fallback; QML shows its own placeholder when
                # the image status is not Ready, so this never flashes a broken
                # icon.
                #
                # Cached like any other answer, and deliberately: a hash whose
                # blob is missing is a *stable* fact, so without this the three
                # store lookups above run again every single time the row is
                # painted into view. Failing to find something is worth
                # remembering exactly as much as finding it is.
                img = QImage(1, 1, QImage.Format.Format_ARGB32)
                img.fill(0)
            self._cache_put(key, img)

        if size is not None:
            size.setWidth(img.width())
            size.setHeight(img.height())
        return img
