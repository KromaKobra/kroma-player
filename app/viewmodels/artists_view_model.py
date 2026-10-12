"""Artists ViewModel — list model of every artist, plus the lookup QML needs.

Two jobs, and they are the same data seen at two zoom levels:

- **The list.** A row per artist, for a browse or repair page. Artists are bounded
  by the size of the library rather than by anything a user types, so this is a
  plain reset-on-change model with no paging: one query fills it, and a mutation
  refills it. That is the same trade ``playlistsVM`` makes, for the same reason.
- **The lookup.** ``suggest()`` backs the track editor's artist input. That input
  exists to make picking an *existing* artist easier than typing a name that
  nearly matches one, which is the mechanism by which "Daft Punk" and "daft punk"
  stopped being two rows.

The list is deliberately not demand-driven like the track lists. A track list has
to answer for fifty thousand rows and does so by fetching only what is painted;
an artist list answers for a few thousand at most, where the machinery would cost
more than it saved.
"""
from __future__ import annotations

from enum import IntEnum
from typing import Any, Dict, List, Optional

from PySide6.QtCore import (
    Property,
    QModelIndex,
    Qt,
    Signal,
    Slot,
)

from app.viewmodels.collection_list_model import CollectionListModel
from app.repositories.collection_summaries import artist_durations
from app.events.event_bus import EventBus
from app.library.context import declared_id_kinds
from app.library.identity import declared_ids
from app.repositories.artist_repository import DEFAULT_SORT
from app.services.artist_service import ArtistService


class ArtistRoles(IntEnum):
    ArtistId       = Qt.ItemDataRole.UserRole + 1
    Name           = Qt.ItemDataRole.UserRole + 2
    ImageHash      = Qt.ItemDataRole.UserRole + 3
    TrackCount     = Qt.ItemDataRole.UserRole + 4
    ExternalIds    = Qt.ItemDataRole.UserRole + 5
    NeedsAttention = Qt.ItemDataRole.UserRole + 6
    # Appended, never inserted: these numbers are the model's wire format.
    IsIdentified   = Qt.ItemDataRole.UserRole + 7

    TotalDuration = Qt.ItemDataRole.UserRole + 8


_ROLE_NAMES: dict[int, bytes] = {
    ArtistRoles.ArtistId:       b"artistId",
    ArtistRoles.Name:           b"name",
    ArtistRoles.ImageHash:      b"imageHash",
    ArtistRoles.TrackCount:     b"trackCount",
    ArtistRoles.ExternalIds:    b"externalIds",
    ArtistRoles.NeedsAttention: b"needsAttention",
    ArtistRoles.IsIdentified:   b"isIdentified",
    ArtistRoles.TotalDuration: b"totalDuration",
}


class ArtistsViewModel(CollectionListModel):
    """List model exposing every artist, plus name lookup for the editor."""

    entity_kind = "artist"

    counts_changed = Signal()
    sort_changed = Signal()

    def __init__(
        self,
        artist_service: ArtistService,
        event_bus: EventBus,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._service = artist_service
        self._rows: List[Dict[str, Any]] = []
        self._sort_field: str = DEFAULT_SORT
        self._sort_ascending: bool = False

        for evt in (
            "artist.updated", "artist.deleted",
            # A track changing hands changes artist track counts, and a track
            # being added or deleted can create or empty an artist outright.
            "track.added", "track.updated", "track.deleted",
        ):
            event_bus.subscribe(evt, self._on_artists_changed)
        # Which plugins are on decides which ids are shown, not which artists
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
        if role == ArtistRoles.ArtistId:
            return row["id"]
        if role == ArtistRoles.Name:
            return row["name"]
        if role == ArtistRoles.ImageHash:
            return row["image_hash"]
        if role == ArtistRoles.TrackCount:
            return row["track_count"]
        if role == ArtistRoles.ExternalIds:
            return declared_ids(row.get("external_ids"), declared_id_kinds(), "artist")
        if role == ArtistRoles.IsIdentified:
            return bool(declared_ids(row.get("external_ids"), declared_id_kinds(), "artist"))
        if role == ArtistRoles.NeedsAttention:
            return row["needs_attention"]
        if role == ArtistRoles.TotalDuration:
            return row.get("total_duration", 0)
        return None

    def roleNames(self) -> dict[int, bytes]:
        return _ROLE_NAMES

    def _on_plugins_changed(self, *_args) -> None:
        if self._rows:
            self.dataChanged.emit(
                self.index(0), self.index(len(self._rows) - 1),
                [ArtistRoles.ExternalIds, ArtistRoles.IsIdentified],
            )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @Property(int, notify=counts_changed)
    def count(self) -> int:
        return len(self._rows)

    @Property(int, notify=counts_changed)
    def needsAttentionCount(self) -> int:  # noqa: N802 (Qt slot, camelCase for QML)
        """How many artist names look like several artists run together.

        What a GUI puts a badge on. Never acted upon automatically — see
        ``ArtistService.propose_split`` for why taking a name apart is always the
        user's call.
        """
        return sum(1 for row in self._rows if row["needs_attention"])

    # ------------------------------------------------------------------
    # Ordering
    # ------------------------------------------------------------------
    #
    # Named as `TrackQuery`'s two are, because they mean the same thing to a GUI
    # and a sort control written against one should read the other without
    # translation. The keys differ — those are catalog field keys, these are the
    # three in `SORT_FIELDS` — but the shape does not.
    #
    # No coalescing timer, unlike `TrackQuery`: that one debounces because a
    # binding pass can set filters and sort together and each costs a query over
    # fifty thousand tracks. This is one grouped query over a few thousand rows,
    # and running it twice is cheaper than the machinery to run it once.

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
        artist_durations(self._service.artist_repo.session, self._rows)
        self._sort_rows_by({"totalDuration": "total_duration"})

    def _resort(self) -> None:
        """Re-read in the new order.

        A full reset rather than a move: the rows are re-queried anyway, and the
        selection remains keyed by entity ID, and the view restores its scroll
        offset when refreshing.
        """
        self.beginResetModel()
        self._reload()
        self.endResetModel()
        self.sort_changed.emit()

    def _on_artists_changed(self, *_args) -> None:
        self.beginResetModel()
        self._reload()
        self.endResetModel()
        self.counts_changed.emit()

    # ------------------------------------------------------------------
    # Slots called from QML
    # ------------------------------------------------------------------

    @Slot()
    def refresh(self) -> None:
        self._on_artists_changed()

    @Slot(str, result="QVariantList")
    @Slot(str, int, result="QVariantList")
    def suggest(self, query: str, limit: int = 8) -> list:
        """Artists matching *query*, best match first — the editor's typeahead.

        Returns ``[{id, name, imageHash, trackCount, externalIds, isIdentified}]``. Answers from
        the database rather than filtering the loaded rows, so it stays correct
        while another page is mid-edit and stays cheap on a large library.
        """
        return [self._to_qml(row) for row in self._service.search(query, limit)]

    @Slot(int, result="QVariant")
    def artist(self, artist_id: int):
        """One artist as a plain object, or ``null``."""
        details = self._service.details(int(artist_id))
        return self._to_qml(details) if details else None

    @Slot(str, result=int)
    def idForName(self, name: str) -> int:  # noqa: N802
        """The id of the artist named *name*, or 0.

        Case-insensitive, because it answers "did the user just retype an artist
        we already have?" — and the answer that matters is yes even when the
        capitals differ.
        """
        artist = self._service.artist_repo.get_by_name_insensitive(name)
        return int(artist.id) if artist is not None else 0

    @staticmethod
    def _to_qml(row: Optional[Dict[str, Any]]) -> dict:
        """snake_case row → the camelCase shape the rest of the QML API uses."""
        row = row or {}
        return {
            "id": row.get("id", 0),
            "name": row.get("name", ""),
            "imageHash": row.get("image_hash", ""),
            "trackCount": row.get("track_count", 0),
            # Only the kinds an enabled plugin declares: with every plugin off a
            # artist is shown as what it is locally, and the ids it carries wait,
            # untouched, for the plugin to come back.
            "externalIds": declared_ids(row.get("external_ids"), declared_id_kinds(), "artist"),
            "isIdentified": bool(declared_ids(row.get("external_ids"), declared_id_kinds(), "artist")),
            "needsAttention": bool(row.get("needs_attention", False)),
        }
