"""Background QObject worker for a query against one online track source.

Runs :meth:`~app.plugins.providers.TrackSourceProvider.rows` on a worker thread
— it is a round trip to a plugin process, and a plugin call refuses the main
thread — and delivers the rows back via signals. The view model is responsible
for post-processing (uid assignment, the already-in-library check, etc.).

Cancellable like every other worker that talks to a plugin: a search the user
has replaced with a newer one is told to stop, and the plugin is sent
``cancel`` for it rather than finishing work nobody will look at.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from PySide6.QtCore import QObject, Signal, Slot

logger = logging.getLogger(__name__)


class TrackSourceWorker(QObject):
    """Ask one track source one query, off the UI thread."""

    finished = Signal(str, list)   # source key, list[row dict]
    failed   = Signal(str, str)    # source key, message
    done     = Signal()

    def __init__(self, source: Any, query: str, limit: int = 20) -> None:
        super().__init__()
        self._source = source
        self._query = query
        self._limit = limit
        self._stop = False

    def cancel(self) -> None:
        self._stop = True

    @Slot()
    def run(self) -> None:
        try:
            rows: List[Dict[str, Any]] = self._source.rows(
                self._query, limit=self._limit, should_stop=lambda: self._stop,
            )
            if self._stop:
                return
            self.finished.emit(self._source.key, rows)
        except Exception as exc:  # noqa: BLE001
            if not self._stop:
                logger.warning("Search on %s failed: %s", self._source.key, exc)
                self.failed.emit(self._source.key, str(exc))
        finally:
            self.done.emit()
