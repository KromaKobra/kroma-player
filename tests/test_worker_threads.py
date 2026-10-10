"""Workers outlive their threads, everywhere the app starts one.

The rule is in ``app.viewmodels.worker_threads``: the last Python reference to a
worker may only be dropped after its thread has finished, because a worker has
no Qt parent and dropping that reference *is* a delete. Do it while the worker's
thread is still alive and Qt ends the process — which is what "cancelling the
import sometimes crashes" turned out to be.

Two things are worth pinning here. That ``own_worker`` really does defer the
view model's teardown until the thread has stopped, which is the invariant; and
that running the whole cycle many times over does not abort, which is the only
way to catch a regression that is a race rather than a mistake. A test for the
second does not fail — it *dies* — and that is the honest signal for this.

The catch is that "many times over" has to mean something specific. Qt emits
``finished`` before it flushes the thread's deferred deletes, so the window in
which the view model can delete a worker its thread is also about to delete is
open only while the main thread is free to take the queued slot at once. Cycling
with a sleep in the pump — which is the obvious way to write it, and what
``test_many_cycles_do_not_abort_the_process`` does — holds the main thread off
just long enough to close that window every time. It ran green over a crash that
was live in the app for months. ``test_back_to_back_cycles_do_not_abort`` is the
same idea with the sleep taken out.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QThread, Signal, Slot

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.fakes.providers import FakeAudio, FakeRegistry, FakeTrackSource, row  # noqa: E402
from app.viewmodels.search_view_model import SearchViewModel  # noqa: E402
from app.viewmodels.worker_threads import own_worker  # noqa: E402


class Dawdler(QObject):
    """A worker that takes just long enough to still be running at `done`."""

    done = Signal()

    def __init__(self, seconds: float = 0.002) -> None:
        super().__init__()
        self._seconds = seconds

    @Slot()
    def run(self) -> None:
        try:
            time.sleep(self._seconds)
        finally:
            self.done.emit()


class Host(QObject):
    """The bookkeeping half: holds the pair, releases it when told."""

    def __init__(self) -> None:
        super().__init__()
        self.thread: QThread | None = None
        self.worker: Dawdler | None = None
        self.thread_was_finished: list[bool] = []

    def start(self, seconds: float = 0.002) -> None:
        thread = QThread(self)
        worker = Dawdler(seconds)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        own_worker(thread, worker, self._on_stopped)
        self.thread = thread
        self.worker = worker
        thread.start()

    @Slot()
    def _on_stopped(self) -> None:
        # Recorded, not asserted, so a failure names the round it happened in.
        self.thread_was_finished.append(
            self.thread is not None and self.thread.isFinished()
        )
        self.thread = None
        self.worker = None


def _pump_until(qt_app, predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if predicate():
            return True
        time.sleep(0.001)
    return False


def test_teardown_waits_for_the_thread_not_for_the_worker(qt_app):
    """The references are released after `finished`, never after `done`."""
    host = Host()
    host.start()

    assert _pump_until(qt_app, lambda: host.worker is None), "teardown never ran"
    assert host.thread_was_finished == [True], (
        "the view model released its worker while the thread was still running"
    )


def test_many_cycles_do_not_abort_the_process(qt_app):
    """The race, run often enough to hit. Aborts rather than fails if broken."""
    host = Host()
    for round_number in range(60):
        host.start(seconds=0.001 * (round_number % 3))
        assert _pump_until(qt_app, lambda: host.worker is None), (
            f"round {round_number}: teardown never ran"
        )
    assert host.thread_was_finished == [True] * 60


class Sprinter(QObject):
    """Like `Dawdler`, but burning CPU rather than sleeping.

    Three thousand rounds of a 2 ms sleep is six seconds of waiting; the same
    rounds of this is about one, and `done` is still emitted from inside `run`
    with the thread very much alive, which is all the race needs.
    """

    done = Signal()

    @Slot()
    def run(self) -> None:
        try:
            total = 0
            for i in range(2000):
                total += i * i
        finally:
            self.done.emit()


class Chain(QObject):
    """Rounds back to back, with nothing in between for the main thread to do.

    The next round starts from inside `_on_stopped`, so the main thread goes
    straight from one teardown to the next start and is never left waiting on a
    pump. That is what keeps the window open — and it is also what the app looks
    like when the import tab is not the one being drawn.
    """

    def __init__(self, rounds: int) -> None:
        super().__init__()
        self.remaining = rounds
        self.thread: QThread | None = None
        self.worker: Sprinter | None = None

    def start(self) -> None:
        if self.remaining <= 0:
            return
        self.remaining -= 1
        thread = QThread(self)
        worker = Sprinter()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        own_worker(thread, worker, self._on_stopped)
        self.thread = thread
        self.worker = worker
        thread.start()

    @Slot()
    def _on_stopped(self) -> None:
        self.thread = None
        self.worker = None
        self.start()


def test_back_to_back_cycles_do_not_abort(qt_app):
    """The real race, at the cadence that opens it. Dies rather than fails.

    Measured against the wiring this replaced — `finished` straight to the view
    model, with no wait in front of it — this caught the double delete in 9 runs
    out of 12, first crash usually somewhere past round 500. Three thousand
    rounds costs about a second, which is worth it for a fault whose only other
    symptom is the app vanishing.
    """
    chain = Chain(rounds=3000)
    chain.start()

    deadline = time.monotonic() + 60.0
    while chain.remaining > 0 and time.monotonic() < deadline:
        qt_app.processEvents()

    assert chain.remaining == 0, (
        f"the chain stalled with {chain.remaining} rounds left"
    )
    assert _pump_until(qt_app, lambda: chain.worker is None), "last teardown never ran"


# -- the download slot, over a real thread ---------------------------------
#
# `test_download_cap` drives the same limit by hand, with the threading taken
# out, so it can pin the ordering exactly. This is the other half: the real
# QThread, the real queued signals, and the question of whether the slot is
# genuinely still held while a worker is unwinding — which is a thing about
# threads, so no stand-in for one can answer it.


class _StubTrackService:
    def find_owned(self, rows):
        return [None for _ in rows]


class _SlowDownload(QObject):
    """A FetchAudioWorker that only dawdles, in the shape the view model expects."""

    finished = Signal(int, int)
    failed = Signal(int, str)
    done = Signal(int)
    progress = Signal(int, str, float)

    def __init__(self, uid, row, track_service, registry, cover_service=None,
                 metadata_override=None, lyrics_service=None):
        super().__init__()
        self._uid = uid

    def cancel(self) -> None:
        pass

    @Slot()
    def run(self) -> None:
        try:
            time.sleep(0.002)
        finally:
            self.done.emit(self._uid)


def _search_vm(repo, cache, event_bus, registry):
    source = registry.sources[0] if registry.sources else None
    vm = SearchViewModel(
        track_repository=repo,
        registry=registry,
        event_bus=event_bus,
        track_service=_StubTrackService(),
        row_cache=cache,
    )
    if source is not None:
        vm.setSource(source.key)
        vm._on_web_search_finished(source.key, [
            row("A", "X", video="aaaaaaaaaaa"), row("B", "X", video="bbbbbbbbbbb"),
        ])
    return vm


@pytest.fixture
def search_vm(repo, cache, event_bus):
    """Results on screen, and no plugin that can fetch them."""
    return _search_vm(repo, cache, event_bus, FakeRegistry(sources=[FakeTrackSource()]))


@pytest.fixture
def allowing_search_vm(repo, cache, event_bus):
    return _search_vm(
        repo, cache, event_bus,
        FakeRegistry(sources=[FakeTrackSource()], audio=FakeAudio()),
    )


def test_a_refused_download_claims_no_slot(qt_app, search_vm):
    search_vm.downloadRow(search_vm._uids[0])

    assert search_vm.isDownloading is False
    assert search_vm._download_thread is None


def test_the_slot_is_held_until_the_thread_has_finished(qt_app, allowing_search_vm, monkeypatch):
    """The limit's actual promise: no second worker beside a live thread.

    The release runs on `finished`, delivered to the main thread as a queued
    call, so a refusal asked for before the event loop is pumped is refused
    however quickly the worker itself got through — which is the point. The
    window this closes is the one where the work is over but the thread is not.
    """
    from app.viewmodels import search_view_model as module

    vm = allowing_search_vm
    first, second = vm._uids[0], vm._uids[1]
    monkeypatch.setattr(module, "FetchAudioWorker", _SlowDownload)

    vm.downloadRow(first)
    assert vm.downloadingUid == first

    busy: list[int] = []
    vm.download_busy.connect(busy.append)
    vm.downloadRow(second)
    assert busy == [second], "a second download started beside the first"

    assert _pump_until(qt_app, lambda: not vm.isDownloading), "the slot was never freed"
    assert vm._download_worker is None, "released only after `finished`, but released"

    vm.downloadRow(second)
    assert vm.downloadingUid == second, "the next download goes when the last is gone"
    assert _pump_until(qt_app, lambda: not vm.isDownloading)
