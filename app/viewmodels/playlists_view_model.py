"""Playlists ViewModel — list model of all playlists for the Playlist tab grid.

Exposes the playlist collection to QML as a QAbstractListModel (one row per
playlist) and acts as the single QML-facing entry point for playlist operations
that originate anywhere in the app:

- The Playlist tab binds this as its grid model and calls createPlaylist /
  deletePlaylist / renamePlaylist.
- The queue / history / search / import context menus call playlistList() to
  populate their "Add to playlist" submenu, then addTracksToPlaylist().

The model fully resets itself whenever any ``playlist.*`` event fires (playlists are
few, so a reset is cheap and keeps derived counts/covers correct).
"""
from __future__ import annotations

from enum import IntEnum
from typing import List

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    Qt,
    Signal,
    Slot,
)

from app.services.playlist_service import PlaylistService
from app.events.event_bus import EventBus


class PlaylistRoles(IntEnum):
    PlaylistId = Qt.ItemDataRole.UserRole + 1
    Name       = Qt.ItemDataRole.UserRole + 2
    Description = Qt.ItemDataRole.UserRole + 3
    CoverHash  = Qt.ItemDataRole.UserRole + 4
    TrackCount = Qt.ItemDataRole.UserRole + 5
    TotalDuration = Qt.ItemDataRole.UserRole + 6


_ROLE_NAMES: dict[int, bytes] = {
    PlaylistRoles.PlaylistId:    b"playlistId",
    PlaylistRoles.Name:          b"name",
    PlaylistRoles.Description:   b"description",
    PlaylistRoles.CoverHash:     b"coverHash",
    PlaylistRoles.TrackCount:    b"trackCount",
    PlaylistRoles.TotalDuration: b"totalDuration",
}


class PlaylistsViewModel(QAbstractListModel):
    """List model exposing every playlist (newest first) plus mutation slots."""

    # Emitted right after a playlist is created so the UI can open it immediately.
    playlist_created = Signal(int)   # playlist_id
    counts_changed   = Signal()      # any add/remove/create/delete (for empty-state)

    def __init__(self, playlist_service: PlaylistService, event_bus: EventBus,
                 parent=None) -> None:
        super().__init__(parent)
        self._service = playlist_service
        self._rows: List[dict] = []

        for evt in (
            "playlist.created", "playlist.deleted", "playlist.updated",
            "playlist.track_added", "playlist.track_removed",
            # A playlist's cover is whatever sits at position 0, so rearranging
            # one changes how it looks in the grid without its membership moving.
            "playlist.reordered",
            # A deleted track cascades out of playlist_tracks at the DB level, so
            # the cached counts/covers must be refreshed too.
            "track.deleted",
        ):
            event_bus.subscribe(evt, self._on_playlists_changed)

        self._reload()

    # ------------------------------------------------------------------
    # QAbstractListModel overrides
    # ------------------------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == PlaylistRoles.PlaylistId:
            return row["id"]
        if role == PlaylistRoles.Name:
            return row["name"]
        if role == PlaylistRoles.Description:
            return row["description"]
        if role == PlaylistRoles.CoverHash:
            return row["cover_hash"]
        if role == PlaylistRoles.TrackCount:
            return row["track_count"]
        if role == PlaylistRoles.TotalDuration:
            return row["total_duration"]
        return None

    def roleNames(self) -> dict[int, bytes]:
        return _ROLE_NAMES

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reload(self) -> None:
        self._rows = self._service.get_summaries()

    def _on_playlists_changed(self, *_args) -> None:
        """Refresh the whole model on any playlist mutation (cheap; few playlists)."""
        self.beginResetModel()
        self._reload()
        self.endResetModel()
        self.counts_changed.emit()

    # ------------------------------------------------------------------
    # Slots called from QML
    # ------------------------------------------------------------------

    @Slot(str, result=int)
    def createPlaylist(self, name: str) -> int:
        """Create a playlist and return its new id (-1 on empty name)."""
        if not name or not name.strip():
            return -1
        playlist_id = self._service.create_playlist(name)
        # Reset already happened via the playlist.created event subscription.
        self.playlist_created.emit(playlist_id)
        return playlist_id

    @Slot(int)
    def deletePlaylist(self, playlist_id: int) -> None:
        self._service.delete_playlist(playlist_id)

    @Slot(int, str)
    def renamePlaylist(self, playlist_id: int, name: str) -> None:
        self._service.rename_playlist(playlist_id, name)

    @Slot(int, int)
    def addTrackToPlaylist(self, playlist_id: int, track_id: int) -> None:
        self._service.add_track(playlist_id, track_id)

    @Slot(int, "QVariantList")
    def addTracksToPlaylist(self, playlist_id: int, track_ids: list) -> None:
        """Add the given track ids to a playlist (duplicates skipped)."""
        ids = [int(t) for t in track_ids if t is not None and int(t) > 0]
        if ids:
            self._service.add_tracks(playlist_id, ids)

    @Slot(result="QVariantList")
    def playlistList(self) -> list:
        """Return [{playlistId, name}] for populating "Add to playlist" menus."""
        return [{"playlistId": r["id"], "name": r["name"]} for r in self._rows]
