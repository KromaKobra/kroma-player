"""The demand-driven track list model.

Every track list in the application is one of these. A model holds an ordered
vector of track ids and nothing else; row *data* arrives only because something
tried to paint it.

The mechanism is ``data()`` itself. Qt calls it for exactly the rows and roles
it is about to draw, having already accounted for delegate height, viewport
size, cache buffer and scroll position. So a miss inside ``data()`` is the
demand signal: record it, return a placeholder, and fill it on the next turn of
the event loop.

That is the whole answer to "how does the backend know whether the GUI shows 50
rows or 5". It never asks and never guesses. A GUI with tall delegates misses
five rows and fetches a page; a dense one misses fifty and fetches a page. The
number appears nowhere in this file.

Three rules hold the design up:

- **Never do I/O in ``data()``.** It runs during paint, once per role per
  delegate. A synchronous read there is what makes a list stutter.
- **Never emit ``dataChanged`` from inside ``data()``.** The flush is on a
  timer so the signal lands on a later turn, not re-entrantly mid-paint.
- **Read where the viewport *is*, not everywhere it has been.** A miss is a
  demand, but demand goes stale: dragging a scrollbar registers one for every
  page it crosses, and by the time the flush runs the pointer is elsewhere
  entirely. Reading those costs a query and a cache insertion each for rows
  nobody will ever see, and on a long list there are enough of them to evict
  the page the user actually landed on — which is why the stutter used to
  outlast the gesture. See ``setVisibleRange`` and ``_live_pages``.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QModelIndex,
    QObject,
    Qt,
    QTimer,
    Signal,
    Slot,
)

from app.library.context import declared_id_kinds, library_context
from app.library.fields import FIELDS, FIELDS_BY_KEY, IDENTITY_FIELD_KEYS, Tier, tiers_for_keys
from app.library.identity import identity_value
from app.library.roles import FIELD_TO_ROLE, ROLE_IS_SELECTED, ROLE_NAMES, ROLE_TO_FIELD
from app.library.row_cache import MISS, TrackRowCache
from app.library.selection import InMemorySelection, SelectionStore

# Rows are fetched a page at a time so that a flick does not turn into one
# query per row. Large enough to cover any plausible viewport, small enough
# that the query stays trivial.
PAGE_SIZE = 128

# Misses are coalesced for roughly one frame before being turned into queries.
FLUSH_MS = 16

# How long to wait when the viewport is being swept rather than read. Has to
# comfortably outlast the gap between pointer moves during a drag — 8-16 ms —
# so it never fires mid-gesture, and stay short enough that letting go does not
# read as a delay. Four frames.
SETTLE_MS = 60

# How far either side of the viewport a flush is still willing to read, in
# pages. A page is several screens at any plausible row height, so this covers
# anything a GUI's cache buffer could legitimately be painting while still
# leaving a sweep's leftovers outside.
WINDOW_PAGES = 1

# The roles a fetch of each tier can have filled. Derived from the tier and not
# from the keys that were asked for, because a tier fills atomically: a request
# for `title` also lands `bpm`, and a delegate bound to it has to hear about
# that.
_TIER_ROLES: dict[Tier, tuple[int, ...]] = {
    tier: tuple(FIELD_TO_ROLE[f.key] for f in FIELDS if f.tier is tier)
    for tier in Tier
    if tier is not Tier.ROW
}


def _roles_for_keys(keys: set[str]) -> list[int]:
    """The roles a fetch of *keys* could have changed, and no others."""
    roles: list[int] = []
    for tier in tiers_for_keys(keys):
        roles.extend(_TIER_ROLES.get(tier, ()))
    return roles


class TrackListModel(QAbstractListModel):
    """An ordered list of track ids that resolves its own row data.

    Subclasses supply the id vector (``set_ids``) and, if their rows are not
    plain library tracks, override ``row_value`` to answer ROW-tier fields.
    """

    countChanged = Signal()
    totalDurationChanged = Signal()
    #: Fires whenever the visible selection changes, including when something
    #: other than the GUI changed it — see :meth:`refresh_selection`.
    selectionChanged = Signal()

    def __init__(
        self,
        row_cache: Optional[TrackRowCache] = None,
        selection: Optional[SelectionStore] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._cache = row_cache if row_cache is not None else library_context().row_cache
        self._selection: SelectionStore = selection if selection is not None else InMemorySelection()

        self._ids: list[int] = []
        self._uids: list[int] = []

        # The selection as it was last announced. Compared against on every
        # contents change so a drop made behind the model's back is still
        # reported exactly once — see `refresh_selection`.
        self._announced_selection: frozenset[int] = frozenset()

        # Total playing time of the id vector, summed on demand and held until
        # the vector changes. None means "not computed since the last change".
        self._total_duration: Optional[int] = None

        # Fields something has actually tried to read on this model. This is
        # what replaces asking a column layout which fields to populate: the
        # set is discovered from the delegate's own bindings.
        self._demanded_keys: set[str] = set()

        # Coalescing state for the current frame's misses.
        self._pending_pages: set[int] = set()
        self._pending_keys: set[str] = set()
        self._last_page = 0

        # Where the GUI last said the viewport was, in rows. -1 means nothing
        # ever said — a GUI that does not call `setVisibleRange` clamps nothing
        # and behaves exactly as it did before, which is what keeps that slot
        # the optional hint it is documented to be.
        self._visible_first = -1
        self._visible_last = -1

        # Ids that were asked for and did not come back — a deleted track still
        # sitting in a stale id vector. Without this they would be re-requested
        # on every single paint, forever.
        self._unresolved: set[int] = set()

        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(FLUSH_MS)
        self._flush_timer.timeout.connect(self._flush)

    # ------------------------------------------------------------------
    # Qt model interface
    # ------------------------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._ids)

    def roleNames(self) -> dict:  # noqa: N802
        return dict(ROLE_NAMES)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        row = index.row()
        if not 0 <= row < len(self._ids):
            return None

        field = ROLE_TO_FIELD.get(int(role))
        if field is None:
            return None

        if field.tier is Tier.ROW:
            return self.row_value(row, field.key)

        if field.key in IDENTITY_FIELD_KEYS:
            ids = self._external_ids(row)
            if ids is MISS:
                return None
            return identity_value(field.key, ids, declared_id_kinds())

        local = self.local_value(row, field.key)
        if local is not MISS:
            return local

        self._demanded_keys.add(field.key)
        track_id = self._ids[row]
        value = self._cache.get(track_id, field.key)
        if value is MISS:
            self._request(row, track_id, field.key)
            return None
        return value

    def _external_ids(self, row: int) -> Any:
        """The raw id map behind the identity fields, or ``MISS`` while it loads.

        The row's own when it has one — a web result or an import candidate
        carries its ids — and the cache's for a library track. The three fields
        built from it are worked out here, at paint time, against what is
        declared now; see ``app.library.identity``.
        """
        local = self.local_value(row, "externalIds")
        if local is not MISS:
            return local or {}
        self._demanded_keys.add("externalIds")
        track_id = self._ids[row]
        value = self._cache.get(track_id, "externalIds")
        if value is MISS:
            self._request(row, track_id, "externalIds")
        return value

    def external_ids_for_uid(self, uid: int) -> dict:
        """Every id the row with *uid* carries, read now — for a context menu.

        Not for painting: a library row whose ids are not cached yet is read
        from the database here, synchronously, which is right for one right
        click and wrong for a scroll.
        """
        try:
            row = self._uids.index(int(uid))
        except ValueError:
            return {}
        local = self.local_value(row, "externalIds")
        if local is not MISS:
            return dict(local or {})
        track_id = self._ids[row]
        cached = self._cache.get(track_id, "externalIds")
        if cached is MISS:
            self._cache.fetch([track_id], {"externalIds"})
            cached = self._cache.get(track_id, "externalIds")
        return dict(cached or {}) if cached is not MISS else {}

    def refresh_identity_roles(self) -> None:
        """Repaint the identity fields: what is declared has changed.

        Nothing is re-read — the ids themselves did not move — so this is one
        ``dataChanged`` over the whole list for three roles.
        """
        if self._ids:
            self.dataChanged.emit(
                self.index(0), self.index(len(self._ids) - 1),
                [FIELD_TO_ROLE[key] for key in sorted(IDENTITY_FIELD_KEYS)],
            )

    def _get_count(self) -> int:
        return len(self._ids)

    #: Row count as a bindable property. ``rowCount()`` is a plain method, so
    #: without this a GUI has to mirror the count off ``ListView.count``.
    count = Property(int, _get_count, notify=countChanged)

    def _get_total_duration(self) -> int:
        """Seconds of playing time across every row, duplicates included.

        Computed the first time something binds it after a change and held
        until the next one, so a header showing "48 min" costs one two-column
        query per list edit rather than one per repaint.
        """
        if self._total_duration is None:
            durations = self._cache.durations(self._ids)
            self._total_duration = sum(durations.get(i, 0) for i in self._ids)
        return self._total_duration

    #: Total playing time in seconds, for "N tracks · 1 hr 12 min" headers.
    totalDuration = Property(int, _get_total_duration, notify=totalDurationChanged)

    def _invalidate_total_duration(self) -> None:
        self._total_duration = None
        self.totalDurationChanged.emit()

    # ------------------------------------------------------------------
    # Row-supplied fields
    # ------------------------------------------------------------------

    def row_value(self, row: int, key: str) -> Any:
        """Answer a ROW-tier field. Override to add list-specific fields.

        The base implementation covers identity and selection, which every
        track list has. Lists whose rows are not library tracks — web results,
        import candidates — override this to add their own.
        """
        if key == "trackId":
            return self._ids[row]
        if key == "uid":
            return self._uids[row]
        if key == "isSelected":
            return self._selection.is_selected(self._uids[row])
        return None

    def local_value(self, row: int, key: str) -> Any:
        """Answer a field from the model's own data, or ``MISS`` to use the cache.

        Some lists show tracks that are not in the library at all — web search
        results, import candidates. Their fields cannot be read from the
        database because there is nothing to read. Overriding this lets such a
        model serve those rows itself while library rows still go through the
        shared cache.
        """
        return MISS

    # ------------------------------------------------------------------
    # Demand tracking
    # ------------------------------------------------------------------

    def _request(self, row: int, track_id: int, key: str) -> None:
        if track_id in self._unresolved:
            return
        page = row // PAGE_SIZE
        self._pending_pages.add(page)
        self._pending_keys.add(key)
        self._last_page = page
        # Never restart a timer that is already running. While a sweep is being
        # waited out that timer is set long, and the misses a sweep generates by
        # the hundred must not shorten it back to a frame.
        if not self._flush_timer.isActive():
            self._flush_timer.start(FLUSH_MS)

    def _flush(self) -> None:
        pages = self._live_pages(self._pending_pages)
        keys = self._pending_keys
        self._pending_pages = set()
        self._pending_keys = set()
        if not pages or not keys:
            return

        rows, ids = self._rows_for_pages(self._with_prefetch(pages))
        if not ids:
            return

        self._cache.fetch(ids, keys)
        self._mark_unresolved(ids, keys)

        # Every row this flush covered, not just the ones this fetch happened to
        # fill. The cache is shared by every list, so a row can be filled by a
        # *different* list between missing here and this flush — ``fetch`` then
        # reports it as nothing to do, and filtering on that leaves the delegate
        # showing the blank it was handed, permanently. It is the miss that
        # earns the repaint, not the read.
        #
        # The roles, on the other hand, are named exactly. A page read can only
        # have moved the fields in the tiers it touched, and saying "all roles"
        # re-fires `uid`, `trackId` and `isSelected` at every delegate on
        # screen — which re-runs selection lookups, the playing-row test and,
        # on the import list, the whole of `importState`.
        self._emit_changed(rows, _roles_for_keys(keys))

    def _live_pages(self, pages: set[int]) -> set[int]:
        """Drop demand for pages the viewport has already left behind.

        The window is deliberately generous: a page is several screens, so
        anything a GUI's cache buffer could legitimately be painting is inside
        it. A model no GUI has given a viewport to clamps nothing.
        """
        if self._visible_first < 0:
            return pages
        low = self._visible_first // PAGE_SIZE - WINDOW_PAGES
        high = self._visible_last // PAGE_SIZE + WINDOW_PAGES
        return {page for page in pages if low <= page <= high}

    def _with_prefetch(self, pages: set[int]) -> set[int]:
        """Add the next page in the direction of travel.

        A flick asks for pages in order, so the cheapest useful prediction is
        simply "one more of the same".
        """
        if not pages:
            return pages
        last_page = max(0, (len(self._ids) - 1) // PAGE_SIZE)
        direction = 1 if self._last_page >= max(pages) else -1
        ahead = {p + direction for p in pages}
        return pages | {p for p in ahead if 0 <= p <= last_page}

    def _rows_for_pages(self, pages: Iterable[int]) -> tuple[list[int], list[int]]:
        rows: list[int] = []
        ids: list[int] = []
        total = len(self._ids)
        for page in sorted(pages):
            start = page * PAGE_SIZE
            for row in range(start, min(start + PAGE_SIZE, total)):
                rows.append(row)
                ids.append(self._ids[row])
        return rows, ids

    def _mark_unresolved(self, ids: list[int], keys: set[str]) -> None:
        """Remember ids that came back empty so they are not re-requested forever."""
        tiers = tiers_for_keys(keys)
        if not tiers:
            return
        for track_id in set(ids):
            if all(self._cache.has_tier(track_id, tier) for tier in tiers):
                self._unresolved.discard(track_id)
            else:
                self._unresolved.add(track_id)

    def _emit_changed(self, rows: list[int], roles: Optional[list[int]] = None) -> None:
        """Emit dataChanged over the contiguous runs covering *rows*.

        *roles* names what could have moved. Omitting it means "all roles",
        which is the honest answer whenever the change is genuinely open-ended —
        an edit can move anything, including whatever a subclass serves from
        ``row_value``. It is the wrong answer for a page read, which can only
        have filled the tiers it queried: saying "all roles" there re-fires
        `uid`, `trackId` and `isSelected` at every delegate on screen, and none
        of those can possibly have changed.

        The enumeration has to be derived from the *tier* rather than from the
        keys that were asked for, because a tier fills atomically — a request
        for `title` also lands `bpm`, and a delegate bound to it has to hear
        about that. See ``_roles_for_keys``.
        """
        if not rows:
            return
        roles = roles or []
        rows = sorted(rows)
        start = previous = rows[0]
        for row in rows[1:]:
            if row == previous + 1:
                previous = row
                continue
            self.dataChanged.emit(self.index(start), self.index(previous), roles)
            start = previous = row
        self.dataChanged.emit(self.index(start), self.index(previous), roles)

    # ------------------------------------------------------------------
    # Contents
    # ------------------------------------------------------------------

    def set_ids(self, ids: list[int], uids: Optional[list[int]] = None) -> None:
        """Replace the whole id vector.

        The row cache is untouched: it is keyed by track id, so a re-sort or
        re-filter repaints out of what is already loaded, with no new reads.
        """
        self.beginResetModel()
        self._ids = [int(i) for i in ids]
        self._uids = list(uids) if uids is not None else list(self._ids)
        retain = getattr(self._selection, "retain", None)
        if retain is not None:
            retain(self._uids)
        self._unresolved.clear()
        self._pending_pages.clear()
        self._pending_keys.clear()
        # The recorded viewport described the *old* list. Left in place, the
        # first paint after a re-sort or re-filter would be clamped against a
        # window that no longer means anything, and the rows the GUI is about to
        # ask for could fall outside it. Deliberately not done in
        # `_set_vectors`: a queue reorder shifts rows by one and the viewport is
        # still true.
        self._visible_first = -1
        self._visible_last = -1
        self.endResetModel()
        self.countChanged.emit()
        self._invalidate_total_duration()
        # `retain` above may have dropped rows that are no longer in the list.
        self.refresh_selection()

    def _set_vectors(self, ids: Iterable[int], uids: Iterable[int]) -> None:
        """Replace the vectors without emitting anything.

        For models that mirror a list owned elsewhere and already bracket the
        change in ``beginInsertRows``/``endRemoveRows`` and friends, so that a
        queue reorder stays a surgical signal rather than a full reset.

        Deliberately silent about selection too: this runs *inside* the bracket,
        and a listener reading ``rowCount()`` back mid-transaction is exactly
        what the module docstring forbids. Callers whose change can drop a row's
        selection call ``refresh_selection`` after their closing ``end*``.
        """
        previous = len(self._ids)
        self._ids = [int(i) for i in ids]
        self._uids = [int(u) for u in uids]
        if len(self._ids) != previous:
            self.countChanged.emit()
        # Unconditional: a queue edit can swap one track for another without
        # the count moving, and the total still changes.
        self._invalidate_total_duration()

    def track_ids(self) -> list[int]:
        return list(self._ids)

    def invalidate_track(self, track_id: int) -> None:
        """Drop a track from the cache and repaint any row showing it."""
        track_id = int(track_id)
        self._cache.invalidate(track_id)
        self._unresolved.discard(track_id)
        self._emit_changed([i for i, tid in enumerate(self._ids) if tid == track_id])
        if track_id in self._ids:
            # An edit can have changed the track's length.
            self._invalidate_total_duration()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    @Slot(int, result="QVariant")
    def uidAt(self, row: int) -> Any:  # noqa: N802
        return self._uids[row] if 0 <= row < len(self._uids) else None

    @Slot(int, result="QVariant")
    def trackIdAt(self, row: int) -> Any:  # noqa: N802
        return self._ids[row] if 0 <= row < len(self._ids) else None

    @Slot(int, result=int)
    def rowOfTrackId(self, track_id: int) -> int:  # noqa: N802
        """First row showing *track_id*, or -1."""
        try:
            return self._ids.index(int(track_id))
        except ValueError:
            return -1

    @Slot(result="QVariantList")
    def trackIds(self) -> list:  # noqa: N802
        """Every library id in the list, in display order.

        The QML-facing counterpart to ``track_ids``, which returns the vector
        verbatim. Rows that are not library tracks — a web search result, an
        import candidate that has not been stashed — carry an id of 0 and are
        skipped here, so the result is always something ``play_from_ids`` can be
        handed directly.
        """
        return [track_id for track_id in self.track_ids() if track_id > 0]

    @Slot(int, int)
    def setVisibleRange(self, first: int, last: int) -> None:  # noqa: N802
        """Where the viewport is, and — from how it moved — whether to wait.

        Still optional, and still a no-op until something has read a field worth
        prefetching: ``data()`` drives loading on its own, and a GUI that never
        calls this behaves exactly as it always did. But a GUI that does call it
        gets two things rather than one.

        The old job is unchanged: warm the pages under the viewport slightly
        before they are painted. The new job is that the model now *remembers*
        the viewport, which is what lets ``_flush`` tell demand for where the
        user is from demand for everywhere they dragged past on the way.

        The distinction between the two is made here, and it is made from
        distance alone. Reading is movement of less than a viewport; anything
        further is a hand on the scrollbar, a flick, or a jump. That inference
        is deliberate — the alternative is a GUI passing down a "scrolling fast"
        flag, which would put knowledge of gestures into a model whose whole
        premise is that it infers demand rather than being told about it.
        """
        if first < 0 or last < first or not self._demanded_keys:
            return

        previous = self._visible_first
        span = last - first + 1
        self._visible_first = first
        self._visible_last = last

        self._pending_pages.update(range(first // PAGE_SIZE, last // PAGE_SIZE + 1))
        self._pending_keys.update(self._demanded_keys)
        self._last_page = last // PAGE_SIZE

        if previous >= 0 and abs(first - previous) > span:
            # Moved further than its own height since the last report, so the
            # list is being swept past rather than read. Pushing the read out
            # for as long as that stays true means the pages crossed are read
            # once, at the end, instead of sixty times a second on the way.
            #
            # Both ways a sweep ends are covered: a pointer that stops sends no
            # further reports and this timer fires, and one that merely slows
            # sends a sub-viewport step, which falls through below and restores
            # the ordinary interval.
            self._flush_timer.start(SETTLE_MS)
            return

        self._flush_timer.start(FLUSH_MS)

    # -- selection -----------------------------------------------------

    @Slot(int, bool)
    def setSelected(self, uid: int, selected: bool) -> None:  # noqa: N802
        self._selection.set_selected(int(uid), selected)
        self._emit_selection_changed()

    @Slot(int)
    def setSelectedExclusive(self, uid: int) -> None:  # noqa: N802
        self._selection.set_exclusive(int(uid))
        self._emit_selection_changed()

    @Slot(list)
    def setSelectedMultiple(self, uids: list) -> None:  # noqa: N802
        self._selection.set_multiple(uids)
        self._emit_selection_changed()

    @Slot()
    def clearSelection(self) -> None:  # noqa: N802
        if not self._selection.selected():
            return
        self._selection.clear()
        self._emit_selection_changed()

    @Slot(result="QVariantList")
    def selectedUids(self) -> list:  # noqa: N802
        selected = self._selection.selected()
        return [uid for uid in self._uids if uid in selected]

    @Slot(result="QVariantList")
    def selectedRows(self) -> list:  # noqa: N802
        """Row indices of the selected rows, ascending.

        The counterpart to ``selectedTrackIds`` for lists where position is the
        identity — the queue, where the same track can appear twice and only
        the row says which one you meant.
        """
        selected = self._selection.selected()
        return [row for row, uid in enumerate(self._uids) if uid in selected]

    @Slot(result="QVariantList")
    def selectedTrackIds(self) -> list:  # noqa: N802
        """Library ids of the selected rows, in display order.

        Exists so a GUI does not have to walk the model calling ``uidAt`` and
        ``trackIdAt`` per row, which costs two QML→Python calls each.
        """
        selected = self._selection.selected()
        return [
            track_id
            for track_id, uid in zip(self._ids, self._uids)
            if uid in selected
        ]

    def _emit_selection_changed(self) -> None:
        if self._ids:
            self.dataChanged.emit(
                self.index(0), self.index(len(self._ids) - 1), [ROLE_IS_SELECTED]
            )
        self._announced_selection = self._visible_selection()
        self.selectionChanged.emit()

    def _visible_selection(self) -> frozenset[int]:
        """The selected uids that are actually rows of this list.

        Queue and history read their selection through to ``QueueManager``, so
        the store can legitimately answer with uids this particular list does
        not hold. Intersecting keeps the comparison about what is on screen.
        """
        return frozenset(self._selection.selected() & set(self._uids))

    def refresh_selection(self) -> None:
        """Emit ``selectionChanged`` if the selection has moved since it was
        last announced.

        Not every selection change comes through the slots above. The queue
        drops a row's selection inside ``QueueManager`` as the row leaves the
        list — before this model is told anything happened — so a before/after
        pair taken around the mutation would see no change at all. Hence the
        remembered snapshot: whoever finished mutating the list calls this, and
        the comparison still holds however long ago the store actually changed.

        Call it *after* the closing ``endRemoveRows``/``endResetModel``, never
        from inside the bracket: a listener will read ``rowCount()`` back.
        """
        current = self._visible_selection()
        if current == self._announced_selection:
            return
        self._announced_selection = current
        self.selectionChanged.emit()

    # ------------------------------------------------------------------
    # Introspection (tests, diagnostics)
    # ------------------------------------------------------------------

    @property
    def demanded_keys(self) -> frozenset[str]:
        """The fields something has actually tried to read on this model."""
        return frozenset(self._demanded_keys)

    def flush_now(self) -> None:
        """Run the pending fetch immediately instead of waiting for the timer."""
        self._flush_timer.stop()
        self._flush()


def field_exists(key: str) -> bool:
    """True if *key* names a field in the catalog."""
    return key in FIELDS_BY_KEY
