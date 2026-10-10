"""The two lyric jobs that need a thread of their own.

The only Qt in this package. Both workers obey the rule
``docs/architecture/cross-cutting/threading.md`` states without exception — *a
worker thread must not touch the shared session* — using the first of its two
sanctioned patterns: everything they need is frozen on the UI thread and handed
over, and everything they produce is written back on the UI thread by the view
model. Neither holds a repository, a service or a session, so neither has to
serialise against the import and download writers.

Both follow the house worker shape (see :mod:`app.exporter.workers`): a plain
``QObject`` moved onto a ``QThread`` by the view model, a ``run`` slot wrapped
in try/except/finally, and a ``done`` signal emitted last on every path out —
which is what :func:`app.viewmodels.worker_threads.own_worker` requires.
"""
from __future__ import annotations

import logging
import threading
from typing import Sequence

from PySide6.QtCore import QObject, Signal, Slot

from app.lyrics.lookup import lookup_lyrics
from app.lyrics.tags import read_lyrics_tag

logger = logging.getLogger(__name__)

# How many tracks the sweep collects before handing a batch back.
#
# A signal per track would cost a queued cross-thread call per file for a job
# that runs over a whole library; one signal at the end would mean an
# interrupted sweep threw away everything it had found and a user watching a
# fresh vault saw nothing happen for a minute. Two hundred is small enough that
# the payload stays trivial and progress is visibly incremental, large enough
# that the queueing is noise next to the file reads.
SWEEP_BATCH = 200


class LyricsFetchWorker(QObject):
    """Search for one track's lyrics, off the UI thread.

    Takes the track's identity as three frozen values rather than a ``Track``:
    the ORM object belongs to the shared session and walking it from here is
    exactly what the threading rule forbids.

    The id travels out again on every result signal so the view model can tell
    whether the answer is still about the track it is showing — a search takes
    seconds and the user may well have skipped on.
    """

    found = Signal(int, str)      # track_id, the lyric document
    not_found = Signal(int)       # track_id — an ordinary outcome, not an error
    failed = Signal(str)          # message
    done = Signal()               # always emitted last

    def __init__(self, track_id: int, title: str, artist: str) -> None:
        super().__init__()
        self._track_id = int(track_id)
        self._title = title
        self._artist = artist

    @Slot()
    def run(self) -> None:
        try:
            content = lookup_lyrics(self._title, self._artist)
            if content and content.strip():
                self.found.emit(self._track_id, content)
            else:
                self.not_found.emit(self._track_id)
        except Exception as exc:  # noqa: BLE001 — lookup_lyrics swallows its own
            logger.exception("Lyric fetch failed for track %s", self._track_id)
            self.failed.emit(str(exc))
        finally:
            self.done.emit()


class LyricsTagSweepWorker(QObject):
    """Copy a library's embedded ``LYRICS`` tags into the database.

    Given ``(track_id, file_path)`` pairs frozen on the UI thread — from
    ``LyricsRepository.track_ids_without_lyrics()`` and ``Track.file_path`` —
    this opens each file and collects whatever tag it finds. Pure filesystem
    work, and **read-only**: no database, which is the whole reason it is
    allowed to run here, and no writes to the files it walks, which is what
    makes it safe to interrupt at any point and to run twice.

    Results come back in batches rather than one signal at the end, so an
    interrupted run still leaves everything it had already found in the table
    and a resumed one has that much less to do.
    """

    batch = Signal(list)          # list[tuple[int, str]] — (track_id, content)
    finished = Signal(int)        # reached the end; how many tracks had lyrics
    cancelled = Signal(int)       # stopped early; how many it had found by then
    failed = Signal(str)
    done = Signal()               # always emitted last

    def __init__(self, targets: Sequence[tuple[int, str]]) -> None:
        super().__init__()
        self._targets = list(targets)
        self._stop = threading.Event()

    def cancel(self) -> None:
        """Called from the UI thread while ``run`` is executing elsewhere."""
        self._stop.set()

    @Slot()
    def run(self) -> None:
        found = 0
        stopped = False
        pending: list[tuple[int, str]] = []
        try:
            for track_id, path in self._targets:
                if self._stop.is_set():
                    stopped = True
                    break
                content = read_lyrics_tag(path)
                if content and content.strip():
                    pending.append((track_id, content))
                    found += 1
                if len(pending) >= SWEEP_BATCH:
                    self.batch.emit(pending)
                    pending = []
            # Handed over either way: a batch already collected is work done, and
            # dropping it on a cancel would mean re-reading those files later.
            if pending:
                self.batch.emit(pending)
            # `finished` means *the whole library was looked at*, and the view
            # model records the walk as done on it. A run that stopped early
            # must not say that — the files it never opened would otherwise
            # never be opened again.
            if stopped:
                self.cancelled.emit(found)
            else:
                self.finished.emit(found)
        except Exception as exc:  # noqa: BLE001 — a sweep must not kill the tab
            logger.exception("Lyric tag sweep failed")
            self.failed.emit(str(exc))
        finally:
            self.done.emit()
