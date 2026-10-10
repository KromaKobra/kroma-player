"""A library query a GUI can declare for itself.

``TrackQuery`` is registered as a QML type, so a GUI writes:

.. code-block:: qml

    import Kroma.Library 1.0

    TrackQuery {
        id: browse
        filters: [{ field: "title", operator: "Contains", value: searchBox.text }]
        sortField: "artist"
    }
    ListView { model: browse }

That is the point of it. The backend no longer decides how many track lists
exist or what each one shows — a GUI wanting three browse panes, an album grid
or an audiobook shelf declares three of these and no backend change is needed.

The rows are ids until something paints them; see ``list_model`` for how that
works.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from app.library.context import library_context
from app.library.list_model import TrackListModel
from app.search.search_query import parse_filter

logger = logging.getLogger(__name__)


class TrackQuery(TrackListModel):
    """A sorted, filtered view of the library, resolved to an id vector."""

    filtersChanged = Signal()
    sortChanged = Signal()

    # Library changes arrive on whatever thread did the work, so they are
    # bounced through a signal to land back on the UI thread before touching
    # the model.
    _libraryChanged = Signal(str, int)
    # Which plugins are on changed: the identity columns mean something else
    # now. Bounced for the same reason, though it usually arrives on the UI
    # thread already.
    _pluginsChanged = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        context = library_context()
        super().__init__(context.row_cache, parent)

        self._repo = context.repository
        self._event_bus = context.event_bus

        self._filters: list = []
        self._sort_field: str = ""
        self._sort_ascending: bool = True

        # Setting filters and sort in the same QML binding pass must run one
        # query, not three.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(0)
        self._refresh_timer.timeout.connect(self._run_query)

        self._libraryChanged.connect(self._on_library_changed)
        self._event_bus.subscribe("track.added", self._on_track_added)
        self._event_bus.subscribe("track.updated", self._on_track_updated)
        self._event_bus.subscribe("track.deleted", self._on_track_deleted)
        self._pluginsChanged.connect(self.refresh_identity_roles)
        self._event_bus.subscribe("plugins.changed", self._pluginsChanged.emit)

        # The first query is scheduled, not run. When QML is constructing the
        # object it has not assigned `filters` or `sortField` yet, so querying
        # here would read the whole library and then immediately re-read it.
        # The zero-timer fires once component creation has finished and before
        # anything is rendered, so the single query it runs sees the final
        # property values.
        #
        # QQmlParserStatus would express this more directly, but PySide6 does
        # not dispatch classBegin/componentComplete to Python types registered
        # with qmlRegisterType — they are simply never called. Do not "fix"
        # this by adding them back.
        self._schedule_refresh()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    def _get_filters(self) -> list:
        return list(self._filters)

    def _set_filters(self, filters: Any) -> None:
        new = list(filters) if filters else []
        if new == self._filters:
            return
        self._filters = new
        self.filtersChanged.emit()
        self._schedule_refresh()

    filters = Property("QVariantList", _get_filters, _set_filters, notify=filtersChanged)

    def _get_sort_field(self) -> str:
        return self._sort_field

    def _set_sort_field(self, field: str) -> None:
        field = str(field or "")
        if field == self._sort_field:
            return
        self._sort_field = field
        self.sortChanged.emit()
        self._schedule_refresh()

    sortField = Property(str, _get_sort_field, _set_sort_field, notify=sortChanged)

    def _get_sort_ascending(self) -> bool:
        return self._sort_ascending

    def _set_sort_ascending(self, ascending: bool) -> None:
        ascending = bool(ascending)
        if ascending == self._sort_ascending:
            return
        self._sort_ascending = ascending
        self.sortChanged.emit()
        self._schedule_refresh()

    sortAscending = Property(
        bool, _get_sort_ascending, _set_sort_ascending, notify=sortChanged
    )

    # ------------------------------------------------------------------
    # Running the query
    # ------------------------------------------------------------------

    @Slot()
    def refresh(self) -> None:
        """Re-run the id query now, cancelling any scheduled one.

        Constructed from Python rather than QML, a query resolves on the next
        turn of the event loop; call this to have it resolve immediately.
        """
        self._refresh_timer.stop()
        self._run_query()

    def _schedule_refresh(self) -> None:
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _run_query(self) -> None:
        parsed = [pf for pf in map(parse_filter, self._parsed_filters()) if pf]
        try:
            ids = self._repo.query_ids(parsed, self._sort_field, self._sort_ascending)
        except Exception:
            logger.exception("Track query failed; leaving the previous result in place")
            return
        self.set_ids(ids)

    def _parsed_filters(self) -> list[dict]:
        """Coerce the QML filter list into plain dicts, dropping malformed entries."""
        out: list[dict] = []
        for raw in self._filters:
            if isinstance(raw, dict):
                out.append(raw)
            elif hasattr(raw, "toVariant"):
                value = raw.toVariant()
                if isinstance(value, dict):
                    out.append(value)
        return out

    # ------------------------------------------------------------------
    # Staying current
    # ------------------------------------------------------------------

    def _on_track_added(self, track) -> None:
        self._libraryChanged.emit("added", getattr(track, "id", 0) or 0)

    def _on_track_updated(self, track) -> None:
        self._libraryChanged.emit("updated", getattr(track, "id", 0) or 0)

    def _on_track_deleted(self, track_id: int) -> None:
        self._libraryChanged.emit("deleted", int(track_id))

    def _on_library_changed(self, kind: str, track_id: int) -> None:
        if kind == "updated":
            # Membership and order are unchanged; only the row's contents moved.
            self.invalidate_track(track_id)
            return
        # An add or a delete makes the id vector itself stale.
        self._cache.invalidate(track_id)
        self._schedule_refresh()

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    def detach(self) -> None:
        """Stop listening to the event bus.

        QML-owned objects are destroyed when their scope goes away, and the bus
        holds strong references to the handlers, so a query that is not
        detached keeps its model alive.
        """
        for event, handler in (
            ("track.added", self._on_track_added),
            ("track.updated", self._on_track_updated),
            ("track.deleted", self._on_track_deleted),
        ):
            try:
                self._event_bus.unsubscribe(event, handler)
            except (KeyError, ValueError):
                pass
