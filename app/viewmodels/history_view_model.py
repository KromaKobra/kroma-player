"""History ViewModel — play history as a track list model.

Newest first: row 0 is the most recently played. Rows cannot be reordered or
removed by hand; the list changes only as a consequence of playback, or when a
track leaves the library entirely.

Selection is kept by ``QueueManager`` rather than here, because navigation
moves rows between the queue and history and has to drop their selection on the
way — see ``app.library.selection``.
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QModelIndex

from app.events.event_bus import EventBus
from app.library.list_model import TrackListModel
from app.library.row_cache import TrackRowCache
from app.library.selection import QueueManagerSelection
from app.queue.queue_manager import QueueManager

logger = logging.getLogger(__name__)


class HistoryViewModel(TrackListModel):
    """Exposes QueueManager's history to QML."""

    def __init__(
        self,
        queue_manager: QueueManager,
        event_bus: EventBus,
        row_cache: Optional[TrackRowCache] = None,
        parent=None,
    ) -> None:
        super().__init__(row_cache, QueueManagerSelection(queue_manager, history=True), parent)
        self._mgr = queue_manager
        event_bus.subscribe("track.deleted", self._on_track_deleted)
        self._pull()

    def _pull(self) -> None:
        entries = self._mgr.history_entries()
        self._set_vectors(
            [e.get("id") for e in entries],
            [e.get("uid", -1) for e in entries],
        )

    # ------------------------------------------------------------------
    # Called by PlayerViewModel after a QueueMutation
    # ------------------------------------------------------------------

    def notify_prepended(self) -> None:
        """A track was inserted at index 0 (advance)."""
        self.beginInsertRows(QModelIndex(), 0, 0)
        self._pull()
        self.endInsertRows()

    def notify_top_removed(self) -> None:
        """Index 0 was popped — the Previous button pulled it back into the queue."""
        self.beginRemoveRows(QModelIndex(), 0, 0)
        self._pull()
        self.endRemoveRows()

    def notify_reset(self) -> None:
        """Several rows changed at once, or the cap moved."""
        self.beginResetModel()
        self._pull()
        self.endResetModel()

    # ------------------------------------------------------------------
    # Event bus handlers
    # ------------------------------------------------------------------

    def _on_track_deleted(self, track_id: int) -> None:
        """Drop every history row for a track that has left the library.

        History can hold the same track more than once, so this removes all of
        them rather than stopping at the first.
        """
        self._cache.invalidate(track_id)
        while True:
            entries = self._mgr.history_entries()
            row = next((i for i, e in enumerate(entries) if e.get("id") == track_id), -1)
            if row < 0:
                return
            self.beginRemoveRows(QModelIndex(), row, row)
            self._mgr.remove_from_history(row)
            self._pull()
            self.endRemoveRows()
            self.refresh_selection()
