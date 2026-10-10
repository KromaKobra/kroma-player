"""Background worker for exporting.

Same shape as :mod:`app.separation.workers`: a plain ``QObject`` moved onto a
``QThread`` by the view model, a ``run`` slot wrapped in try/except/finally, and
a ``done`` signal always emitted last so the view model has exactly one place to
tear the thread down.

Cancellation is checked between tracks, not inside one. A four-minute track
transcodes in a couple of seconds, so killing ffmpeg mid-file would buy no
responsiveness and cost the process-handle bookkeeping that
:class:`~app.separation.runner.SeparationRunner` needs for its far longer jobs.
"""
from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Signal, Slot

from app.exporter.export import ExportCancelled, ExportRequest, export_tracks

logger = logging.getLogger(__name__)


class ExportWorker(QObject):
    """Run one export request to completion off the UI thread."""

    stage = Signal(str)                 # the track currently being written
    progress = Signal(int, int)         # done, total
    finished = Signal(int, int, str)    # exported, skipped, destination folder
    failed = Signal(str)                # error message
    cancelled = Signal(int)             # files written before the stop
    done = Signal()                     # always emitted last

    def __init__(self, request: ExportRequest) -> None:
        super().__init__()
        self._request = request
        self._stop = threading.Event()

    def cancel(self) -> None:
        """Called from the UI thread while ``run`` is executing elsewhere."""
        self._stop.set()

    @Slot()
    def run(self) -> None:
        written = 0
        try:
            outcome = export_tracks(
                self._request,
                on_stage=self.stage.emit,
                on_progress=self.progress.emit,
                should_cancel=self._stop.is_set,
            )
            written = outcome.exported
            self.finished.emit(
                outcome.exported,
                len(outcome.skipped),
                str(outcome.destination or ""),
            )
        except ExportCancelled:
            self.cancelled.emit(written)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Export failed")
            self.failed.emit(str(exc))
        finally:
            self.done.emit()
