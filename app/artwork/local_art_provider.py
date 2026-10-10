"""QQuickImageProvider that extracts embedded album art from local audio files.

QML requests art with ``source: "image://localart/<forward-slash-path>"``.
The path may use forward slashes on all platforms (Windows paths are converted
before being stored in the model).  Mutagen reads the embedded picture and
Pillow decodes it into a QImage; any failure silently returns a 1x1 transparent
pixel so the QML placeholder is shown instead.
"""

from __future__ import annotations

import io
import logging
import threading
from collections import OrderedDict
from pathlib import Path

import mutagen
from PIL import Image
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider

from app.artwork import THUMB_SIZE
from app.artwork.embedded import art_bytes_from

logger = logging.getLogger(__name__)

# Extraction is expensive — a mutagen parse plus a Pillow decode per request —
# and scrolling an import list asks for the same rows over and over.
#
# Bounded by bytes rather than by entries, for the same reason
# ``CoverImageProvider`` is: an entry count says nothing about what the cache
# costs. It is starker here than there, because nothing caps the size of art
# embedded in a user's own file — a 1400x1400 cover decodes to 7.8 MB, so a
# hundred-odd of them is most of a gigabyte held for a preview list. Requests
# are served downscaled to what was asked for, which is what keeps entries near
# the 64 KB a list row actually needs.
DEFAULT_CACHE_BYTES = 16 * 1024 * 1024


class LocalArtImageProvider(QQuickImageProvider):
    """Serves embedded album art extracted on-demand from local audio files."""

    def __init__(self, cache_bytes: int = DEFAULT_CACHE_BYTES) -> None:
        super().__init__(
            QQuickImageProvider.ImageType.Image,
            QQuickImageProvider.Flag.ForceAsynchronousImageLoading,
        )
        # Qt calls requestImage on its own worker threads, so the cache needs
        # a lock — the same arrangement CoverImageProvider uses.
        self._lock = threading.Lock()
        self._cache: "OrderedDict[tuple[str, int], QImage]" = OrderedDict()
        self._cache_bytes = max(1, cache_bytes)
        self._bytes = 0

    def requestImage(self, id: str, size, requestedSize):  # noqa: A002 (Qt signature)
        edge = self._edge_for(requestedSize)
        key = (id, edge)
        img = self._cached(key)
        if img is None:
            img = self._extract(Path(id), edge)
            if img is None or img.isNull():
                # Cached like any other answer: a file with no embedded picture
                # will still have none next time, and re-parsing it with mutagen
                # to rediscover that is the most expensive way to learn nothing.
                img = QImage(1, 1, QImage.Format.Format_ARGB32)
                img.fill(0)
            self._store(key, img)
        if size is not None:
            size.setWidth(img.width())
            size.setHeight(img.height())
        return img

    @staticmethod
    def _edge_for(requested_size) -> int:
        """Longest edge to decode down to, in pixels.

        Keyed into the cache alongside the path, so a list row asking for 128px
        and a detail panel asking for 512 do not evict each other by taking
        turns overwriting one entry.
        """
        if requested_size is None:
            return THUMB_SIZE
        edge = max(requested_size.width(), requested_size.height())
        return edge if edge > 0 else THUMB_SIZE

    def _cached(self, key: tuple[str, int]) -> QImage | None:
        with self._lock:
            img = self._cache.get(key)
            if img is not None:
                self._cache.move_to_end(key)
            return img

    def _store(self, key: tuple[str, int], img: QImage) -> None:
        with self._lock:
            previous = self._cache.pop(key, None)
            if previous is not None:
                self._bytes -= previous.sizeInBytes()
            self._cache[key] = img
            self._bytes += img.sizeInBytes()
            # Always keep the entry just added, however large — see
            # CoverImageProvider._cache_put.
            while self._bytes > self._cache_bytes and len(self._cache) > 1:
                _, evicted = self._cache.popitem(last=False)
                self._bytes -= evicted.sizeInBytes()

    def _extract(self, path: Path, edge: int = THUMB_SIZE) -> QImage | None:
        try:
            audio = mutagen.File(path)  # type: ignore[attr-defined]
            if audio is None:
                return None
            art_bytes = self._get_art_bytes(audio)
            if not art_bytes:
                return None
            with Image.open(io.BytesIO(art_bytes)) as im:
                # Downscale before converting, not after: `thumbnail` is a no-op
                # on art already smaller than the request, and on art that is
                # larger it means the RGBA buffer below is built at display size
                # rather than at whatever resolution the user's file happened to
                # embed. That buffer is what the cache then holds.
                im.thumbnail((edge, edge), Image.Resampling.LANCZOS)
                im = im.convert("RGBA")
                w, h = im.size
                qimg = QImage(im.tobytes("raw", "RGBA"), w, h, QImage.Format.Format_RGBA8888)
                return qimg.copy()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not extract embedded art from %s: %s", path, exc)
            return None

    def _get_art_bytes(self, audio: object) -> bytes | None:
        # The same reader the stash uses, so what this list shows is what the
        # library keeps — see `app.artwork.embedded`.
        return art_bytes_from(audio)
