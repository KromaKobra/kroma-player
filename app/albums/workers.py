"""The only Qt in this package: the thread that rewrites the audio files.

Everything below this module is free of Qt and of SQLAlchemy, and this one adds
the signals without adding any decisions — the plan is already frozen when it
arrives.

Two rules it keeps, both from ``docs/guides/add-a-feature.md``:

- **``done`` is emitted exactly once, last, on every path out of ``run``.** A
  reference released on ``done`` rather than on the thread's ``finished`` is the
  crash that only happens when nobody is looking; the view model releases on
  ``on_stopped``.
- **It writes nothing to the database.** The application shares one SQLAlchemy
  ``Session``, so a worker touching it would race every reader in the process.
  This reports the ids it actually rewrote and the view model does the write on
  the UI thread — which also means a run that failed on one file cannot claim it
  succeeded.

Cancellation stops **between tracks**. That is a boundary where abandoning
leaves nothing behind: some files rewritten and some not is precisely the state
the album was already in, and the ones that did change are individually correct.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal, Slot

from app.albums.unify import UnifyCancelled, UnifyPlan, rewrite_track

logger = logging.getLogger(__name__)


class AlbumUnifyWorker(QObject):
    """Embed one cover into each of an album's chosen tracks."""

    stage = Signal(str)
    progress = Signal(int, int)      # done, total
    finished = Signal(list)          # track ids successfully rewritten
    failed = Signal(str)
    cancelled = Signal(list)         # track ids rewritten before stopping
    done = Signal()

    def __init__(self, plan: UnifyPlan) -> None:
        super().__init__()
        self._plan = plan
        self._stop = False

    @Slot()
    def cancel(self) -> None:
        """Ask the run to stop at the next track boundary."""
        self._stop = True

    @Slot()
    def run(self) -> None:
        written: list[int] = []
        try:
            total = self._plan.total
            self.stage.emit(f"Unifying artwork on “{self._plan.album_name}”")
            self.progress.emit(0, total)

            for index, (track_id, path) in enumerate(self._plan.track_paths, start=1):
                if self._stop:
                    raise UnifyCancelled()
                try:
                    rewrite_track(self._plan, path)
                except Exception as exc:  # noqa: BLE001
                    # One unwritable file must not abandon the rest. On Windows
                    # the playing track's file is held open by the audio backend,
                    # so the single most likely failure here is the track the
                    # user is listening to — and losing the other nineteen
                    # repairs over it would be a poor trade.
                    logger.warning("Could not rewrite artwork for %s: %s", path, exc)
                else:
                    written.append(int(track_id))
                self.progress.emit(index, total)

            self.finished.emit(written)
        except UnifyCancelled:
            self.cancelled.emit(written)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Album artwork unify failed")
            self.failed.emit(str(exc))
        finally:
            self.done.emit()
