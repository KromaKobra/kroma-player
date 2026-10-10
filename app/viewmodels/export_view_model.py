"""Exporting tracks out of the library — the model behind the export dialog.

One export at a time. Not a simplification: the destination is usually a single
external drive or a USB stick, where two concurrent writers make both slower,
and a second run into the same folder would race the first for filenames.

The view model does the database work itself, on the UI thread, before handing
off. That is one already-eager-loaded query, and it means the worker never
touches the session — which matters, because the app shares a single SQLAlchemy
session across everything.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import (
    Property,
    QObject,
    QThread,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QDesktopServices

from app.exporter.export import ExportError, ExportItem, ExportRequest, preflight
from app.exporter.formats import (
    EXPORT_FORMATS,
    FORMAT_LABELS,
    FORMAT_MP3,
    normalize,
)
from app.exporter.workers import ExportWorker
from app.services.playlist_service import PlaylistService
from app.services.track_service import TrackService
from app.settings.export import ExportSettings
from app.utils.binaries import ffmpeg_path
from app.viewmodels.worker_threads import own_worker

logger = logging.getLogger(__name__)


class ExportViewModel(QObject):
    """Configure and run an export, and report on it while it runs."""

    settings_changed = Signal()
    running_changed = Signal()
    progress_changed = Signal()
    error_occurred = Signal(str)
    export_finished = Signal(int, int, str)   # exported, skipped, destination

    def __init__(
        self,
        playlist_service: PlaylistService,
        track_service: TrackService,
        settings: ExportSettings | None = None,
        lyrics_service=None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._playlists = playlist_service
        self._tracks = track_service
        self._settings = settings or ExportSettings(None)
        # Read per item while the snapshot is built, on this thread. Optional:
        # without it the copies simply go out with no lyrics, as they did before
        # the table existed.
        self._lyrics = lyrics_service

        self._thread: Optional[QThread] = None
        self._worker: Optional[ExportWorker] = None

        self._stage: str = ""
        self._processed: int = 0
        self._total: int = 0

        self._last_exported: int = 0
        self._last_skipped: int = 0
        self._last_destination: str = ""

    # ==================================================================
    # Configuration
    # ==================================================================

    @Property("QVariantList", constant=True)
    def formats(self) -> List[str]:
        """The format keys, in the order the GUI should offer them."""
        return list(EXPORT_FORMATS)

    @Property("QVariantList", constant=True)
    def formatLabels(self) -> List[str]:
        """Display names for :attr:`formats`, index for index."""
        return [FORMAT_LABELS[f] for f in EXPORT_FORMATS]

    @Property(str, notify=settings_changed)
    def format(self) -> str:
        return self._settings.format

    @format.setter
    def format(self, value: str) -> None:
        value = normalize(value)
        if value == self._settings.format:
            return
        self._settings.format = value
        self.settings_changed.emit()

    @Property(str, notify=settings_changed)
    def destination(self) -> str:
        return self._settings.directory

    @destination.setter
    def destination(self, value: str) -> None:
        value = self._to_local_path(value)
        if value == self._settings.directory:
            return
        self._settings.directory = value
        self.settings_changed.emit()

    @Property(bool, notify=settings_changed)
    def destinationValid(self) -> bool:
        """Whether the remembered folder is still somewhere we can write.

        Checked rather than assumed: the last export may have gone to a drive
        that is no longer plugged in.
        """
        directory = self._settings.directory
        return bool(directory) and Path(directory).is_dir()

    @Property(str, notify=settings_changed)
    def unavailableReason(self) -> str:
        """Why the chosen format cannot be produced here, or ``""``.

        Only ever about the format. A missing destination is obvious from the
        empty picker and does not need saying twice.
        """
        if self._settings.format == FORMAT_MP3 and ffmpeg_path() is None:
            return "ffmpeg is not installed — .mp3 export needs it"
        return ""

    @Property(bool, notify=settings_changed)
    def canExport(self) -> bool:
        """Whether pressing Export right now could actually do something."""
        return (
            not self.isRunning
            and self.destinationValid
            and not self.unavailableReason
        )

    # ==================================================================
    # Progress
    # ==================================================================

    @Property(bool, notify=running_changed)
    def isRunning(self) -> bool:
        return self._thread is not None

    @Property(str, notify=progress_changed)
    def stage(self) -> str:
        """The track currently being written."""
        return self._stage

    @Property(int, notify=progress_changed)
    def processed(self) -> int:
        return self._processed

    @Property(int, notify=progress_changed)
    def total(self) -> int:
        return self._total

    @Property(int, notify=progress_changed)
    def progress(self) -> int:
        """Completion as a percentage, for the progress strip."""
        if self._total <= 0:
            return 0
        return int(self._processed * 100 / self._total)

    @Property(int, notify=progress_changed)
    def lastExported(self) -> int:
        return self._last_exported

    @Property(int, notify=progress_changed)
    def lastSkipped(self) -> int:
        return self._last_skipped

    @Property(str, notify=progress_changed)
    def lastDestination(self) -> str:
        return self._last_destination

    # ==================================================================
    # Slots
    # ==================================================================

    @Slot(int)
    def exportPlaylist(self, playlist_id: int) -> None:
        """Export every track in a playlist, in playlist order."""
        playlist = self._playlists.get_playlist(playlist_id)
        if playlist is None:
            self.error_occurred.emit("That playlist no longer exists")
            return

        tracks = self._playlists.get_tracks(playlist_id)
        self._start([self._snapshot(t) for t in tracks], str(playlist.name))

    @Slot("QVariantList")
    @Slot("QVariantList", str)
    def exportTracks(self, track_ids, name: str = "Tracks") -> None:
        """Export an arbitrary set of tracks, in the order they were given.

        The counterpart of :meth:`exportPlaylist` for everything that is not a
        playlist. *name* is the folder the copies land in, and it is what makes
        this usable for an album — a record exported into a folder called
        "Tracks" is not an export anybody asked for. Defaulted rather than
        required so the one-argument call this shipped with still works.
        """
        ids = [int(i) for i in track_ids]
        found = {t.id: t for t in self._tracks.get_tracks_by_ids(ids)}
        items = [self._snapshot(found[i]) for i in ids if i in found]
        self._start(items, str(name or "Tracks"))

    @Slot()
    def cancel(self) -> None:
        """Stop after the track currently being written."""
        if self._worker is not None:
            self._worker.cancel()

    @Slot()
    def revealDestination(self) -> None:
        """Open the folder the last export went to in the file manager."""
        if self._last_destination:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._last_destination))

    def shutdown(self) -> None:
        """Stop the export before the app exits.

        Without this, closing the window mid-export would leave a thread — and
        possibly an ffmpeg child — writing files into the user's folder after
        the app they started it from is gone. Called from
        ``bootstrap.persist_app_state``.
        """
        self.cancel()
        if self._thread is None:
            return
        self._thread.quit()
        # Brief: the worker stops at the next track boundary, and a hang here
        # would keep the window on screen after the user closed it.
        if self._thread.wait(3000):
            # Only once it really stopped. Releasing a worker while its thread
            # still runs is the one thing that turns a slow shutdown into a
            # crashed one — see `app.viewmodels.worker_threads`.
            self._thread = None
            self._worker = None

    # ==================================================================
    # Internals
    # ==================================================================

    def _start(self, items: List[ExportItem], name: str) -> None:
        if self.isRunning:
            self.error_occurred.emit("An export is already running")
            return

        request = ExportRequest(
            items=tuple(items),
            destination=Path(self._settings.directory),
            playlist_name=name,
            fmt=self._settings.format,
        )

        # Run the checks here as well as in the engine so a misconfigured
        # export fails as a message, immediately, rather than as a thread that
        # starts and stops again.
        try:
            preflight(request)
        except ExportError as exc:
            self.error_occurred.emit(str(exc))
            return

        self._stage = ""
        self._processed = 0
        self._total = len(items)
        self._last_exported = 0
        self._last_skipped = 0
        self.progress_changed.emit()

        thread = QThread(self)
        worker = ExportWorker(request)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.stage.connect(self._on_stage)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        own_worker(thread, worker, self._on_stopped)
        self._thread = thread
        self._worker = worker
        thread.start()
        self.running_changed.emit()
        self.settings_changed.emit()   # canExport is false while running

    def _snapshot(self, track) -> ExportItem:
        """Freeze the fields the engine needs, off the ORM object.

        The lyrics read happens here — on the UI thread, against the shared
        session — for the same reason every other field is copied out rather
        than looked up later: the engine runs on a worker thread and may not
        touch the session at all.
        """
        track_id = int(track.id)
        lyrics = ""
        if self._lyrics is not None:
            lyrics = self._lyrics.get(track_id) or ""
        return ExportItem(
            track_id=track_id,
            title=str(track.title or ""),
            artist=str(track.artist or ""),
            album=str(track.album or ""),
            lyrics=lyrics,
        )

    @staticmethod
    def _to_local_path(value: str) -> str:
        """Turn a QML ``file:///`` URL into a plain path."""
        text = str(value or "")
        if text.startswith("file:"):
            return QUrl(text).toLocalFile()
        return text

    # ---- worker callbacks --------------------------------------------

    @Slot(str)
    def _on_stage(self, text: str) -> None:
        self._stage = text
        self.progress_changed.emit()

    @Slot(int, int)
    def _on_progress(self, done: int, total: int) -> None:
        self._processed = done
        self._total = total
        self.progress_changed.emit()

    @Slot(int, int, str)
    def _on_finished(self, exported: int, skipped: int, destination: str) -> None:
        self._last_exported = exported
        self._last_skipped = skipped
        self._last_destination = destination
        self._stage = ""
        self.progress_changed.emit()
        self.export_finished.emit(exported, skipped, destination)

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._stage = ""
        self.progress_changed.emit()
        self.error_occurred.emit(message)

    @Slot(int)
    def _on_cancelled(self, written: int) -> None:
        self._last_exported = written
        self._stage = ""
        self.progress_changed.emit()
        self.error_occurred.emit(
            f"Export cancelled — {written} file{'' if written == 1 else 's'} written"
        )

    @Slot()
    def _on_stopped(self) -> None:
        """The export thread has gone; release it and re-enable the button."""
        self._thread = None
        self._worker = None
        self.running_changed.emit()
        self.settings_changed.emit()
