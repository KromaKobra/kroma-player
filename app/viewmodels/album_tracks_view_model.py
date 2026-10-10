"""Album-tracks ViewModel — the tracks of the one album currently open.

The album page needs a list model of its own where the artist page does not, and
the reason is the running order. An artist page is a declarative ``TrackQuery``
filtered on ``artistId``, because "this artist's tracks" is a set with no order
of its own — any sort the catalog offers will do. An album's order **is** a fact
about the album, stored in ``track_albums.position``, and a ``TrackQuery`` cannot
express it: sorting goes through the field catalog, and a running order is not a
field.

So this model, which is structurally a copy of
:class:`~app.viewmodels.playlist_tracks_view_model.PlaylistTracksViewModel` — the
other list in the application whose order is stored rather than derived. In
particular ``moveRows`` and ``removeAtRows`` are named and shaped exactly as that
model's are, because ``TrackTable.qml`` looks both of them up **by name** before
it will let a row be dragged by its number. Renaming either one silently turns
the drag off.

Lifecycle:
- QML calls ``load(album_id)`` when the user opens an album.
- The header binds ``albumName`` / ``trackCount``.
- Dragging a row by its number calls ``moveRows``; dragging one out to the right
  calls ``removeAtRows``, which takes it off the album and not out of the library.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Optional

from PySide6.QtCore import Property, QModelIndex, Signal, Slot

from app.events.event_bus import EventBus
from app.library.list_model import TrackListModel
from app.library.row_cache import TrackRowCache
from app.services.album_service import AlbumService


class AlbumTracksViewModel(TrackListModel):
    """Exposes one album's tracks, in its running order, reorderable."""

    album_changed = Signal()

    def __init__(
        self,
        album_service: AlbumService,
        event_bus: EventBus,
        row_cache: Optional[TrackRowCache] = None,
        parent=None,
    ) -> None:
        super().__init__(row_cache, None, parent)
        self._album_service = album_service

        self._album_id: int = -1
        self._album_name: str = ""
        # A track appears at most once on an album, so a row's uid can simply be
        # its track id — the same bargain the playlist model makes.

        self._own_write: bool = False

        event_bus.subscribe("track.deleted", self._on_track_deleted)
        event_bus.subscribe("album.updated", self._on_album_updated)
        event_bus.subscribe("album.reordered", self._on_reordered)
        event_bus.subscribe("album.deleted", self._on_album_deleted)

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    @contextmanager
    def _mutating(self):
        """Ignore this model's own echo off the event bus.

        Load-bearing rather than an optimisation, for the reason
        ``PlaylistTracksViewModel._mutating`` sets out at length: ``EventBus.emit``
        is a plain call, so a service call made inside a ``beginRemoveRows``
        bracket re-enters this model and opens a ``beginResetModel`` in the
        middle of a removal that has not been closed. Qt cannot make sense of
        that.
        """
        self._own_write = True
        try:
            yield
        finally:
            self._own_write = False

    # ------------------------------------------------------------------
    # Properties exposed to QML
    # ------------------------------------------------------------------

    @Property(int, notify=album_changed)
    def albumId(self) -> int:  # noqa: N802
        return self._album_id

    @Property(str, notify=album_changed)
    def albumName(self) -> str:  # noqa: N802
        return self._album_name

    @Property(int, notify=album_changed)
    def trackCount(self) -> int:  # noqa: N802
        return len(self._ids)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @Slot(int)
    def load(self, album_id: int) -> None:
        """Open an album."""
        self._album_id = int(album_id)
        album = self._album_service.get(self._album_id)
        self._album_name = album.name if album else ""
        self._reload_ids()

    @Slot()
    def reload(self) -> None:
        """Re-read the current album's ordering after an external change."""
        if self._album_id < 0:
            return
        self._reload_ids()

    def _reload_ids(self) -> None:
        ids = (
            self._album_service.album_repo.track_ids(self._album_id)
            if self._album_id >= 0
            else []
        )
        self.set_ids(ids)
        self.album_changed.emit()

    # ------------------------------------------------------------------
    # Removal from the album
    # ------------------------------------------------------------------

    @Slot()
    def removeSelectedFromAlbum(self) -> None:  # noqa: N802
        """Take every selected row off this album — not out of the library."""
        self.removeAtRows(self.selectedRows())

    @Slot("QVariantList")
    def removeAtRows(self, rows: list) -> None:  # noqa: N802
        """Take a set of rows off the album, in the database and here.

        The second half of the number-column gesture: dragged up or down a row
        reorders, dragged out to the right it leaves the album. The name is
        fixed by ``TrackTable.qml``, which looks for it before arming the drag.

        Surgical rather than a re-read, for the reason the playlist model gives:
        a reset reaches the GUI as "the list is now something else", losing the
        scroll position and giving the departing row nothing to animate.
        """
        if self._album_id < 0:
            return
        indices = sorted({int(r) for r in rows if 0 <= int(r) < len(self._ids)})
        if not indices:
            return

        runs: list[tuple[int, int]] = []
        run_end = run_start = indices[-1]
        for index in reversed(indices[:-1]):
            if index == run_start - 1:
                run_start = index
            else:
                runs.append((run_start, run_end))
                run_end = run_start = index
        runs.append((run_start, run_end))

        doomed = [self._ids[index] for index in indices]
        with self._mutating():
            self._album_service.remove_tracks(self._album_id, doomed)

            for start, end in runs:
                self.beginRemoveRows(QModelIndex(), start, end)
                remaining = self._ids[:start] + self._ids[end + 1:]
                self._set_vectors(remaining, remaining)
                self.endRemoveRows()

        self.refresh_selection()
        self.album_changed.emit()

    # ------------------------------------------------------------------
    # Reordering
    # ------------------------------------------------------------------

    def _move_row(self, from_index: int, to_index: int) -> None:
        """Move one row in this model only, leaving the database alone.

        The guards are not decoration: Qt answers ``beginMoveRows`` with False
        when the destination is the row's own position, leaving the
        ``endMoveRows`` after it unbalanced, and ``moveRows`` below expects
        members already in place to cost nothing.
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
            self._album_service.set_order(self._album_id, list(self._ids))
        # After the write, never during. ``rowsMoved`` has already gone out by
        # the time this runs — Qt needs it around each individual move — and
        # anything that answers a *question about the album* rather than about a
        # row must not be asked while the order it would read is still the old
        # one. The album page's contributing artists are exactly that: their
        # tie-break is the running order, so a listener woken by ``rowsMoved``
        # re-reads the database a moment before the drag reaches it.
        self.album_changed.emit()

    @Slot(int, int)
    def moveTrack(self, from_index: int, to_index: int) -> None:  # noqa: N802
        """Move one row, and write the new ordering. A no-op call costs nothing."""
        if self._album_id < 0:
            return
        before = list(self._ids)
        self._move_row(int(from_index), int(to_index))
        if self._ids != before:
            self._persist_order()

    @Slot("QVariantList", int)
    def moveRows(self, rows: list, to: int) -> None:  # noqa: N802
        """Move a set of rows so they land contiguously at *to*, in their own order.

        *to* is an index into the album **with the moved rows already taken
        out** — the only phrasing that stays unambiguous when the selection
        straddles the drop point. The two-phase walk is the playlist model's,
        verbatim, and for the same reason: sending each member straight to its
        final slot has one that starts above the destination travel down through
        the slots its own block-mates already occupy, shoving them out on the way
        past.

        One write, at the end. The walk moves a row up to twice, and each of
        those is an in-memory list operation; the order that matters is the one
        left when it stops.
        """
        if self._album_id < 0:
            return
        wanted = sorted({int(r) for r in rows if 0 <= int(r) < len(self._ids)})
        if not wanted:
            return

        before = list(self._ids)
        ids = [self._ids[row] for row in wanted]
        dest = max(0, min(int(to), len(self._ids) - len(ids)))
        head = wanted[0]

        # Phase 1 — gather the block behind its first member, which does not move.
        for offset in range(1, len(ids)):
            self._move_row(self._ids.index(ids[offset]), head + offset)

        # Phase 2 — slide the contiguous block to its destination. Down has to go
        # back to front and up front to back.
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
        """This album's track ids, in running order."""
        return self.track_ids()

    # ------------------------------------------------------------------
    # Event bus handlers
    # ------------------------------------------------------------------

    def _on_track_deleted(self, track_id: int) -> None:
        self._cache.invalidate(track_id)
        if int(track_id) in self._ids:
            self._reload_ids()

    def _on_album_updated(self, album) -> None:
        """Membership or the name changed somewhere else.

        One handler for both because an album's `updated` covers each — and the
        header reads the name off this model, so a rename elsewhere would
        otherwise sit stale until the album was closed and reopened.
        """
        if self._own_write:
            return
        if album is None or int(getattr(album, "id", -1)) != self._album_id:
            return
        self._album_name = album.name or ""
        self._reload_ids()

    def _on_reordered(self, album_id: int) -> None:
        if int(album_id) == self._album_id and not self._own_write:
            self._reload_ids()

    def _on_album_deleted(self, album_id: int) -> None:
        if int(album_id) == self._album_id:
            self._album_id = -1
            self._album_name = ""
            self._reload_ids()
