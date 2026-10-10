"""Aborting an import stops the queue and the row in the middle of it.

Two halves, and the second is the one with teeth. Emptying the queue is
bookkeeping. Stopping the row already being written means stopping it somewhere
it is safe to stop: every stash does its slow work — transcode, artwork,
download — inside a temporary directory and commits at the very end, first by
creating the track row and then by moving the audio into the vault. Stop before
that commit and the temp directory takes everything with it. Stop *inside* it
and the library is left naming a file that was never moved, which nothing later
would notice or repair.

So the boundary is "just before the commit", and what is pinned here is that a
stopped row leaves the library exactly as it was, that already-imported rows are
untouched, and that a row stopped this way is not reported as a failure — it did
not fail, it was called off.
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

from app.importer import import_workers  # noqa: E402
from app.importer.stash import _stopped  # noqa: E402
from app.viewmodels.import_view_model import ImportViewModel  # noqa: E402
from tests.conftest import OwnedByIdOnly  # noqa: E402
from tests.fakes.providers import FakeAudio, registry_for  # noqa: E402
from tests.fakes.providers import from_legacy  # noqa: E402

TIMEOUT = 5.0


class StubImportService(OwnedByIdOnly):
    def existing_ids(self, ids):
        return {}


class StubDownloads:
    allowed = True


class StubImports:
    """Finished rows stay listed, so a test here can still see what landed.

    Aborting is about the queue, not about what the list keeps afterwards. The
    shipped default takes a row off as it finishes, which is its own feature and
    has its own file — tests/test_import_clear.py.
    """
    clear_imported = False


class GatedStash:
    """A stash held at its boundary check, so abort can land mid-row.

    Stands where the real `stash_imported_track` stands: it does the slow part
    first, then asks whether it should still commit. The test controls when the
    slow part returns, which is what makes "the user pressed abort during the
    transcode" a thing that happens on demand.
    """

    def __init__(self) -> None:
        self._permits = threading.Semaphore(0)
        self.started: list[int] = []
        self.committed: list[int] = []

    def release(self, count: int = 1) -> None:
        for _ in range(count):
            self._permits.release()

    def __call__(self, result, track_service, cover_service, should_stop=None,
                 *, cover_bytes=None, registry=None, lyrics_service=None):
        uid = int(result["uid"])
        self.started.append(uid)
        assert self._permits.acquire(timeout=TIMEOUT), "the test never released a stash"
        if _stopped(should_stop):
            return None                    # the temp directory would go here
        self.committed.append(uid)
        return 900 + uid


def _pump_until(qt_app, predicate, timeout: float = TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if predicate():
            return True
        time.sleep(0.002)
    return False


def _wait(predicate, timeout: float = TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.002)
    return False


def _row_legacy(title):
    return {
        "title": title,
        "artist": "A",
        "album": "",
        "duration": 100,
        "source_id": f"vid{title}",
        "cover_url": "",
        "source_path": f"/music/{title}.opus",
        "not_found": False,
    }


def _row(*args, **kwargs):
    """`_row_legacy`, as a core row — see `tests.fakes.providers.from_legacy`."""
    return from_legacy(_row_legacy(*args, **kwargs))


@pytest.fixture
def stash(monkeypatch):
    gate = GatedStash()
    monkeypatch.setattr(import_workers, "stash_imported_track", gate)
    return gate


@pytest.fixture
def vm(qt_app, stash):
    model = ImportViewModel(
        import_service=StubImportService(),
        track_service=None,
        registry=registry_for(audio=FakeAudio()),
        import_settings=StubImports(),
    )
    yield model
    model.abortStash()
    stash.release(100)
    _pump_until(qt_app, lambda: model._stash_thread is None)


def _load(vm, count):
    vm._on_search_finished([_row(f"Song {i}") for i in range(count)])
    return [r["uid"] for r in vm._results]


def _in_library(vm):
    return [r["uid"] for r in vm._results if r.get("id") is not None]


# -- the button's two states ----------------------------------------------


def test_a_run_is_only_under_way_between_starting_and_finishing(qt_app, vm, stash):
    uids = _load(vm, 2)
    assert vm.isStashing is False, "nothing to abort before anything is pressed"

    vm.stashAll()
    assert vm.isStashing is True

    stash.release(2)
    assert _pump_until(qt_app, lambda: not vm.isStashing), "left offering to abort"
    assert vm.isAborting is False
    assert _in_library(vm) == uids


def test_the_run_stays_under_way_across_the_gap_between_rows(qt_app, vm, stash):
    """The button must not flick back to "Import all" between two rows."""
    _load(vm, 3)
    vm.stashAll()

    states: list[bool] = []
    vm.stashing_changed.connect(lambda: states.append(vm.isStashing))

    stash.release()
    assert _pump_until(qt_app, lambda: vm.stashingUid == 1)

    assert all(states), f"the run reported itself over mid-queue: {states}"


def test_aborting_when_nothing_runs_does_nothing(vm):
    _load(vm, 2)
    vm.abortStash()
    assert vm.isAborting is False
    assert vm.isStashing is False


# -- what abort actually stops --------------------------------------------


def test_abort_stops_the_queue_and_the_row_in_flight(qt_app, vm, stash):
    """The feature: nothing further starts, and the current row never commits."""
    uids = _load(vm, 4)
    vm.stashAll()
    assert _wait(lambda: stash.started == [uids[0]])

    vm.abortStash()

    assert vm.isAborting is True, "the row in flight has not reached a boundary yet"
    assert vm.isStashing is True, "so the run is not over either"

    stash.release()                       # let the in-flight row reach its check
    assert _pump_until(qt_app, lambda: not vm.isStashing)

    assert stash.started == [uids[0]], "the queued rows started anyway"
    assert stash.committed == [], "the row in flight was written after all"
    assert _in_library(vm) == [], "the library gained a track from an aborted run"
    assert vm.isAborting is False


def test_rows_already_imported_are_left_alone(qt_app, vm, stash):
    uids = _load(vm, 4)
    vm.stashAll()

    stash.release()                       # first row commits
    assert _pump_until(qt_app, lambda: vm.stashingUid == uids[1])

    vm.abortStash()
    stash.release()
    assert _pump_until(qt_app, lambda: not vm.isStashing)

    assert stash.committed == [uids[0]], "only the row that finished should be in"
    assert _in_library(vm) == [uids[0]]
    assert vm.result_count == 4, "the rest are still listed, ready to try again"


def test_an_aborted_row_is_not_reported_as_a_failure(qt_app, vm, stash):
    """It did not fail. Putting an error in front of the user would be a lie."""
    uids = _load(vm, 2)
    failures: list[int] = []
    completions: list[int] = []
    vm.stash_failed.connect(lambda uid, message: failures.append(uid))
    vm.stash_completed.connect(lambda uid, track_id: completions.append(uid))

    vm.stashAll()
    assert _wait(lambda: stash.started == [uids[0]])
    vm.abortStash()
    stash.release()
    assert _pump_until(qt_app, lambda: not vm.isStashing)

    assert failures == []
    assert completions == []


def test_the_aborted_row_stops_spinning(qt_app, vm, stash):
    """`done` fires on the cancelled path too, so the spinner still clears."""
    uids = _load(vm, 2)
    vm.stashAll()
    assert _wait(lambda: stash.started == [uids[0]])
    assert vm.stashingUid == uids[0]

    vm.abortStash()
    stash.release()

    assert _pump_until(qt_app, lambda: vm.stashingUid == -1), "left spinning"


def test_a_new_run_can_be_started_after_an_abort(qt_app, vm, stash):
    """Abort is not a dead end — the rows it skipped are still importable."""
    uids = _load(vm, 3)
    vm.stashAll()
    assert _wait(lambda: stash.started == [uids[0]])
    vm.abortStash()
    stash.release()
    assert _pump_until(qt_app, lambda: not vm.isStashing)

    vm.stashAll()
    assert vm.isStashing is True
    assert vm.isAborting is False, "the new run inherited the old one's abort"

    stash.release(3)
    assert _pump_until(qt_app, lambda: not vm.isStashing)
    assert _in_library(vm) == uids, "every row imported on the second attempt"


def test_the_button_offers_to_stop_while_a_run_is_under_way(qt_app, tmp_path):
    """The feature's actual surface, on the real loaded GUI.

    `test_gui_packages` cannot catch a mistake here. It loads this same window
    and fails on binding *errors*, but a misspelt property is not an error in
    QML — it evaluates to `undefined`, and `!undefined` is true, so the button
    would sit there permanently offering to start an import that is already
    running. Nothing complains; it just never becomes an abort button.
    """
    from PySide6.QtCore import QObject, QUrl
    from PySide6.QtQml import QQmlApplicationEngine

    from app.artwork.cover_provider import CoverImageProvider
    from app.artwork.local_art_provider import LocalArtImageProvider
    from app.gui.gui_paths import bundled_gui_root
    from app.main import CONTEXT_PROPERTIES, _register_qml_types
    from app.utils.library_paths import set_library_root

    vault = tmp_path / "venomvault"
    (vault / ".data").mkdir(parents=True)
    set_library_root(vault)

    from app.bootstrap import create_service_registry

    registry = create_service_registry()
    _register_qml_types(registry)

    directory = bundled_gui_root() / "nocturne"
    engine = QQmlApplicationEngine()
    engine.addImageProvider(
        "cover", CoverImageProvider(registry.get_service("cover_store"))
    )
    engine.addImageProvider("localart", LocalArtImageProvider())
    context = engine.rootContext()
    for qml_name, service in CONTEXT_PROPERTIES.items():
        context.setContextProperty(qml_name, registry.get_service(service))
    engine.addImportPath(str(directory))
    engine.load(QUrl.fromLocalFile(str(directory / "main.qml")))

    roots = engine.rootObjects()
    assert roots, "the GUI failed to load"
    qt_app.processEvents()

    model = registry.get_service(CONTEXT_PROPERTIES["importVM"])
    button = roots[0].findChild(QObject, "importActionButton")
    assert button is not None, "the import action button is gone or renamed"

    def label():
        qt_app.processEvents()
        return button.property("text"), button.property("enabledAction")

    assert label() == ("Import all", True)

    # Driven through the state the view model would really be in, rather than
    # through a real stash: what is under test is the binding, not the queue.
    model._stash_active = True
    model._stash_queue = [1, 2]
    model.stashing_changed.emit()
    assert label() == ("Abort import", True)

    model._aborting = True
    model.stashing_changed.emit()
    text, enabled = label()
    assert text.startswith("Stopping"), text
    assert enabled is False, "pressing again cannot make the boundary arrive sooner"

    model._aborting = False
    model._stash_active = False
    model._stash_queue = []
    model.stashing_changed.emit()
    assert label() == ("Import all", True)

    del engine


def test_repeated_aborts_do_not_abort_the_process(qt_app, monkeypatch):
    """The crash the search cancel turned up, on the path abort now uses.

    A stash worker has no Qt parent, so the view model's reference to it is what
    keeps it alive; releasing that while its thread still runs is a delete from
    the wrong thread and Qt ends the process over it. `own_worker` is what stops
    that, and this is the shape of run that finds out — abort makes the worker
    return almost immediately after `done`, which is the exact window the
    normal path is too slow to hit. It does not fail if it regresses, it dies.
    """

    def dawdle(result, track_service, cover_service, should_stop=None,
               *, cover_bytes=None, registry=None, lyrics_service=None):
        time.sleep(0.002)
        return None if _stopped(should_stop) else 900 + int(result["uid"])

    monkeypatch.setattr(import_workers, "stash_imported_track", dawdle)
    model = ImportViewModel(
        import_service=StubImportService(),
        track_service=None,
        registry=registry_for(audio=FakeAudio()),
        import_settings=StubImports(),
    )

    for round_number in range(40):
        model._on_search_finished([_row(f"Song {i}") for i in range(6)])
        model.stashAll()
        time.sleep(0.001 * (round_number % 3))    # abort at varying depths
        model.abortStash()
        assert _pump_until(qt_app, lambda: not model.isStashing), (
            f"round {round_number}: the run never stopped"
        )
        assert model._stash_thread is None


def test_the_row_in_flight_cannot_be_queued_twice_while_it_unwinds(qt_app, vm, stash):
    """Its own button is still on screen; clicking it must not start a second."""
    uids = _load(vm, 2)
    vm.stashAll()
    assert _wait(lambda: stash.started == [uids[0]])
    vm.abortStash()

    vm.stashTrack(uids[0])

    stash.release(2)
    assert _pump_until(qt_app, lambda: not vm.isStashing)
    assert stash.started == [uids[0]], "the aborted row was started a second time"
