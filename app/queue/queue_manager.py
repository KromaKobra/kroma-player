"""Queue management system.

The rules the whole app hangs off, stated once:

* The queue is resilient. A track leaves it only by being played through, by
  being skipped with the Next button, or by being removed by hand. Nothing
  leaves it by being *passed over* — jumping to the bottom of the queue plays
  that track and leaves everything above it exactly where it was.
* A track heard for less than the listen threshold never lands in history. If
  something else starts playing while it is still that young, it does not
  disappear: it drops to queue[1] and plays next.
* The one exception is the Next button (and a track reaching its end), which is
  a deliberate "done with this" and always records the play. The Previous
  button is the opposite exception: a rewind records nothing, however long the
  outgoing track played for.

Those sentences are implemented in exactly one place —
:meth:`QueueManager._displace_current` — and every navigation method routes
through it.

History doubles as the back-stack. Previous pulls history[0] back into the
queue and shifts everything else back one slot, so pressing Previous repeatedly
walks backwards through what was played while stacking the tracks it passes at
the front of the queue; pressing Next then walks straight back forward. That is
why there is no separate previous stack to keep in sync — and why Previous only
ever consumes history, never writes to it. Writing to it would put a track in
history *and* in the queue, and the next Previous would pull the same track
back out and queue it twice.
"""
from __future__ import annotations
from typing import Iterable, Optional
from dataclasses import dataclass


# Default cap for history. Overridable per-instance so a setting can drive it.
DEFAULT_MAX_HISTORY = 100


@dataclass
class QueueMutation:
    """
    Describes what changed after a playback navigation call so the ViewModel
    can emit the correct begin/end signals on BOTH models without re-reading
    the full list.

    Index fields are None when that list was not affected. A reset flag means
    the change was many-sided and is not worth describing surgically — cheap
    here, because the row cache is keyed by track id, so a reset repaints out
    of what is already loaded and costs no queries.
    """
    # Queue changes
    queue_removed_index:   Optional[int] = None
    queue_inserted_index:  Optional[int] = None
    queue_reset:           bool          = False

    # A reorder, as ``(from, to)`` steps to be applied in order — each one
    # indexed against the list the step before it left behind, and against the
    # removal above if there is one, which always goes first. Playing a row from
    # inside the queue is the only thing that sets this: rows travel to the
    # front and whatever was playing either drops in behind them or leaves. A
    # walk rather than a reset so a view can show which row went where.
    queue_moved:           Optional[list[tuple[int, int]]] = None

    # History changes. An insert is always at index 0 — history only grows at
    # the top — so it needs no index of its own.
    history_inserted:      bool          = False
    history_removed_index: Optional[int] = None
    history_reset:         bool          = False


class QueueManager:
    """
    Manages both the playback queue and play history.

    Queue:   ordered list, index 0 is now-playing.
    History: reverse-chronological stack, index 0 is most-recently played, and
             also the back-stack the Previous button pops from.

    Both store the same entry shape:
        { "id": int, "uid": int, "sourceType"?: str, "sourceId"?: int }

    Optional collection provenance travels with the entry across both lists.

    All navigation methods return a QueueMutation so the ViewModel layer can
    emit surgical Qt model signals without polling the lists.
    """

    def __init__(self, max_history: int = DEFAULT_MAX_HISTORY) -> None:
        self._queue:    list[dict] = []
        self._history:  list[dict] = []
        self._max_history: int = max(1, int(max_history))
        # Selection is per-view and fully independent: the queue view and the
        # history view each keep their own set of selected row uids. A uid is
        # never shared between the two sets, so selecting/deselecting in one
        # view never affects the other.
        self._queue_selected: set[int] = set()    # uids selected in the queue view
        self._history_selected: set[int] = set()  # uids selected in the history view
        self._next_uid: int = 0                   # monotonic source of per-row instance ids

    @property
    def max_history(self) -> int:
        return self._max_history

    @max_history.setter
    def max_history(self, value: int) -> None:
        self._max_history = max(1, int(value))
        self._cap_list(self._history, self._max_history)

    def _selection_set(self, history: bool) -> set[int]:
        """Return the selection set for the requested view."""
        return self._history_selected if history else self._queue_selected

    def _tag(self, entry: dict) -> dict:
        """
        Stamp an entry with a unique per-instance ``uid`` so that two copies of
        the same track (identical ``id``) remain distinguishable for selection.

        Unlike a positional index, a uid is stable across reordering and is
        unaffected by inserts/removals elsewhere in the list, so the selection
        set never has to be rewritten when the queue mutates. Internal moves
        reuse the existing uid; only entries entering a list from outside are
        tagged here.
        """
        entry["uid"] = self._next_uid
        self._next_uid += 1
        return entry

    def _fresh(self, entry: dict) -> dict:
        """
        Return a shallow copy of ``entry`` stamped with a brand-new uid.

        Entries move between the queue and history as shared object references.
        If the same object were placed in both lists it would carry the same uid
        into both rows, so selecting one row would select its twin. Copying on
        cross-list moves guarantees every rendered row is an independent
        instance with its own uid.
        """
        return self._tag(dict(entry))

    # ------------------------------------------------------------------
    # Read access
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._queue)

    def current(self) -> Optional[dict]:
        return self._queue[0] if self._queue else None

    def queue_entries(self) -> list[dict]:
        """The queue, in order. Read-only view for models mirroring the list."""
        return self._queue

    def history_entries(self) -> list[dict]:
        """History, newest first. Read-only view for models mirroring the list."""
        return self._history

    # ------------------------------------------------------------------
    # Queue and history editing — no playback semantics
    # ------------------------------------------------------------------

    def set_queue(self, entries: Iterable[dict]) -> None:
        self._queue = [self._tag(dict(e)) for e in entries]
        self._queue_selected.clear()

    def set_history(self, entries: Iterable[dict]) -> None:
        """Replace history wholesale — session restore, or clearing it."""
        self._history = [self._tag(dict(e)) for e in entries]
        self._cap_list(self._history, self._max_history)
        self._history_selected.clear()

    def insert_tracks(
        self, entries: Iterable[dict], index: Optional[int] = None
    ) -> Optional[tuple[int, int]]:
        """
        Insert entries into the queue as one block, preserving their order.

        ``index`` None appends; ``index`` 1 is "play next". Returns the
        (first_row, last_row) range inserted, or None if there was nothing to
        insert, so the caller can bracket a single pair of Qt row signals
        instead of one per track.
        """
        tagged = [self._tag(dict(e)) for e in entries]
        if not tagged:
            return None

        first = len(self._queue) if index is None else max(0, min(int(index), len(self._queue)))
        self._queue[first:first] = tagged
        return first, first + len(tagged) - 1

    def remove_at(self, index: int) -> Optional[dict]:
        if 0 <= index < len(self._queue):
            entry = self._queue.pop(index)
            self._queue_selected.discard(entry["uid"])
            return entry
        return None

    def remove_from_history(self, index: int) -> Optional[dict]:
        """Drop one history row and its selection.

        History has no user-facing removal, but a deleted track has to leave
        every list that mentions it.
        """
        if not 0 <= index < len(self._history):
            return None
        entry = self._history.pop(index)
        self._history_selected.discard(entry.get("uid"))
        return entry

    def move(self, from_index: int, to_index: int) -> None:
        if not (0 <= from_index < len(self._queue)):
            return
        entry = self._queue.pop(from_index)
        self._queue.insert(max(0, min(to_index, len(self._queue))), entry)

    # ------------------------------------------------------------------
    # The policy
    # ------------------------------------------------------------------

    def _displace_current(self, listened: bool) -> tuple[Optional[dict], bool]:
        """
        Pop queue[0] and route it per the resilience policy.

        Returns ``(entry, went_to_history)``. A listened track is recorded and
        gone; an un-listened one is handed straight back so the caller can
        re-seat it at queue[1] — it was never really heard, so it must not
        leave the queue. ``entry`` is None only when the queue was empty.

        :meth:`previous` always passes False: a rewind never records a play,
        whatever the playhead said.
        """
        if not self._queue:
            return None, False

        current = self._queue.pop(0)
        # Leaving the queue: drop its queue-view selection. It is NOT carried
        # into the history-view selection.
        self._queue_selected.discard(current["uid"])

        if not listened:
            return current, False
        return current, self._history_prepend(current)

    # ------------------------------------------------------------------
    # Playback navigation — all return QueueMutation
    # ------------------------------------------------------------------

    def advance(self) -> QueueMutation:
        """
        Move the current track to the top of history; queue[1] becomes queue[0].

        Called when a track finishes or the user presses Next. Both are a
        deliberate "done with this one", so the play is recorded however short
        it was — this is the single exception to the listen threshold.
        """
        if not self._queue:
            return QueueMutation()

        _, history_inserted = self._displace_current(listened=True)
        return QueueMutation(
            queue_removed_index=0,
            # Only signal a history insert if one actually happened — a
            # duplicate of the current top is discarded, so the view must not
            # move.
            history_inserted=history_inserted,
        )

    def play_at(self, indices: Iterable[int], listened: bool) -> QueueMutation:
        """
        Play the given queue rows: they move to the front in the order given,
        and the first of them becomes queue[0].

        Everything not named stays in the queue, in order. Nothing is skipped
        into history — that is the whole point.
        """
        rows = sorted({i for i in indices if 0 <= i < len(self._queue)})
        if not rows:
            return QueueMutation()

        if rows == [0]:
            # Already at the front. Replaying it is a playback concern, not a
            # queue one, so there is nothing to report.
            return QueueMutation()

        # Pull the targets out first so the surviving rows keep their order,
        # then decide what happens to whatever was playing.
        targets = [self._queue[i] for i in rows]
        for i in reversed(rows):
            self._queue.pop(i)

        displaced = None
        history_inserted = False
        if 0 not in rows:
            displaced, history_inserted = self._displace_current(listened)

        self._queue[0:0] = targets
        if displaced is not None and not history_inserted:
            # Never really heard — it drops in behind the new current track
            # rather than being thrown away.
            self._queue.insert(len(targets), displaced)

        # Say what happened rather than that everything did. Nothing here is
        # created or destroyed except the track that was playing, and only when
        # it was listened to; the rest is the same rows in a different order, so
        # a reset would throw away the one thing a view could show — which row
        # came to the front, and from where.
        #
        # Order matters, and the removal goes first: a track that is leaving
        # should be seen leaving from where it was, not shuffled down a slot and
        # then taken out of the middle.
        dropped = 1 if (displaced is not None and history_inserted) else 0

        # Each target lands at its own position in the block. `rows` is
        # ascending, so a target is never disturbed by the ones moved before it
        # — they all started above it and end above it — and its index only has
        # to be adjusted for the removal that has already happened.
        moves = [(row - dropped, slot) for slot, row in enumerate(rows)]
        moves = [(src, dst) for src, dst in moves if src != dst]

        if not dropped and not moves:
            # The rows named were already at the front, in the order they were
            # already in. Nothing left the queue either, or `dropped` would say
            # so, and nothing reached history without something leaving.
            return QueueMutation()

        return QueueMutation(
            queue_removed_index=0 if dropped else None,
            queue_moved=moves,
            history_inserted=history_inserted,
        )

    def play_entries(self, entries: Iterable[dict], listened: bool) -> QueueMutation:
        """
        Play tracks that are not already queue rows — a library, search,
        playlist or history row. They are inserted at the front in order and
        the first one becomes queue[0].

        The source row is left alone: playing a history row is a new play, not
        a rewind, so history keeps its record of the earlier one.
        """
        tagged = [self._tag(dict(e)) for e in entries]
        if not tagged:
            return QueueMutation()

        displaced, history_inserted = self._displace_current(listened)

        self._queue[0:0] = tagged
        if displaced is not None and not history_inserted:
            self._queue.insert(len(tagged), displaced)

        # A single insert onto an empty-or-untouched queue is describable;
        # anything else moved two things at once.
        if displaced is None and len(tagged) == 1:
            return QueueMutation(queue_inserted_index=0, history_inserted=history_inserted)
        return QueueMutation(queue_reset=True, history_inserted=history_inserted)

    def previous(self) -> QueueMutation:
        """
        Rewind one step: history[0] comes back out of history and becomes
        queue[0], and everything else shifts back one slot.

        This is the only place a history row is consumed, and it is the right
        one — Previous undoes a play, so that play stops being in the past.
        Nothing is written to history here: a rewind is not "done with this
        one", so the outgoing track is not recorded and does not leave. It
        lands at queue[1], which is what makes Next walk straight back forward
        again.
        """
        if not self._history:
            return QueueMutation()

        previous_track = self._history.pop(0)
        self._history_selected.discard(previous_track.get("uid"))

        # ``listened=False`` is the statement that this is a rewind. The
        # outgoing track is not finished with — it is the very next thing
        # ahead of the listener — so it comes straight back however long it
        # played for, and its play is not recorded. Were it recorded, the next
        # Previous would pull that same track back out of history and the
        # queue would hold it twice.
        displaced, _ = self._displace_current(listened=False)

        # Re-entering the queue: a fresh uid so this row is a distinct instance
        # from any copy history still holds.
        self._queue.insert(0, self._fresh(previous_track))
        if displaced is not None:
            self._queue.insert(1, displaced)

        # Always one row arriving at the front and nothing else moving. Either
        # nothing was playing, or the current track came straight back in
        # behind the row pulled out of history — same uid, one slot lower, so
        # as far as the queue is concerned it never left. Saying so rather than
        # resetting is what lets a view show the arrival; a reset tells it only
        # that everything is different now.
        return QueueMutation(queue_inserted_index=0, history_removed_index=0)

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _history_prepend(self, entry: dict) -> bool:
        """
        Prepend a fresh-uid copy of ``entry`` to history unless it duplicates
        the current top, in which case it is discarded. Returns True only if a
        row was actually inserted (False when discarded as a duplicate).

        Inserting a copy (rather than the original object) keeps every history
        row a distinct instance — see :meth:`_fresh`.
        """
        if self._history and self._history[0]["id"] == entry["id"]:
            return False
        self._history.insert(0, self._fresh(entry))
        self._cap_list(self._history, self._max_history)
        return True

    @staticmethod
    def _cap_list(lst: list[dict], max_size: int) -> None:
        """Remove items from the end of the list if it exceeds max_size."""
        while len(lst) > max_size:
            lst.pop()

    # ------------------------------------------------------------------
    # Selection
    #
    # Every method takes a ``history`` flag selecting which view's set to act
    # on, so the queue and history views are managed independently. ``history``
    # defaults to False (queue) since that is the common case.
    # ------------------------------------------------------------------

    def set_selected(self, uid: int, selected: bool, history: bool = False) -> None:
        """Toggle selection for a single row (additive to existing selection)."""
        sel = self._selection_set(history)
        if selected:
            sel.add(uid)
        else:
            sel.discard(uid)

    def set_selected_exclusive(self, uid: int, history: bool = False) -> None:
        """Set this row as the only selected row (clear all others)."""
        sel = self._selection_set(history)
        sel.clear()
        sel.add(uid)

    def set_selected_multiple(self, uids: set[int], history: bool = False) -> None:
        """Replace entire selection with the given set of row uids."""
        if history:
            self._history_selected = set(uids)
        else:
            self._queue_selected = set(uids)

    def is_selected(self, uid: int, history: bool = False) -> bool:
        return uid in self._selection_set(history)

    def clear_selection(self, history: bool = False) -> None:
        self._selection_set(history).clear()

    def selected_ids(self, history: bool = False) -> frozenset[int]:
        """Currently selected row uids (not track/content ids) for the view."""
        return frozenset(self._selection_set(history))
