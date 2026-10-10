"""Playlist-tracks ViewModel — the tracks of the one playlist currently open.

Loading a playlist is now a single ``SELECT track_id ... ORDER BY position``:
the rows are ids, and the shared row cache fills the fields as they are
painted. Opening a 5,000-track playlist costs the same as opening an empty one.

Lifecycle:
- QML calls ``load(playlist_id)`` when the user opens a playlist.
- The header binds ``playlistName`` / ``trackCount``.
- The context menu calls ``removeSelectedFromPlaylist()``; the play button reads
  ``trackIds()`` and hands them to ``playerVM.replace_queue_and_play()``.
- It listens on the EventBus so the open playlist stays current when tracks are
  added or removed elsewhere, or leave the library entirely — and when the
  playlist itself is renamed or deleted.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Optional

from PySide6.QtCore import Property, QModelIndex, QObject, Signal, Slot

from app.events.event_bus import EventBus
from app.library.list_model import TrackListModel
from app.library.row_cache import TrackRowCache
from app.services.playlist_service import PlaylistService


class PlaylistTracksViewModel(TrackListModel):
    """Exposes one playlist's tracks, in order, with selection and removal."""

    # Notifies the header (name / count) and the play button (enabled state).
    playlist_changed = Signal()

    def __init__(
        self,
        playlist_service: PlaylistService,
        event_bus: EventBus,
        row_cache: Optional[TrackRowCache] = None,
        parent=None,
    ) -> None:
        super().__init__(row_cache, None, parent)
        self._playlist_service = playlist_service

        self._playlist_id: int = -1
        self._playlist_name: str = ""
        # Rows in a playlist are unique, so a row's uid can simply be its track
        # id — there is no second copy for it to be confused with.

        # True while this model is the one doing the writing. See `_mutating`.
        self._own_write: bool = False

        event_bus.subscribe("track.deleted", self._on_track_deleted)
        event_bus.subscribe("playlist.track_added", self._on_membership_changed)
        event_bus.subscribe("playlist.track_removed", self._on_membership_changed)
        event_bus.subscribe("playlist.reordered", self._on_reordered)
        event_bus.subscribe("playlist.deleted", self._on_playlist_deleted)
        event_bus.subscribe("playlist.updated", self._on_playlist_renamed)

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    @contextmanager
    def _mutating(self):
        """Ignore this model's own echo off the event bus.

        ``EventBus.emit`` is a plain function call, and the service emits per
        track while removing them. So a service call made *inside* a
        ``beginRemoveRows`` bracket re-enters this model through
        ``_on_membership_changed`` and answers with ``_reload_ids`` — which is a
        ``beginResetModel``, opened in the middle of a removal that has not been
        closed yet. Qt has no way to make sense of that.

        This is therefore load-bearing rather than an optimisation: the guard is
        what lets the mutating slots below bracket their own work at all. The
        echo is safe to drop because it is describing a change this model has
        already made to itself, surgically, a moment earlier.
        """
        self._own_write = True
        try:
            yield
        finally:
            self._own_write = False

    # ------------------------------------------------------------------
    # Properties exposed to QML
    # ------------------------------------------------------------------

    @Property(int, notify=playlist_changed)
    def playlistId(self) -> int:  # noqa: N802
        return self._playlist_id

    @Property(str, notify=playlist_changed)
    def playlistName(self) -> str:  # noqa: N802
        return self._playlist_name

    @Property(int, notify=playlist_changed)
    def trackCount(self) -> int:  # noqa: N802
        return len(self._ids)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @Slot(int)
    def load(self, playlist_id: int) -> None:
        """Open a playlist."""
        self._playlist_id = int(playlist_id)
        playlist = self._playlist_service.get_playlist(self._playlist_id)
        self._playlist_name = playlist.name if playlist else ""
        self._reload_ids()

    @Slot()
    def reload(self) -> None:
        """Re-read the current playlist's ordering after an external change."""
        if self._playlist_id < 0:
            return
        self._reload_ids()

    def _reload_ids(self) -> None:
        ids = (
            self._playlist_service.get_track_ids(self._playlist_id)
            if self._playlist_id >= 0
            else []
        )
        self.set_ids(ids)
        self.playlist_changed.emit()

    # ------------------------------------------------------------------
    # Removal from playlist
    # ------------------------------------------------------------------

    @Slot()
    def removeSelectedFromPlaylist(self) -> None:  # noqa: N802
        """Remove every selected row from this playlist, in the database and here."""
        self.removeAtRows(self.selectedRows())

    @Slot("QVariantList")
    def removeAtRows(self, rows: list) -> None:  # noqa: N802
        """Remove a set of rows, grouping contiguous runs into single signals.

        Rows rather than the selection, because the gesture that reaches this
        does not always agree with it: a row dragged off by its grip carries the
        whole selection when it is part of one and only itself when it is not.
        The same slot and the same name as ``QueueViewModel``'s, because the drag
        looks for it by name.

        Surgical, not a re-read. ``_reload_ids`` would be shorter and is what
        this used to do, but a reset reaches the GUI as "the list is now
        something else" — every row rebuilt, the scroll position gone, and no
        departure to animate. Row signals are what let a removed row fly off the
        edge and the ones below it close up.
        """
        if self._playlist_id < 0:
            return
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

        # One transaction for the lot, before any of the row signals: a playlist
        # row's uid is its track id, so the ids are read off the rows while the
        # rows still mean something.
        doomed = [self._ids[index] for index in indices]
        with self._mutating():
            self._playlist_service.remove_tracks(self._playlist_id, doomed)

            for start, end in runs:
                self.beginRemoveRows(QModelIndex(), start, end)
                remaining = self._ids[:start] + self._ids[end + 1:]
                self._set_vectors(remaining, remaining)
                self.endRemoveRows()

        # Once, not per run, and outside the brackets — see `_set_vectors`.
        self.refresh_selection()
        self.playlist_changed.emit()

    # ------------------------------------------------------------------
    # Reordering
    # ------------------------------------------------------------------

    def _move_row(self, from_index: int, to_index: int) -> None:
        """Move one row in this model only, leaving the database alone.

        The guards are not decoration. Qt refuses a move whose destination is the
        row's own position and answers ``beginMoveRows`` with False, which leaves
        the ``endMoveRows`` after it unbalanced; both are reachable from
        ``moveRows`` below, which expects members already in place to cost
        nothing.

        Nothing is written here because ``moveRows`` walks this up to twice per
        row: the queue can afford to re-read its store every step because the
        store is a list in memory, and this one is SQLite. The order that
        matters is the one left at the end, and that is persisted once.
        """
        last = len(self._ids) - 1
        if not 0 <= from_index <= last:
            return
        to_index = max(0, min(int(to_index), last))
        if to_index == from_index:
            return

        destination = to_index + 1 if to_index > from_index else to_index
        self.beginMoveRows(
            QModelIndex(), from_index, from_index, QModelIndex(), destination
        )
        ids = list(self._ids)
        ids.insert(to_index, ids.pop(from_index))
        self._set_vectors(ids, ids)
        self.endMoveRows()

    def _persist_order(self) -> None:
        with self._mutating():
            self._playlist_service.set_order(self._playlist_id, list(self._ids))

    @Slot(int, int)
    def moveTrack(self, from_index: int, to_index: int) -> None:  # noqa: N802
        """Move one row, and write the new ordering. A no-op call costs nothing."""
        if self._playlist_id < 0:
            return
        before = list(self._ids)
        self._move_row(int(from_index), int(to_index))
        if self._ids != before:
            self._persist_order()

    @Slot("QVariantList", int)
    def moveRows(self, rows: list, to: int) -> None:  # noqa: N802
        """Move a set of rows so they land contiguously at *to*, in their own order.

        *to* is an index into the playlist **with the moved rows already taken
        out** — the only phrasing that stays unambiguous when the selection
        straddles the drop point. Same contract as ``QueueViewModel.moveRows``,
        and the same two-phase walk, for the same reason: sending each member
        straight to its final slot has one that starts above the destination
        travel down *through* the slots its own block-mates already occupy,
        shoving them back out on the way past.

        Row numbers stop meaning anything after the first move; track ids do not,
        and here a row's uid *is* its track id, so the block is captured as ids
        up front and each member is looked up again as its turn comes.
        """
        if self._playlist_id < 0:
            return
        wanted = sorted({int(r) for r in rows if 0 <= int(r) < len(self._ids)})
        if not wanted:
            return

        before = list(self._ids)
        ids = [self._ids[row] for row in wanted]
        dest = max(0, min(int(to), len(self._ids) - len(ids)))
        head = wanted[0]

        # Phase 1 — gather the block behind its first member, which does not
        # move. Every step here is an upward move over rows that are not in the
        # block, so nothing already gathered is disturbed.
        for offset in range(1, len(ids)):
            self._move_row(self._ids.index(ids[offset]), head + offset)

        # Phase 2 — slide the contiguous block to its destination. Down has to
        # go back to front and up front to back; either one the other way round
        # walks a member over one already in place.
        order = range(len(ids) - 1, -1, -1) if dest > head else range(len(ids))
        for offset in order:
            self._move_row(self._ids.index(ids[offset]), dest + offset)

        if self._ids != before:
            self._persist_order()

    # ------------------------------------------------------------------
    # Play-all support
    # ------------------------------------------------------------------

    @Slot(result="QVariantList")
    def trackIds(self) -> list:  # noqa: N802
        """This playlist's track ids, in order (for replace-queue-and-play)."""
        return self.track_ids()

    # ------------------------------------------------------------------
    # Event bus handlers
    # ------------------------------------------------------------------

    def _on_track_deleted(self, track_id: int) -> None:
        self._cache.invalidate(track_id)
        if int(track_id) in self._ids:
            self._reload_ids()

    def _on_membership_changed(self, playlist_id: int, _track_id: int) -> None:
        # `_own_write` is this model's own change coming back around — already
        # applied here, surgically, and a re-read would undo that. See `_mutating`.
        if playlist_id == self._playlist_id and not self._own_write:
            self._reload_ids()

    def _on_reordered(self, playlist_id: int) -> None:
        """Someone else rearranged the open playlist.

        Nothing joined or left, so this is a re-read of the ordering rather than
        of the membership — but there is no cheaper way to learn a new order than
        to ask for it, and nothing in the app currently reorders a playlist other
        than this model.
        """
        if playlist_id == self._playlist_id and not self._own_write:
            self._reload_ids()

    def _on_playlist_renamed(self, playlist_id: int) -> None:
        """A rename anywhere must reach the open playlist's header.

        The name is read once by load(); without this the header keeps showing
        the old one until the playlist is closed and reopened.
        """
        if playlist_id != self._playlist_id:
            return
        playlist = self._playlist_service.get_playlist(self._playlist_id)
        self._playlist_name = playlist.name if playlist else ""
        self.playlist_changed.emit()

    def _on_playlist_deleted(self, playlist_id: int) -> None:
        if playlist_id == self._playlist_id:
            self._playlist_id = -1
            self._playlist_name = ""
            self._reload_ids()
