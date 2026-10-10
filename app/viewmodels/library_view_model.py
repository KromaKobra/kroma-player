"""Library ViewModel — what a GUI needs to know to build its own track lists.

This is the counterpart to the ``TrackQuery`` QML type. The type lets a GUI
declare a list; this object tells it what it can put in one — which fields
exist, what type each carries, and which of them can be sorted or filtered on.

Deliberately absent: labels, colours, widths, icons, renderers, and any notion
of a column. A GUI names and draws things itself. The backend's job is to say
what there is.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Property, QObject, Signal, Slot

from app.library.fields import catalog
from app.services.track_service import TrackService


class LibraryViewModel(QObject):
    """The library's shape and size, for a GUI building its own views."""

    stats_changed = Signal()

    # The filter vocabulary. Operators are the strings ``TrackQuery.filters``
    # accepts; a GUI builds its own filter UI from these rather than hardcoding
    # a list that could drift out of step with what the backend understands.
    TEXT_OPERATORS = ["Contains", "Equals"]
    NUMERIC_OPERATORS = ["=", ">", "<", ">=", "<="]

    def __init__(self, track_service: TrackService, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._track_service = track_service
        self._total = 0

    @Property("QVariantList", constant=True)
    def fields(self) -> list:
        """Every field a track exposes: ``{key, type, sortable, filterable}``.

        ``key`` is both the role name to read in a delegate and the value to
        pass as ``TrackQuery.sortField`` or a filter's ``field``.
        """
        return catalog()

    @Property("QVariantList", constant=True)
    def textOperators(self) -> list:  # noqa: N802
        return list(self.TEXT_OPERATORS)

    @Property("QVariantList", constant=True)
    def numericOperators(self) -> list:  # noqa: N802
        return list(self.NUMERIC_OPERATORS)

    @Property(int, notify=stats_changed)
    def trackCount(self) -> int:  # noqa: N802
        """Tracks in the library, as of the last ``refresh()``."""
        return self._total

    @Slot()
    def refresh(self) -> None:
        stats = self._track_service.get_library_stats()
        total = int(stats.get("total", 0))
        if total != self._total:
            self._total = total
            self.stats_changed.emit()

    @Slot(str, result="QVariantList")
    def operatorsFor(self, field_key: str) -> list:  # noqa: N802
        """The operators that make sense for a field's type."""
        for field in catalog():
            if field["key"] == field_key:
                if field["type"] in ("int", "seconds", "yyyymmdd"):
                    return list(self.NUMERIC_OPERATORS)
                return list(self.TEXT_OPERATORS)
        return []
