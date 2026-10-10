"""Background workers for the two backup passes.

Same shape as :mod:`app.exporter.workers`: a plain ``QObject`` moved onto a
``QThread`` by the view model, a ``run`` slot wrapped in try/except/finally, and
a ``done`` signal always emitted last so the view model has exactly one place to
tear the thread down. See :mod:`app.viewmodels.worker_threads` for the rule
about when its references may be released.

Two workers rather than one, because the pause between them is the feature. The
scan produces a plan and stops; the user reads it; only then does the apply
worker run, and it runs that plan rather than deciding anything again.

Cancellation is checked between files, not inside one. A track is a few
megabytes, so stopping mid-copy would buy no responsiveness — and a copy
abandoned at a file boundary leaves a ``.part`` that the next run sweeps, while
one abandoned mid-``os.replace`` has nowhere good to land.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot

from app.backup.apply import apply_plan
from app.backup.plan import (
    BackupCancelled,
    BackupError,
    BackupOptions,
    BackupPlan,
    BackupResult,
)
from app.backup.scan import build_plan

logger = logging.getLogger(__name__)


class BackupScanWorker(QObject):
    """Work out what a sync would do. Writes nothing."""

    stage = Signal(str)
    progress = Signal(int, int)     # done, total
    finished = Signal()             # the plan is on `self.plan`
    failed = Signal(str)
    cancelled = Signal()
    done = Signal()                 # always emitted last

    def __init__(self, source: Path, destination: Path, options: BackupOptions) -> None:
        super().__init__()
        self._source = source
        self._destination = destination
        self._options = options
        self._stop = threading.Event()

        # The result is handed over as a plain attribute rather than as a signal
        # argument, so nothing has to marshal a Python object across a queued
        # cross-thread connection. Every other worker in the app emits only
        # primitives, and this keeps that true. The view model still holds a
        # reference to the worker when `finished` reaches it, so reading the
        # result off it there is straightforward.
        self.plan: Optional[BackupPlan] = None

    def cancel(self) -> None:
        """Called from the UI thread while ``run`` is executing elsewhere."""
        self._stop.set()

    @Slot()
    def run(self) -> None:
        try:
            self.plan = build_plan(
                self._source,
                self._destination,
                self._options,
                on_stage=self.stage.emit,
                on_progress=self.progress.emit,
                should_cancel=self._stop.is_set,
            )
            self.finished.emit()
        except BackupCancelled:
            self.cancelled.emit()
        except BackupError as exc:
            # Expected refusals — a folder inside the vault, a drive that is
            # not writable. The message is already written for the user.
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Backup scan failed")
            self.failed.emit(str(exc))
        finally:
            self.done.emit()


class BackupApplyWorker(QObject):
    """Execute a plan the user has confirmed."""

    stage = Signal(str)
    progress = Signal(int, int)
    finished = Signal()             # the outcome is on `self.result`
    failed = Signal(str)
    cancelled = Signal(int)         # files written before the stop
    done = Signal()

    def __init__(self, plan: BackupPlan, app_version: str = "") -> None:
        super().__init__()
        self._plan = plan
        self._app_version = app_version
        self._stop = threading.Event()
        self._written = 0

        # Handed over as an attribute, not a signal argument — see the note in
        # BackupScanWorker.
        self.result: Optional[BackupResult] = None

    def cancel(self) -> None:
        self._stop.set()

    @Slot()
    def run(self) -> None:
        try:
            self.result = apply_plan(
                self._plan,
                app_version=self._app_version,
                on_stage=self.stage.emit,
                on_progress=self._count,
                should_cancel=self._stop.is_set,
            )
            self.finished.emit()
        except BackupCancelled:
            self.cancelled.emit(self._written)
        except BackupError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Backup failed")
            self.failed.emit(str(exc))
        finally:
            self.done.emit()

    def _count(self, done: int, total: int) -> None:
        """Track progress so a cancellation can say how far it got.

        The engine reports position rather than returning anything when it is
        stopped, so this is the only place that number survives.
        """
        self._written = done
        self.progress.emit(done, total)
