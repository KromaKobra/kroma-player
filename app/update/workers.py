"""Background workers for the two halves of updating: asking, and fetching.

Same shape as :mod:`app.backup.workers`, down to the details that are not
decoration: a plain ``QObject`` moved onto a ``QThread`` by the view model, a
``run`` slot wrapped in try/except/finally, a ``done`` signal emitted **exactly
once, last, on every path out** — including failure and cancellation — and the
result handed over as a plain attribute rather than as a signal argument, so
nothing has to marshal a Python object across a queued cross-thread connection.
See :mod:`app.viewmodels.worker_threads` for the rule about when the view model
may release its references, and what happens when it does not.

Two workers rather than one because the pause between them is the feature. The
check reports what exists and stops; the user reads what it says and decides;
only then does anything download. Nothing arrives on the user's disk without a
click — which is half of what makes this defensible against ADR 0012 at all.

Cancellation is checked between chunks rather than being a flag someone reads
at the end, because the thing being cancelled is a 230 MB download.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot

from app.update.download import UpdateCancelled, UpdateError, download_asset
from app.update.release import Asset, Release, fetch_latest_release

logger = logging.getLogger(__name__)


class UpdateCheckWorker(QObject):
    """Ask GitHub what the newest stable release is. Writes nothing.

    A few kilobytes of JSON over two requests. It is on a thread anyway because
    the first one can hang for the full timeout on a captive-portal network, and
    fifteen seconds of frozen window is not an acceptable way to find out there
    is no update.
    """

    finished = Signal()      # the release is on `self.release`, or None
    failed = Signal(str)
    done = Signal()          # always emitted last

    def __init__(self) -> None:
        super().__init__()

        # Handed over as an attribute, not a signal argument — see the module
        # docstring. The view model still holds a reference to the worker when
        # `finished` reaches it, so reading the result off it there is
        # straightforward, and it is only released in `_on_stopped`.
        self.release: Optional[Release] = None

    @Slot()
    def run(self) -> None:
        try:
            # Returns None for every uninteresting outcome — offline,
            # rate-limited, malformed, no manifest — and none of those is a
            # failure the user can act on. `failed` is reserved for something
            # genuinely unexpected getting this far.
            self.release = fetch_latest_release()
            self.finished.emit()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Update check failed")
            self.failed.emit(str(exc))
        finally:
            self.done.emit()


class UpdateDownloadWorker(QObject):
    """Fetch one asset and verify it against the hash published with it."""

    stage = Signal(str)
    progress = Signal(int, int)     # bytes received, bytes expected
    finished = Signal()             # the path is on `self.downloaded_path`
    failed = Signal(str)
    cancelled = Signal()
    done = Signal()

    def __init__(self, asset: Asset, destination: Optional[Path] = None) -> None:
        super().__init__()
        self._asset = asset
        self._destination = destination
        self._stop = threading.Event()

        self.downloaded_path: Optional[Path] = None

    def cancel(self) -> None:
        """Called from the UI thread while ``run`` is executing elsewhere."""
        self._stop.set()

    @Slot()
    def run(self) -> None:
        try:
            self.stage.emit(f"Downloading {self._asset.name}")
            path = download_asset(
                self._asset,
                self._destination,
                on_progress=self.progress.emit,
                should_cancel=self._stop.is_set,
            )
            # Only reached once the digest matched: download_asset raises rather
            # than returning an unverified file, so there is no state in which
            # `downloaded_path` names something that was not checked.
            self.stage.emit("Verified")
            self.downloaded_path = path
            self.finished.emit()
        except UpdateCancelled:
            self.cancelled.emit()
        except UpdateError as exc:
            # Expected refusals — a checksum mismatch, a server that went away,
            # a full disk. The message is already written for the user.
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Update download failed")
            self.failed.emit(str(exc))
        finally:
            self.done.emit()
