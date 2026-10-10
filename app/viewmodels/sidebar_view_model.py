"""Sidebar ViewModel — track-detail panel for the right sidebar.

Surfaces full metadata for either the currently-playing track or the active
view's selection. Data is pulled straight from the database via TrackService so
the panel always reflects the authoritative stored values, not the trimmed
``display_data`` that queue/search rows carry.

Display logic (see :meth:`_recompute`), in priority order:
  * 2+ tracks selected  -> multi-select summary (count, combined time + size)
  * exactly 1 selected  -> that track's full details
  * nothing selected    -> the currently-playing track, if any
  * otherwise           -> empty state

QML drives selection through the single :meth:`set_selection` slot; the playing
track is observed automatically via the PlayerViewModel's change signal.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from PySide6.QtCore import QObject, Property, Signal, Slot

from app.library.context import declared_id_kinds
from app.models.track import Track
from app.plugins import external_ids
from app.services.track_service import TrackService

logger = logging.getLogger(__name__)

# Display modes exposed to QML as the ``mode`` property.
MODE_EMPTY = "empty"
MODE_SINGLE = "single"
MODE_MULTI = "multi"

_BYTES_PER_MB = 1024 * 1024


def _file_size_bytes(track: Track) -> int:
    """Size of a track's backing Opus file, or 0 if it cannot be read."""
    try:
        return os.path.getsize(track.file_path)
    except OSError:
        return 0


def _fmt_duration(seconds: int) -> str:
    """Format seconds as M:SS, or H:MM:SS once it passes an hour."""
    if not seconds or seconds <= 0:
        return "—"
    hours, rem = divmod(int(seconds), 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _fmt_combined_duration(seconds: int) -> str:
    """Format a total as ``{h}h {m}m`` (hours dropped when under an hour)."""
    hours, rem = divmod(int(seconds), 3600)
    minutes = rem // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _fmt_size(num_bytes: int) -> str:
    """Format a byte count as megabytes with one decimal place."""
    if not num_bytes or num_bytes <= 0:
        return "—"
    return f"{num_bytes / _BYTES_PER_MB:.1f} MB"


def _fmt_date(value: Optional[int]) -> str:
    if not value:
        return "—"
    s = str(value)
    if len(s) == 8:
        yyyy, mm, dd = s[0:4], s[4:6], s[6:8]
        if mm == "00":
            return yyyy
        if dd == "00":
            return f"{int(mm)}/{yyyy}"
        return f"{int(mm)}/{int(dd)}/{yyyy}"
    return s


class SidebarViewModel(QObject):
    """Backs the right-sidebar track-info panel."""

    info_changed = Signal()
    active_changed = Signal()

    def __init__(self, track_service: TrackService, player_vm: QObject,
                 event_bus=None, parent=None) -> None:
        super().__init__(parent)
        self._track_service = track_service
        self._player_vm = player_vm

        self._selected_ids: list[int] = []
        self._mode: str = MODE_EMPTY
        self._track: Optional[Track] = None

        # Multi-select aggregates.
        self._count = 0
        self._combined_seconds = 0
        self._combined_bytes = 0

        # Whether the panel is expanded. While collapsed we skip every database
        # and file read and just remember that a refresh is owed (_dirty), then
        # catch up the moment the panel is shown again.
        self._active = False
        self._dirty = True

        # The playing track is the fallback shown when nothing is selected, so
        # re-evaluate whenever it changes.
        player_vm.current_track_changed.connect(self._on_inputs_changed)

        # An edit elsewhere in the app changes what this panel should say. The
        # panel holds the ORM object, so its attributes are already current —
        # what is missing is the notify that repaints the formatted strings.
        if event_bus is not None:
            event_bus.subscribe("track.updated", self._on_track_updated)
            # Which links there are depends on which plugins are on.
            event_bus.subscribe("plugins.changed", lambda *_: self.info_changed.emit())

    def _on_track_updated(self, track) -> None:
        if self._track is not None and track is not None and track.id == self._track.id:
            self._on_inputs_changed()

    # ------------------------------------------------------------------
    # Active / lazy-refresh control
    # ------------------------------------------------------------------

    @Property(bool, notify=active_changed)
    def active(self) -> bool:
        return self._active

    @active.setter
    def active(self, value: bool) -> None:
        value = bool(value)
        if value == self._active:
            return
        self._active = value
        # Catch up on any work deferred while the panel was collapsed.
        if value and self._dirty:
            self._recompute()
        self.active_changed.emit()

    def _on_inputs_changed(self) -> None:
        """Handle a selection or playing-track change.

        Recompute immediately when the panel is visible; otherwise just flag that
        a refresh is owed so we never touch the database/disk while collapsed.
        """
        if self._active:
            self._recompute()
        else:
            self._dirty = True

    # ------------------------------------------------------------------
    # QML entry point
    # ------------------------------------------------------------------

    @Slot("QVariantList")
    def setSelection(self, track_ids) -> None:  # noqa: N802  (Qt slot, camelCase for QML)
        """Replace the active view's selection (list of library track ids).

        Non-library rows (web-search results with no id yet) and duplicates are
        dropped; original order is preserved.
        """
        seen: set[int] = set()
        ids: list[int] = []
        for raw in track_ids:
            if raw is None:
                continue
            try:
                tid = int(raw)
            except (ValueError, TypeError):
                continue
            if tid < 0 or tid in seen:
                continue
            seen.add(tid)
            ids.append(tid)
        self._selected_ids = ids
        self._on_inputs_changed()

    # ------------------------------------------------------------------
    # Recompute / state builders
    # ------------------------------------------------------------------

    def _recompute(self) -> None:
        self._dirty = False
        if len(self._selected_ids) >= 2:
            self._build_multi()
        elif len(self._selected_ids) == 1:
            self._build_single(self._selected_ids[0])
        else:
            pid = self._player_vm.current_track_id
            if pid is not None and pid >= 0:
                self._build_single(pid)
            else:
                self._build_empty()
        self.info_changed.emit()

    def _build_empty(self) -> None:
        self._mode = MODE_EMPTY
        self._track = None

    def _build_single(self, track_id: int) -> None:
        track = self._track_service.get_track_by_id(track_id)
        if track is None:
            self._build_empty()
            return
        self._mode = MODE_SINGLE
        self._track = track

    def _build_multi(self) -> None:
        self._mode = MODE_MULTI
        self._track = None
        total_secs = 0
        total_bytes = 0
        resolved = 0
        for tid in self._selected_ids:
            track = self._track_service.get_track_by_id(tid)
            if track is None:
                continue
            resolved += 1
            total_secs += track.duration_seconds or 0
            total_bytes += _file_size_bytes(track)
        self._count = resolved
        self._combined_seconds = total_secs
        self._combined_bytes = total_bytes

    # ------------------------------------------------------------------
    # Properties — mode
    # ------------------------------------------------------------------

    @Property(str, notify=info_changed)
    def mode(self) -> str:
        return self._mode

    # ------------------------------------------------------------------
    # Properties — single track
    # ------------------------------------------------------------------

    @Property(int, notify=info_changed)
    def trackId(self) -> int:
        """The shown track's library id, or -1. Everything else here is a
        display string, so this is the only handle a caller has on the track
        itself — the details panel's Edit button needs it."""
        return int(self._track.id) if self._track else -1

    @Property(str, notify=info_changed)
    def coverHash(self) -> str:
        return self._track.cover_hash if (self._track and self._track.cover_hash) else ""

    @Property(str, notify=info_changed)
    def title(self) -> str:
        return self._track.title if self._track else ""

    @Property(str, notify=info_changed)
    def artists(self) -> str:
        return self._track.artist if self._track else ""

    @Property(str, notify=info_changed)
    def album(self) -> str:
        return (self._track.album or "") if self._track else ""

    @Property(str, notify=info_changed)
    def releaseDate(self) -> str:
        return _fmt_date(self._track.release_date) if self._track else ""

    @Property(str, notify=info_changed)
    def genres(self) -> str:
        if not self._track or not self._track.genres:
            return ""
        return ", ".join(g.name for g in self._track.genres)

    @Property(str, notify=info_changed)
    def key(self) -> str:
        return (self._track.musical_key or "") if self._track else ""

    @Property(str, notify=info_changed)
    def tempo(self) -> str:
        if self._track and self._track.bpm:
            return f"{self._track.bpm} BPM"
        return ""

    @Property(str, notify=info_changed)
    def duration(self) -> str:
        return _fmt_duration(self._track.duration_seconds) if self._track else ""

    @Property(str, notify=info_changed)
    def isrc(self) -> str:
        return (self._track.isrc or "") if self._track else ""

    @Property(str, notify=info_changed)
    def copyright(self) -> str:
        return (self._track.copyright_text or "") if self._track else ""

    # ---- external links ----

    @Property("QVariantList", notify=info_changed)
    def externalLinks(self) -> list:  # noqa: N802
        """Where this track's ids point: ``[{key, label, value, url}]``.

        One per recording id an enabled plugin declares for tracks, in
        declaration order; ``url`` is empty for a kind with no link. An
        instrumental's id is left out — it names where a second file came from,
        not the track. Empty with every plugin off — the ids are still on the
        track, and come back here with the plugin.
        """
        if not self._track:
            return []
        ids = external_ids.read(external_ids.TRACK, self._track)
        links = []
        for kind in declared_id_kinds():
            if getattr(kind, "entity", "") != external_ids.TRACK:
                continue
            if getattr(kind, "role", "recording") not in ("", "recording"):
                continue
            value = ids.get(kind.key)
            if not value:
                continue
            links.append({
                "key": kind.key,
                "label": kind.source_label or kind.label,
                "value": value,
                "url": kind.url_for(value),
            })
        return links

    # ---- bottom-right stack ----

    @Property(str, notify=info_changed)
    def localIdHex(self) -> str:
        return f"{self._track.id:04X}" if self._track else ""

    @Property(str, notify=info_changed)
    def fileSize(self) -> str:
        return _fmt_size(_file_size_bytes(self._track)) if self._track else ""

    @Property(str, notify=info_changed)
    def dateAdded(self) -> str:
        return _fmt_date(self._track.created_at) if self._track else ""

    # ------------------------------------------------------------------
    # Properties — multi-select
    # ------------------------------------------------------------------

    @Property(int, notify=info_changed)
    def selectionCount(self) -> int:
        return self._count

    @Property(str, notify=info_changed)
    def combinedDuration(self) -> str:
        return _fmt_combined_duration(self._combined_seconds)

    @Property(str, notify=info_changed)
    def combinedSize(self) -> str:
        return _fmt_size(self._combined_bytes)
