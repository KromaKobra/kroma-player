"""Stable selection and batched playback resolution shared by library collections."""
from PySide6.QtCore import QAbstractListModel, Signal, Slot
from app.library.selection import InMemorySelection
from app.repositories.collection_summaries import playback_entries


class CollectionListModel(QAbstractListModel):
    selectionChanged = Signal()
    entity_kind = ""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selection = InMemorySelection()
        self.modelReset.connect(self._retain_selection)

    def _retain_selection(self):
        self._selection.retain(row["id"] for row in self._rows)
        self.selectionChanged.emit()

    def _sort_rows_by(self, fields: dict[str, str]) -> None:
        field = fields.get(self._sort_field)
        if field is None:
            return
        def key(row):
            value = row.get(field)
            return (value.casefold() if isinstance(value, str) else value or 0), row["id"]
        self._rows.sort(key=key, reverse=not self._sort_ascending)

    @Slot(int, result=int)
    def uidAt(self, index):
        return self._rows[index]["id"] if 0 <= index < len(self._rows) else -1

    @Slot(result="QVariantList")
    def selectedUids(self):
        return [row["id"] for row in self._rows if self._selection.is_selected(row["id"])]

    @Slot(result="QVariantList")
    def selectedRows(self):
        return [i for i, row in enumerate(self._rows) if self._selection.is_selected(row["id"])]

    @Slot("QVariantList")
    def setSelectedMultiple(self, ids):
        self._selection.set_multiple(ids)
        self._retain_selection()

    @Slot(int)
    def setSelectedExclusive(self, uid):
        self.setSelectedMultiple([uid])

    @Slot()
    def clearSelection(self):
        self._selection.clear()
        self.selectionChanged.emit()

    @Slot(int, result="QVariantMap")
    def entity(self, uid):
        row = next((row for row in self._rows if row["id"] == uid), {})
        return {"id": row.get("id", -1), "name": row.get("name", ""),
                "coverHash": row.get("cover_hash", row.get("image_hash", "")),
                "trackCount": row.get("track_count", 0)}

    @Slot("QVariantList", result="QVariantList")
    def playbackEntries(self, entity_ids):
        """Resolve one batch, preserving display order and each collection's order."""
        wanted = set(int(uid) for uid in entity_ids)
        ids = [row["id"] for row in self._rows if row["id"] in wanted]
        if not ids:
            return []
        repo = getattr(self._service, self.entity_kind + "_repo")
        return playback_entries(repo.session, self.entity_kind, ids)
