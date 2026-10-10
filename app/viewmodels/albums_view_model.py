"""Albums ViewModel — list model of every album, plus the lookup QML needs.

The same two jobs, at the same two zoom levels, as
:mod:`app.viewmodels.artists_view_model`:

- **The list.** A row per album, for the Albums face of the library and the
  repair badge on it. Albums are bounded by the size of the library rather than
  by anything a user types, so this is a plain reset-on-change model with no
  paging: one query fills it, and a mutation refills it.
- **The lookup.** ``suggest()`` backs the track editor's album input, so picking
  an album the library already has is easier than typing a name that nearly
  matches one — the mechanism that stops "Discovery" and "discovery" becoming
  two records.

Deliberately not demand-driven like the track lists, for the reason
``ArtistsViewModel`` gives: a track list has to answer for fifty thousand rows
and does so by fetching only what is painted; an album list answers for a few
thousand at most, where the machinery would cost more than it saved.
"""
from __future__ import annotations

from enum import IntEnum
from typing import Any, Dict, List, Optional

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QModelIndex,
    Qt,
    Signal,
    Slot,
)

from app.events.event_bus import EventBus
from app.library.context import declared_id_kinds
from app.library.identity import declared_ids
from app.repositories.album_repository import DEFAULT_SORT
from app.services.album_service import AlbumService


class AlbumRoles(IntEnum):
    AlbumId        = Qt.ItemDataRole.UserRole + 1
    Name           = Qt.ItemDataRole.UserRole + 2
    CoverHash      = Qt.ItemDataRole.UserRole + 3
    TrackCount     = Qt.ItemDataRole.UserRole + 4
    ExternalIds    = Qt.ItemDataRole.UserRole + 5
    NeedsAttention = Qt.ItemDataRole.UserRole + 6
    # Appended, never inserted: these numbers are the model's wire format and a
    # GUI that cached one has to go on getting the same value back.
    IsComplete     = Qt.ItemDataRole.UserRole + 7
    IsIdentified   = Qt.ItemDataRole.UserRole + 8


_ROLE_NAMES: dict[int, bytes] = {
    AlbumRoles.AlbumId:        b"albumId",
    AlbumRoles.Name:           b"name",
    AlbumRoles.CoverHash:      b"coverHash",
    AlbumRoles.TrackCount:     b"trackCount",
    AlbumRoles.ExternalIds:    b"externalIds",
    AlbumRoles.NeedsAttention: b"needsAttention",
    AlbumRoles.IsComplete:     b"isComplete",
    AlbumRoles.IsIdentified:   b"isIdentified",
}


class AlbumsViewModel(QAbstractListModel):
    """List model exposing every album, plus name lookup for the editor."""

    counts_changed = Signal()
    sort_changed = Signal()

    def __init__(
        self,
        album_service: AlbumService,
        event_bus: EventBus,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._service = album_service
        self._rows: List[Dict[str, Any]] = []
        self._sort_field: str = DEFAULT_SORT
        self._sort_ascending: bool = False

        for evt in (
            "album.created", "album.updated", "album.deleted",
            # A track changing hands changes album track counts, and a track's
            # artwork changing can make an album's covers agree or stop agreeing
            # without any album row being touched at all.
            "track.added", "track.updated", "track.deleted",
        ):
            event_bus.subscribe(evt, self._on_albums_changed)
        # Which plugins are on decides which ids are shown, not which albums
        # exist — a repaint of two roles, not a reload.
        event_bus.subscribe("plugins.changed", self._on_plugins_changed)

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
        if role == AlbumRoles.AlbumId:
            return row["id"]
        if role == AlbumRoles.Name:
            return row["name"]
        if role == AlbumRoles.CoverHash:
            return row["cover_hash"]
        if role == AlbumRoles.TrackCount:
            return row["track_count"]
        if role == AlbumRoles.ExternalIds:
            return declared_ids(row.get("external_ids"), declared_id_kinds(), "album")
        if role == AlbumRoles.IsIdentified:
            return bool(declared_ids(row.get("external_ids"), declared_id_kinds(), "album"))
        if role == AlbumRoles.NeedsAttention:
            return row["needs_attention"]
        if role == AlbumRoles.IsComplete:
            return row["is_complete"]
        return None

    def roleNames(self) -> dict[int, bytes]:
        return _ROLE_NAMES

    def _on_plugins_changed(self, *_args) -> None:
        if self._rows:
            self.dataChanged.emit(
                self.index(0), self.index(len(self._rows) - 1),
                [AlbumRoles.ExternalIds, AlbumRoles.IsIdentified],
            )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @Property(int, notify=counts_changed)
    def count(self) -> int:
        return len(self._rows)

    @Property(int, notify=counts_changed)
    def needsAttentionCount(self) -> int:  # noqa: N802
        """How many albums have tracks that disagree about their artwork.

        What a GUI puts a badge on. Never acted upon automatically — which of
        the two repairs is right depends on whether these are one record or two,
        and only the user knows that.
        """
        return sum(1 for row in self._rows if row["needs_attention"])

    # ------------------------------------------------------------------
    # Ordering
    # ------------------------------------------------------------------

    def _get_sort_field(self) -> str:
        return self._sort_field

    def _set_sort_field(self, value: str) -> None:
        value = str(value or DEFAULT_SORT)
        if value == self._sort_field:
            return
        self._sort_field = value
        self._resort()

    def _get_sort_ascending(self) -> bool:
        return self._sort_ascending

    def _set_sort_ascending(self, value: bool) -> None:
        value = bool(value)
        if value == self._sort_ascending:
            return
        self._sort_ascending = value
        self._resort()

    sortField = Property(str, _get_sort_field, _set_sort_field, notify=sort_changed)
    sortAscending = Property(
        bool, _get_sort_ascending, _set_sort_ascending, notify=sort_changed
    )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reload(self) -> None:
        self._rows = self._service.list_all(self._sort_field, self._sort_ascending)

    def _resort(self) -> None:
        self.beginResetModel()
        self._reload()
        self.endResetModel()
        self.sort_changed.emit()

    def _on_albums_changed(self, *_args) -> None:
        self.beginResetModel()
        self._reload()
        self.endResetModel()
        self.counts_changed.emit()

    # ------------------------------------------------------------------
    # Slots called from QML
    # ------------------------------------------------------------------

    @Slot()
    def refresh(self) -> None:
        self._on_albums_changed()

    @Slot(str, result="QVariantList")
    @Slot(str, int, result="QVariantList")
    def suggest(self, query: str, limit: int = 8) -> list:
        """Albums matching *query*, best match first — the editor's typeahead.

        Answers from the database rather than filtering the loaded rows, so it
        stays correct while another page is mid-edit and stays cheap on a large
        library.
        """
        return [self._to_qml(row) for row in self._service.search(query, limit)]

    @Slot(int, result="QVariant")
    def album(self, album_id: int):
        """One album as a plain object, or ``null``."""
        details = self._service.details(int(album_id))
        return self._to_qml(details) if details else None

    @Slot(str, result=int)
    def idForName(self, name: str) -> int:  # noqa: N802
        """The id of the first album named *name*, or 0.

        Case-insensitive, because it answers "did the user just retype an album
        we already have?". Note that unlike the artist version this can be
        genuinely ambiguous — two records may share a name — so it answers with
        the oldest and the editor should prefer ``suggest`` where the user can
        see which one they are picking.
        """
        album = self._service.album_repo.get_by_name_insensitive(name)
        return int(album.id) if album is not None else 0

    @staticmethod
    def _to_qml(row: Optional[Dict[str, Any]]) -> dict:
        """snake_case row → the camelCase shape the rest of the QML API uses."""
        row = row or {}
        return {
            "id": row.get("id", 0),
            "name": row.get("name", ""),
            "coverHash": row.get("cover_hash", ""),
            "trackCount": row.get("track_count", 0),
            # Only the kinds an enabled plugin declares: with every plugin off a
            # album is shown as what it is locally, and the ids it carries wait,
            # untouched, for the plugin to come back.
            "externalIds": declared_ids(row.get("external_ids"), declared_id_kinds(), "album"),
            "isIdentified": bool(declared_ids(row.get("external_ids"), declared_id_kinds(), "album")),
            "needsAttention": bool(row.get("needs_attention", False)),
            "isComplete": bool(row.get("is_complete", False)),
        }
