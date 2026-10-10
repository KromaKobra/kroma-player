"""Background workers for vocal removal.

Same shape as :mod:`app.importer.import_workers`: a plain ``QObject`` moved onto
a ``QThread`` by the view model, a ``run`` slot wrapped in try/except/finally,
and a ``done`` signal that is always emitted last so the view model has one
place to tear the thread down.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from app.separation.runner import (
    SeparationCancelled,
    SeparationError,
    SeparationRunner,
    probe_devices,
)
from app.utils.library_paths import track_path

logger = logging.getLogger(__name__)


class DeviceProbeWorker(QObject):
    """Ask an interpreter what it can run on, without blocking the UI.

    Importing torch takes seconds, so this cannot happen on the main thread
    when Settings opens.
    """

    # devices, error, backend, backend_error
    finished = Signal(list, str, str, str)
    done = Signal()

    def __init__(self, python_path: str, msst_path: str = "") -> None:
        super().__init__()
        self._python_path = python_path
        self._msst_path = msst_path

    @Slot()
    def run(self) -> None:
        try:
            probe = probe_devices(self._python_path, self._msst_path)
            self.finished.emit(
                probe.devices, probe.error, probe.backend, probe.backend_error
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Device probe failed")
            self.finished.emit([], str(exc), "", "")
        finally:
            self.done.emit()


class SeparationWorker(QObject):
    """Generate one track's instrumental and install it into the vault."""

    stage = Signal(int, str)      # track_id, human-readable phase
    progress = Signal(int, int)   # track_id, 0-100 within this track
    finished = Signal(int)        # track_id
    failed = Signal(int, str)     # track_id, error message
    cancelled = Signal(int)       # track_id
    done = Signal(int)            # track_id (always emitted last)

    def __init__(
        self,
        track_id: int,
        python_path: str,
        model_path: str,
        device: str,
        backing_service,
        config_path: str = "",
        msst_path: str = "",
    ) -> None:
        super().__init__()
        self._track_id = track_id
        self._backing = backing_service
        self._runner = SeparationRunner(
            python_path, model_path, device, config_path, msst_path
        )

    def cancel(self) -> None:
        """Called from the UI thread while ``run`` is executing elsewhere."""
        self._runner.cancel()

    @Slot()
    def run(self) -> None:
        try:
            mix = track_path(self._track_id)
            if not mix.exists():
                raise SeparationError(f"No audio file for track {self._track_id}")

            with tempfile.TemporaryDirectory(prefix="kroma-bt-out-") as out:
                opus = self._runner.generate(
                    mix,
                    Path(out),
                    on_stage=lambda text: self.stage.emit(self._track_id, text),
                    on_progress=lambda pct: self.progress.emit(self._track_id, pct),
                )
                # install() moves the file out of the temp dir before it is
                # cleaned up, and marks the track as having an instrumental.
                self._backing.install(self._track_id, opus)

            self.finished.emit(self._track_id)
        except SeparationCancelled:
            self.cancelled.emit(self._track_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Separation failed for track %s", self._track_id)
            self.failed.emit(self._track_id, str(exc))
        finally:
            self.done.emit(self._track_id)
