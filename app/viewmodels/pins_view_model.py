"""Cached sidebar state and pin actions shared by all GUI context menus."""
import logging

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot
from sqlalchemy.exc import SQLAlchemyError

from app.events.event_bus import EventBus
from app.repositories.sidebar_pin_repository import TARGETS, SidebarPinRepository
from app.services.playlist_service import PlaylistService

logger = logging.getLogger(__name__)


class PinsViewModel(QObject):
    changed = Signal()
    error = Signal(str)

    def __init__(self, repository: SidebarPinRepository, event_bus: EventBus,
                 playlist_service: PlaylistService | None = None, parent=None) -> None:
        super().__init__(parent)
        self._repo = repository
        self._playlists = playlist_service
        self._items: list[dict] = []
        self._keys: set[tuple[str, int]] = set()
        # A batch import or split can emit many events in one turn. Read once
        # when it settles; paint and menu visibility only consult cached data.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh)
        for kind in TARGETS:
            for action in ("added", "created", "updated", "deleted"):
                event_bus.subscribe(f"{kind}.{action}", self._schedule_refresh)
        for event in ("playlist.track_added", "playlist.track_removed", "playlist.reordered",
                      "playlist.cover_changed", "album.reordered"):
            event_bus.subscribe(event, self._schedule_refresh)
        self.refresh()

    def _schedule_refresh(self, *_args) -> None:
        if self._items:
            self._refresh_timer.start(0)

    @Property("QVariantList", notify=changed)
    def items(self) -> list[dict]:
        return self._items

    @Slot(str, int, result=bool)
    def isPinned(self, kind: str, content_id: int) -> bool:
        return (kind, content_id) in self._keys

    @Slot()
    def refresh(self) -> None:
        self._refresh_timer.stop()
        try:
            items = self._repo.summaries()
            playlist_ids = [r["contentId"] for r in items if r["contentType"] == "playlist"]
            if playlist_ids and self._playlists is not None:
                covers = {r["id"]: r["cover_hash"]
                          for r in self._playlists.get_summaries(playlist_ids)}
                for row in items:
                    if row["contentType"] == "playlist":
                        row["coverHash"] = covers.get(row["contentId"]) or ""
        except SQLAlchemyError:
            self._repo.session.rollback()
            logger.exception("Could not refresh sidebar pins")
            self.error.emit("Couldn’t refresh sidebar pins. Try again.")
            return
        if items != self._items:
            self._items = items
            self._keys = {(r["contentType"], r["contentId"]) for r in items}
            self.changed.emit()

    def _mutate(self, action: str, kind: str, content_id: int) -> bool:
        if kind not in TARGETS or content_id <= 0:
            return False
        try:
            changed = getattr(self._repo, action)(kind, content_id)
        except SQLAlchemyError:
            logger.exception("Could not %s sidebar content", action)
            self.error.emit("Couldn’t update sidebar pins. Try again.")
            return False
        self.refresh()
        return changed

    @Slot(str, int, result=bool)
    def pin(self, kind: str, content_id: int) -> bool:
        return self._mutate("pin", kind, content_id)

    @Slot(str, int, result=bool)
    def unpin(self, kind: str, content_id: int) -> bool:
        return self._mutate("unpin", kind, content_id)
