"""Lazy, coalesced playlist artwork generation outside the UI thread.

Only ordered source hashes determine the cache key; titles, tail edits and track
IDs do not. Identical arrangements share both tiers across playlists and restarts.
The Qt thread owns repository access and publication. Workers only read cover
blobs and encode images, so a late result cannot overwrite a newer track order.
"""
from __future__ import annotations

import json
import logging

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot

from app.artwork import LARGE_SIZE, THUMB_SIZE
from app.artwork import image_ops
from app.artwork.cover_store import CoverStore
from app.events.event_bus import EventBus
from app.repositories.playlist_repository import PlaylistRepository

logger = logging.getLogger(__name__)
_SIZES = (THUMB_SIZE, LARGE_SIZE)


def playlist_cover_hash(sources: tuple[str | None, ...]) -> str:
    # Version the recipe so future crop/encoder changes can invalidate old art.
    return image_ops.content_hash(json.dumps(
        ["playlist-grid-v1", sources], separators=(",", ":"),
    ).encode("utf-8"))


class _Result(QObject):
    done = Signal(object, object)


class _Generate(QRunnable):
    def __init__(self, store: CoverStore, sources: tuple, result: _Result):
        super().__init__()
        self.store, self.sources, self.result = store, sources, result

    def run(self) -> None:
        try:
            images = []
            loaded = {}
            for cover_hash in self.sources:
                image = None
                if cover_hash:
                    if cover_hash not in loaded:
                        loaded[cover_hash] = (self.store.get(cover_hash, LARGE_SIZE)
                                              or self.store.get(cover_hash, THUMB_SIZE))
                    image = loaded[cover_hash]
                    if image is None:
                        # Do not persist an incomplete recipe while its source
                        # thumbnails are still being ingested/backfilled.
                        raise ValueError(f"Source artwork {cover_hash} is not cached")
                images.append(image)
            tiers = image_ops.make_playlist_covers(images, _SIZES)
        except Exception:
            logger.warning("Could not generate playlist artwork", exc_info=True)
            tiers = None
        self.result.done.emit(self.sources, tiers)


class PlaylistArtwork(QObject):
    """Resolve immediately, then publish replacement art after encoding finishes."""

    def __init__(self, store: CoverStore, repo: PlaylistRepository, events: EventBus):
        super().__init__()
        self._store, self._repo, self._events = store, repo, events
        self._pending: dict[int, tuple] = {}
        self._active = False
        self._result = _Result(self)
        self._result.done.connect(self._finished)
        # One bounded worker leaves CPU available for playback and scrolling.
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._start)
        self._closed = False

    def resolve(self, playlist_id: int, sources: list, own_cover: str | None) -> str | None:
        if self._closed:
            return own_cover or (sources[0] if sources else None)
        if own_cover or len(sources) < 4:
            self.release(playlist_id)
            return own_cover or (sources[0] if sources else None)
        sources = tuple(sources)
        wanted = playlist_cover_hash(sources)
        previous = self._store.playlist_cover(playlist_id)
        if all(self._store.has(wanted, size) for size in _SIZES):
            self._pending.pop(playlist_id, None)
            if previous != wanted:
                self._store.set_playlist_cover(playlist_id, wanted, {})
                self._retire(previous)
            return wanted
        # Repeated reads and rapid edits replace one pending request per playlist.
        self._pending[playlist_id] = sources
        if not self._active:
            self._timer.start(0)
        # Keep the existing image until its replacement is completely available.
        return previous or sources[0]

    def release(self, playlist_id: int) -> None:
        self._pending.pop(playlist_id, None)
        previous = self._store.playlist_cover(playlist_id)
        if previous:
            self._store.remove_playlist_cover(playlist_id)
            self._retire(previous)

    def shutdown(self) -> None:
        self._closed = True
        self._timer.stop()
        self._pending.clear()
        self._pool.waitForDone()

    def _retire(self, cover_hash: str | None) -> None:
        if cover_hash and not self._repo.cover_is_referenced(cover_hash):
            self._store.delete_unreferenced_playlist_cover(cover_hash)

    @Slot()
    def _start(self) -> None:
        if self._active or not self._pending:
            return
        sources = next(iter(self._pending.values()))
        self._active = True
        self._pool.start(_Generate(self._store, sources, self._result))

    @Slot(object, object)
    def _finished(self, sources: tuple, tiers: dict | None) -> None:
        self._active = False
        if self._closed:
            return
        wanted = playlist_cover_hash(sources)
        changed = []
        for playlist_id, pending in list(self._pending.items()):
            if pending != sources:
                continue
            self._pending.pop(playlist_id)
            playlist = self._repo.get_by_id(playlist_id)
            current = self._repo.get_cover_sources(playlist_id) if playlist else []
            if not playlist or playlist.cover_hash or tuple(current) != sources:
                # A reorder, deletion or source-art edit may have happened while
                # encoding, even if no view has requested the new cover yet.
                self.resolve(playlist_id, current, playlist.cover_hash if playlist else None)
                continue
            if tiers is not None:
                previous = self._store.playlist_cover(playlist_id)
                self._store.set_playlist_cover(playlist_id, wanted, tiers)
                self._retire(previous)
                changed.append(playlist_id)
        # Publish only after storage is ready, so providers never cache a miss.
        for playlist_id in changed:
            self._events.emit("playlist.cover_changed", playlist_id)
        if self._pending:
            self._timer.start(0)
