"""Cancelling an import stops it, drops what it found, and gives the tab back.

The bug this pins down is not that cancel did too little — it is that it did
nothing observable. The match worker ran to completion in the background and
then pushed its results into the model, so the page the user had just left came
back by itself, holding results from a run they had abandoned.

So the assertions cover all three halves of "cancel":

* the worker stops, rather than finishing quietly in the background;
* nothing it matched reaches the model;
* the busy flags clear *immediately*, not when the thread eventually unwinds,
  because those flags are what the Library page swaps its view on.

The engine here blocks inside ``match_file`` — reached through the fake identity
provider in ``tests.fakes`` — until the test releases it, which
is what makes the only timing that matters — cancel arriving while a match is in
flight — reproducible rather than lucky.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.importer.import_workers import ImportSearchWorker  # noqa: E402
from app.importer.media_probe import ProbedFile, probe_media_files  # noqa: E402
from app.viewmodels.import_view_model import ImportViewModel  # noqa: E402
from tests.conftest import OwnedByIdOnly  # noqa: E402
from tests.fakes.providers import FakeAudio, registry_for  # noqa: E402

TIMEOUT = 5.0


# -- doubles ---------------------------------------------------------------


class GatedEngine:
    """A match engine that holds every file until the test lets it through.

    ``started`` and ``finished`` are written only by the worker thread and read
    only by the test, so "a match is in flight" is ``started > finished`` and
    needs no lock of its own.
    """

    def __init__(self) -> None:
        self._permits = threading.Semaphore(0)
        self.started = 0
        self.finished = 0

    def release(self, count: int = 1) -> None:
        for _ in range(count):
            self._permits.release()

    def match_file(self, probed: ProbedFile, use_filename_fallback: bool):
        self.started += 1
        assert self._permits.acquire(timeout=TIMEOUT), "the test never released a match"
        self.finished += 1
        return {
            "title": probed.title,
            "artist": probed.artist,
            "album": "",
            "duration": 100,
            "source_id": f"vid{self.finished:08d}",
            "cover_url": "",
            "cover_master_url": "",
            "source_path": probed.source_path,
            "release_date": None,
        }


class StubImportService(OwnedByIdOnly):
    """The one thing the view model asks of it while finishing a search."""

    def existing_ids(self, ids):
        return {}


class StubDownloads:
    allowed = True


# -- helpers ---------------------------------------------------------------


def _wait(predicate, timeout: float = TIMEOUT) -> bool:
    """Spin until *predicate* holds. For state the worker thread writes."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _pump_until(qt_app, predicate, timeout: float = TIMEOUT) -> bool:
    """Run the event loop until *predicate* holds. For queued signals."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


@pytest.fixture
def files():
    return [
        ProbedFile(path=Path(f"/music/{i}.opus"), title=f"Song {i}", artist="A")
        for i in range(4)
    ]


@pytest.fixture
def other_files():
    """A second import's worth of files, sharing nothing with `files`.

    Distinct paths because a file already in the list is skipped before the
    match run — re-importing `files` would correctly do nothing at all, which
    is not what the tests using this are about.
    """
    return [
        ProbedFile(path=Path(f"/music/other-{i}.opus"), title=f"Other {i}", artist="B")
        for i in range(3)
    ]


@pytest.fixture
def engine():
    return GatedEngine()


@pytest.fixture
def vm(qt_app, engine):
    model = ImportViewModel(
        import_service=StubImportService(),
        track_service=None,
        registry=registry_for(engine, audio=FakeAudio()),
    )
    yield model
    # Never leave a worker thread behind for the next test to trip over.
    model.cancelImport()
    engine.release(100)
    _pump_until(qt_app, lambda: model._search_thread is None)


def _start(vm, engine, files):
    """Begin a match run and return once its first match is in flight.

    Counted relative to what the engine has already seen, so a test can run two
    imports one after the other.
    """
    before = engine.started
    vm._candidates = files
    vm._start_search(files)
    assert _wait(lambda: engine.started > before), "the worker never started matching"


def _unwind(qt_app, vm, engine):
    """Let the run finish however it means to, and wait for its teardown."""
    engine.release(100)
    assert _pump_until(qt_app, lambda: vm._search_thread is None), "run never unwound"


# -- the worker on its own -------------------------------------------------


def test_a_cancelled_worker_reports_done_but_never_finished(engine, files):
    """`done` is teardown and always fires; `finished` is a result and must not."""
    worker = ImportSearchWorker(files, False, registry_for(engine))

    emitted: list[str] = []
    worker.finished.connect(lambda _results: emitted.append("finished"))
    worker.done.connect(lambda: emitted.append("done"))

    worker.cancel()
    worker.run()

    assert emitted == ["done"]
    assert engine.started == 0, "a worker cancelled before it ran matched nothing"


def test_the_worker_stops_at_the_next_file_not_the_last(engine, files):
    """Cancellation is checked per file, so a long list does not run on."""
    worker = ImportSearchWorker(files, False, registry_for(engine))
    matched: list[list] = []
    worker.finished.connect(matched.append)

    def driver():
        # Let one match through, then cancel while the second one is waiting.
        assert _wait(lambda: engine.started == 1)
        engine.release()
        assert _wait(lambda: engine.started == 2)
        worker.cancel()
        engine.release()

    thread = threading.Thread(target=driver)
    thread.start()
    worker.run()
    thread.join(timeout=TIMEOUT)

    assert engine.finished == 2, "it kept matching after the flag was set"
    assert matched == [], "a cancelled run has no result to emit"


# -- probing ---------------------------------------------------------------


def test_probing_stops_between_files_when_asked(tmp_path):
    """A cancelled scan stops reading the disk instead of running to the end."""
    paths = []
    for i in range(5):
        path = tmp_path / f"{i}.opus"
        path.write_bytes(b"")
        paths.append(path)

    polls = {"n": 0}

    def should_stop() -> bool:
        # Polled once before each file: let two through, then stop.
        polls["n"] += 1
        return polls["n"] > 2

    probed = probe_media_files(paths, should_stop)

    assert len(probed) == 2
    assert probe_media_files(paths) == probe_media_files(paths, lambda: False), (
        "no should_stop and a should_stop that never fires must agree"
    )


# -- the view model --------------------------------------------------------


def test_cancel_gives_the_page_back_before_the_thread_stops(qt_app, vm, engine, files):
    """The busy flags clear on the click, not when the worker gets around to it."""
    _start(vm, engine, files)

    vm.cancelImport()

    assert vm.isSearching is False, "the Library page swaps its view on this"
    assert vm.isAnalyzing is False
    assert vm._search_thread is not None, "the worker is still inside a match"

    _unwind(qt_app, vm, engine)
    assert vm.isSearching is False


def test_cancel_drops_what_the_run_had_matched(qt_app, vm, engine, files):
    """One file matched, another in flight, then cancelled: neither is kept."""
    _start(vm, engine, files)
    engine.release()
    assert _wait(lambda: engine.started == 2 and engine.finished == 1)

    completed: list[int] = []
    vm.search_completed.connect(lambda: completed.append(1))

    vm.cancelImport()
    _unwind(qt_app, vm, engine)

    assert engine.started == 2, "it worked through the rest of the list anyway"
    assert completed == [], "a cancelled run must not announce results"
    assert vm.result_count == 0
    assert vm.hasResults is False
    assert vm.searchProgress == 0, "stale progress would show on the next run"


def test_cancel_leaves_an_earlier_imports_results_alone(
    qt_app, vm, engine, files, other_files
):
    """Cancelling a second run puts you back on the first one's results."""
    _start(vm, engine, files)
    _unwind(qt_app, vm, engine)
    assert vm.hasResults is True
    first_run = vm.result_count
    assert first_run == len(files)

    _start(vm, engine, other_files)
    vm.cancelImport()
    _unwind(qt_app, vm, engine)

    assert vm.hasResults is True, "the import page still has something to show"
    assert vm.result_count == first_run, "run one's results were disturbed"
    assert vm.isSearching is False


def test_cancel_leaves_the_surviving_rows_saying_what_they_are(
    qt_app, vm, engine, files, other_files
):
    """A cancelled CSV run must not leave local-file rows offering to download.

    Download mode is set when a run *starts*, so between then and results
    arriving it describes a run that may never produce any. Cancel is exactly
    that case — and the rows on screen came from somewhere else entirely.

    What makes this safe is that no row asks the view model: each carries its
    own `is_download`, stamped when it arrived, and a run that never produced a
    row never stamped anything.
    """
    _start(vm, engine, files)          # a local-file import, run to completion
    _unwind(qt_app, vm, engine)

    vm._set_download_mode(True)             # as analyzeCsv does, before parsing
    _start(vm, engine, other_files)
    vm.cancelImport()
    _unwind(qt_app, vm, engine)

    assert vm.hasResults is True
    assert all(r["is_download"] is False for r in vm._results), \
        "the rows on screen came from local files and still say so"
    assert vm.pendingDownloadCount == 0
    assert vm.pendingStashCount == vm.result_count


def test_repeated_cancels_do_not_abort_the_process(qt_app, files):
    """The teardown race, run often enough to catch.

    A worker has no Qt parent, so Python owns it, and dropping the last
    reference is a `delete`. Doing that from the main thread while the worker's
    own thread is still running is a fatal error in Qt — the process aborts, so
    this test does not fail, it *dies*, which is the whole point of running the
    real threads rather than a stub of them.

    Cancelling is what made it likely: the normal path runs the result handler
    first and the model reset in there is usually delay enough for the worker to
    have deleted itself. A cancelled worker emits no result, so nothing stands
    between `done` and the reference being dropped. Hence a loop, and hence a
    match slow enough that cancel lands mid-flight rather than before the run
    has begun.
    """

    class DawdlingEngine:
        def match_file(self, probed, use_filename_fallback):
            time.sleep(0.002)
            return {
                "title": probed.title,
                "artist": probed.artist,
                "album": "",
                "duration": 100,
                "source_id": "vid00000001",
                "cover_url": "",
                "cover_master_url": "",
                "source_path": probed.source_path,
                "release_date": None,
            }

    model = ImportViewModel(
        import_service=StubImportService(),
        track_service=None,
        registry=registry_for(DawdlingEngine(), audio=FakeAudio()),
    )

    for round_number in range(40):
        model._candidates = files
        model._start_search(files)
        time.sleep(0.003 * (round_number % 4))   # cancel at varying depths
        model.cancelImport()
        assert _pump_until(qt_app, lambda: model._search_thread is None), (
            f"round {round_number}: the run never unwound"
        )
        assert model.result_count == 0


def test_a_new_import_is_refused_while_the_last_one_unwinds(qt_app, vm, engine, files):
    """And says so, rather than being a button that quietly does nothing."""
    _start(vm, engine, files)
    vm.cancelImport()

    complaints: list[str] = []
    vm.error_occurred.connect(complaints.append)

    vm.analyzePaths(["/music/somewhere-else"])

    assert vm._analyze_thread is None, "a second run started over the first"
    assert len(complaints) == 1 and "try again" in complaints[0]

    _unwind(qt_app, vm, engine)
