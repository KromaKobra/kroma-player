"""Queue ViewModel — the play queue as a track list model.

The queue is one of the two lists the backend genuinely owns: it is playback
state, not a screen. So unlike a library query, which a GUI declares for
itself, this stays a named object.

What it is *not* any more is a bespoke list model. It mirrors
``QueueManager``'s ordering into an id vector and lets the shared row cache
resolve the fields — so a queue row costs the same as a search row, and a track
sitting in both is read once.
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QModelIndex, Signal, Slot

from app.events.event_bus import EventBus
from app.library.list_model import TrackListModel
from app.library.row_cache import TrackRowCache
from app.library.selection import QueueManagerSelection
from app.queue.queue_manager import QueueManager

logger = logging.getLogger(__name__)


class QueueViewModel(TrackListModel):
    """Exposes QueueManager's queue to QML.

    Row order lives in the manager; this model mirrors it. Every mutation
    brackets the manager call in the matching Qt row signals, so reordering or
    removing stays surgical rather than resetting the whole view.
    """

    # The row about to leave because playback moved on, not because anyone asked
    # for it. Carries the uid rather than the row so the GUI can still tell which
    # one it meant after the indices have shifted under it — the same reason the
    # track list keys everything else on uid.
    #
    # Emitted *before* the row goes, since `uidAt` cannot answer afterwards.
    advance_removed = Signal(int)  # uid

    def __init__(
        self,
        queue_manager: QueueManager,
        event_bus: EventBus,
        row_cache: Optional[TrackRowCache] = None,
        parent=None,
    ) -> None:
        super().__init__(row_cache, QueueManagerSelection(queue_manager, history=False), parent)
        self._mgr = queue_manager
        event_bus.subscribe("track.deleted", self._on_track_deleted)
        self._pull()

    # ------------------------------------------------------------------
    # Mirroring the manager
    # ------------------------------------------------------------------

    def _pull(self) -> None:
        """Re-read the manager's ordering into the id vector.

        Always called *inside* a begin/end pair, so Qt sees the new contents at
        the moment it expects them.
        """
        entries = self._mgr.queue_entries()
        self._set_vectors(
            [e.get("id") for e in entries],
            [e.get("uid", -1) for e in entries],
        )

    def _row_of_uid(self, uid: int) -> int:
        for row, entry in enumerate(self._mgr.queue_entries()):
            if entry.get("uid") == uid:
                return row
        return -1

    # ------------------------------------------------------------------
    # Slots called from QML
    # ------------------------------------------------------------------

    @Slot(int)
    def removeAt(self, index: int) -> None:  # noqa: N802
        if not 0 <= index < len(self._ids):
            return
        self.beginRemoveRows(QModelIndex(), index, index)
        self._mgr.remove_at(index)
        self._pull()
        self.endRemoveRows()
        # The manager discarded the row's selection on the way out.
        self.refresh_selection()

    @Slot(int)
    def removeByUid(self, uid: int) -> None:  # noqa: N802
        row = self._row_of_uid(uid)
        if row >= 0:
            self.removeAt(row)

    @Slot("QVariantList")
    def insertNext(self, track_ids: list) -> None:  # noqa: N802
        """Insert tracks at queue position 1, so they play after the current one.

        The whole selection goes in as one ordered block: right-clicking rows 4
        and 8 and choosing Play next puts 4 in slot 1 and 8 in slot 2. Doing it
        as a block rather than a loop of single inserts is both the correct
        ordering and one pair of row signals instead of N.
        """
        self._insert({"id": int(t)} for t in track_ids)

    @Slot("QVariantList")
    def append_track_ids(self, track_ids: list) -> None:
        """Append tracks by id to the end of the queue."""
        self._insert(({"id": int(t)} for t in track_ids), index=None)

    @Slot("QVariantList")
    def insertNextEntries(self, entries: list) -> None:
        self._insert(entries)

    @Slot("QVariantList")
    def appendEntries(self, entries: list) -> None:
        self._insert(entries, index=None)

    def _insert(self, entries, index: int = 1) -> None:
        """Insert a block of entries, bracketed in a single row signal pair.

        The manager reports the range it actually used, so a queue shorter than
        *index* still lands somewhere sensible instead of being guessed at here.
        """
        pending = list(entries)
        if not pending:
            return
        first = len(self._ids) if index is None else min(index, len(self._ids))
        self.beginInsertRows(QModelIndex(), first, first + len(pending) - 1)
        self._mgr.insert_tracks(pending, index)
        self._pull()
        self.endInsertRows()

    @Slot()
    def removeSelected(self) -> None:  # noqa: N802
        """Remove every selected row."""
        self.removeAtRows(self.selectedRows())

    @Slot("QVariantList")
    def removeAtRows(self, rows: list) -> None:  # noqa: N802
        """Remove a set of rows, grouping contiguous runs into single signals.

        Takes the rows rather than reading the selection, because the gesture
        that reaches this does not always agree with it: a queue row dragged off
        by its grip carries the whole selection when it is part of one, and
        carries only itself when it is not — leaving the selection undisturbed
        either way.

        Named to pair with ``removeAt`` above, and to stay clear of
        ``QAbstractItemModel.removeRows(row, count, parent)``, which is a base
        virtual and means something else.
        """
        indices = sorted({int(r) for r in rows if 0 <= int(r) < len(self._ids)})
        if not indices:
            return

        # Descending, so each run's indices are still valid as the ones below
        # it are removed.
        runs: list[tuple[int, int]] = []
        run_end = run_start = indices[-1]
        for index in reversed(indices[:-1]):
            if index == run_start - 1:
                run_start = index
            else:
                runs.append((run_start, run_end))
                run_end = run_start = index
        runs.append((run_start, run_end))

        for start, end in runs:
            self.beginRemoveRows(QModelIndex(), start, end)
            for row in range(end, start - 1, -1):
                self._mgr.remove_at(row)
            self._pull()
            self.endRemoveRows()

        # Once, not per run: the selection is empty either way and the GUI has
        # no use for the intermediate states.
        self.refresh_selection()

    @Slot()
    def clearAll(self) -> None:  # noqa: N802
        """Empty the queue in one go.

        A reset rather than a walk down ``removeAt``: emptying the queue is one
        act, and N row-removal signals make the GUI animate it as N departures.
        The manager's ``set_queue`` drops the selection on the way through, so
        there is nothing left over to reconcile.
        """
        if not self._ids:
            return
        self.beginResetModel()
        self._mgr.set_queue([])
        self._pull()
        self.endResetModel()
        self.refresh_selection()

    @Slot(int, int)
    def moveTrack(self, from_index: int, to_index: int) -> None:  # noqa: N802
        """Move one row, or do nothing at all — quietly.

        The guards are not decoration. Qt refuses a move whose destination is
        the row's own position and answers ``beginMoveRows`` with False, which
        leaves the ``endMoveRows`` after it unbalanced; and a source outside the
        list would have the manager silently decline while Qt was told a move
        had happened. Both are reachable from ``moveRows`` below, which drops a
        block wherever the pointer left it and expects the members already in
        place to cost nothing.
        """
        last = len(self._ids) - 1
        if not 0 <= from_index <= last:
            return
        to_index = max(0, min(int(to_index), last))
        if to_index == from_index:
            return

        destination = to_index + 1 if to_index > from_index else to_index
        self.beginMoveRows(QModelIndex(), from_index, from_index, QModelIndex(), destination)
        self._mgr.move(from_index, to_index)
        self._pull()
        self.endMoveRows()

    @Slot("QVariantList", int)
    def moveRows(self, rows: list, to: int) -> None:  # noqa: N802
        """Move a set of rows so they land contiguously at *to*, in their own order.

        *to* is an index into the queue **with the moved rows already taken
        out**. That is the only phrasing that stays unambiguous when the
        selection straddles the drop point: "row 4" means something different
        before and after three rows above it have been lifted, whereas "fourth
        of what is left" does not move under you.

        A non-contiguous selection cannot be one ``beginMoveRows``, and a model
        reset would cost the view its scroll position and every delegate. So
        this walks the block into place instead, one real move at a time — each
        intermediate list is a valid model state, which is what lets a view
        animate through it rather than blink.

        Row numbers stop meaning anything after the first move; uids do not, so
        the block is captured as uids up front and each member is looked up
        again as its turn comes.

        It goes in two phases because sending each member straight to its final
        slot does not work: one that starts above the destination has to travel
        down *through* the slots its own block-mates already occupy, and shoves
        them back out on the way past. Gathering first and sliding after means
        no move ever steps over a member that is already placed. At most two
        moves per member, against a span-long walk for the naive version.
        """
        wanted = sorted({int(r) for r in rows if 0 <= int(r) < len(self._ids)})
        if not wanted:
            return

        uids = [self._uids[row] for row in wanted]
        dest = max(0, min(int(to), len(self._ids) - len(uids)))
        head = wanted[0]

        # Phase 1 — gather the block behind its first member, which does not
        # move. Every step here is an upward move over rows that are not in the
        # block, so nothing already gathered is disturbed.
        for offset in range(1, len(uids)):
            self.moveTrack(self._row_of_uid(uids[offset]), head + offset)

        # Phase 2 — slide the contiguous block to its destination. Down has to
        # go back to front and up front to back; either one the other way round
        # walks a member over one already in place. A block that is already
        # home costs nothing — moveTrack declines a move to where a row is.
        order = range(len(uids) - 1, -1, -1) if dest > head else range(len(uids))
        for offset in order:
            self.moveTrack(self._row_of_uid(uids[offset]), dest + offset)

    # ------------------------------------------------------------------
    # Mutation notification (called by PlayerViewModel after navigation)
    # ------------------------------------------------------------------

    def notify_removed(self, index: int) -> None:
        """A row left because playback moved past it.

        Reached from ``PlayerViewModel._apply_mutation`` on the advance path.
        Saying that out loud is what lets the GUI give a track that played
        through a different exit from one the user took out by hand.
        """
        uid = self.uidAt(index)
        if uid is not None:
            self.advance_removed.emit(int(uid))
        self.beginRemoveRows(QModelIndex(), index, index)
        self._pull()
        self.endRemoveRows()

    def notify_played(self, removed_index: Optional[int], moves: list) -> None:
        """A row was played from inside the queue: it travels to the front.

        The manager has already done all of it, and what arrives here is the
        walk it took — see ``QueueMutation.queue_moved``. Each step is announced
        separately so a view can follow the rows rather than rebuild them, which
        is the whole reason this is not a reset.

        Unlike the other notifications this cannot lean on ``_pull``: that reads
        the manager, which is at the *end* of the walk, and every step but the
        last needs the list as that step leaves it. So the vectors are carried
        along by hand and handed over a step at a time. They must land exactly
        where the manager is — ``tests/test_queue_playback.py`` is what holds
        that walk honest.
        """
        ids = list(self._ids)
        uids = list(self._uids)

        # First, because a track that is leaving should be seen leaving from
        # where it was rather than from wherever the reorder pushed it.
        if removed_index is not None and 0 <= removed_index < len(ids):
            self.advance_removed.emit(int(uids[removed_index]))
            self.beginRemoveRows(QModelIndex(), removed_index, removed_index)
            ids.pop(removed_index)
            uids.pop(removed_index)
            self._set_vectors(ids, uids)
            self.endRemoveRows()

        for src, dst in moves:
            src, dst = int(src), int(dst)
            if not 0 <= src < len(ids) or not 0 <= dst < len(ids) or src == dst:
                continue
            # Qt indexes the destination in the list *before* the row is lifted
            # out, so a downward move has to name the slot past its target — the
            # same adjustment ``moveTrack`` makes.
            destination = dst + 1 if dst > src else dst
            self.beginMoveRows(QModelIndex(), src, src, QModelIndex(), destination)
            ids.insert(dst, ids.pop(src))
            uids.insert(dst, uids.pop(src))
            self._set_vectors(ids, uids)
            self.endMoveRows()

        # The manager dropped the departing row's selection on the way past.
        self.refresh_selection()

    def notify_inserted(self, index: int) -> None:
        self.beginInsertRows(QModelIndex(), index, index)
        self._pull()
        self.endInsertRows()

    def notify_reset(self) -> None:
        self.beginResetModel()
        self._pull()
        self.endResetModel()

    # ------------------------------------------------------------------
    # Event bus handlers
    # ------------------------------------------------------------------

    def _on_track_deleted(self, track_id: int) -> None:
        """Drop every queue row for a track that has left the library.

        The queue can hold the same track more than once, so this removes all
        of them rather than stopping at the first.
        """
        self._cache.invalidate(track_id)
        while True:
            entries = self._mgr.queue_entries()
            row = next((i for i, e in enumerate(entries) if e.get("id") == track_id), -1)
            if row < 0:
                return
            self.removeAt(row)
