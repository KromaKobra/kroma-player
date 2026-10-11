"""Asynchronous, bounded cover-palette cache for GUI themes."""
from collections import OrderedDict

from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, Signal, Slot

from app.artwork import LARGE_SIZE, THUMB_SIZE
from app.artwork.cover_store import CoverStore
from app.artwork.image_ops import ensure_avif_support
from app.artwork.palette import extract_palette


class _Result(QObject):
    done = Signal(str, "QVariantMap")


class _Extract(QRunnable):
    def __init__(self, store: CoverStore, cover_hash: str, result: _Result):
        super().__init__()
        self.store, self.cover_hash, self.result = store, cover_hash, result

    def run(self):
        palette = {}
        try:
            data = (self.store.get(self.cover_hash, THUMB_SIZE)
                    or self.store.get(self.cover_hash, LARGE_SIZE))
            if data:
                palette = extract_palette(data)
        except Exception:
            # A damaged cache must not interfere with playback or the theme.
            pass
        self.result.done.emit(self.cover_hash, palette)


class ArtworkPaletteViewModel(QObject):
    paletteChanged = Signal()
    coverHashChanged = Signal()

    def __init__(self, store: CoverStore):
        super().__init__()
        ensure_avif_support()
        self._store = store
        self._hash = ""
        self._palette = {}
        self._cache = OrderedDict()
        self._pending = {}

    @Property("QVariantMap", notify=paletteChanged)
    def palette(self):
        return self._palette

    def _set_palette(self, palette):
        if self._palette != palette:
            self._palette = palette
            self.paletteChanged.emit()

    def _get_hash(self):
        return self._hash

    def _set_hash(self, cover_hash):
        if self._hash == cover_hash:
            return
        self._hash = cover_hash
        self.coverHashChanged.emit()
        self._set_palette(self._cache.get(cover_hash, {}))
        if cover_hash in self._cache:
            self._cache.move_to_end(cover_hash)
        elif cover_hash and cover_hash not in self._pending:
            result = _Result(self)
            result.done.connect(self._finished)
            self._pending[cover_hash] = result
            QThreadPool.globalInstance().start(_Extract(self._store, cover_hash, result))

    coverHash = Property(str, _get_hash, _set_hash, notify=coverHashChanged)

    @Slot(str, "QVariantMap")
    def _finished(self, cover_hash, palette):
        self._pending.pop(cover_hash).deleteLater()
        # Don't cache misses: a cover backfill may make this hash available later.
        if palette:
            self._cache[cover_hash] = palette
            self._cache.move_to_end(cover_hash)
            while len(self._cache) > 128:
                self._cache.popitem(last=False)
        # A previous track's late result must never recolour the current one.
        if cover_hash == self._hash:
            self._set_palette(palette)
