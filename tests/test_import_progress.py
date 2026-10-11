"""The row being imported says so, and only that row.

Stashes run one at a time through a queue, so at any moment exactly one row is
being written and the rest are waiting or done. The status cell draws a spinner
on that row, and it reads which row from ``stashingUid`` rather than latching a
flag when ``stash_started`` arrives — the cell lives in a list delegate, and a
delegate that scrolls out of the cache buffer is destroyed and rebuilt with its
local state back at the default. Latched, the spinner would disappear from the
one row that is genuinely still working, the moment the user scrolled.

So what is pinned here is that the property tracks the queue exactly: one row at
a time, in order, and -1 whenever nothing is being written. The stash itself is
replaced with a gate the test opens, because what a stash *does* belongs to the
stash worker and is tested elsewhere.
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


class GatedStash:
    """Stands in for `stash_imported_track`, one row at a time, on demand."""

    def __init__(self) -> None:
        self._permits = threading.Semaphore(0)
        self.started: list[int] = []

    def release(self, count: int = 1) -> None:
        for _ in range(count):
            self._permits.release()

    def __call__(self, result, track_service, cover_service, should_stop=None,
                 *, cover_bytes=None, registry=None, lyrics_service=None):
        self.started.append(result["uid"])
        assert self._permits.acquire(timeout=TIMEOUT), "the test never released a stash"
        return 900 + int(result["uid"])


def _pump_until(qt_app, predicate, timeout: float = TIMEOUT) -> bool:
    """Run the event loop until *predicate* holds. For queued signals."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if predicate():
            return True
        time.sleep(0.002)
    return False


def _wait(predicate, timeout: float = TIMEOUT) -> bool:
    """Spin until *predicate* holds. For what the worker thread records.

    `stashingUid` is set on the main thread as the thread is launched, so it is
    true the moment the call returns; the stash itself is reached a moment later
    on the worker. The two need different waits and conflating them is a flaky
    test, not a flaky feature.
    """
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
    )
    yield model
    stash.release(100)
    _pump_until(qt_app, lambda: model._stash_thread is None)


def _load(vm, count):
    vm._on_search_finished([_row(f"Song {i}") for i in range(count)])
    return [r["uid"] for r in vm._results]


def test_nothing_is_being_written_before_anything_starts(vm):
    _load(vm, 2)
    assert vm.stashingUid == -1


def test_the_queue_reports_one_row_at_a_time_in_order(qt_app, vm, stash):
    """The whole feature: "Import all" says which file it is on, and only that one."""
    uids = _load(vm, 3)

    vm.stashAll()

    # Set synchronously as the worker is launched, so the first row is already
    # named by the time the click returns.
    assert vm.stashingUid == uids[0]
    assert _wait(lambda: stash.started == [uids[0]]), "the first row never started"
    assert stash.started == [uids[0]], "the other two are queued, not running"

    stash.release()
    assert _pump_until(qt_app, lambda: vm.stashingUid == uids[1])
    assert _wait(lambda: stash.started == [uids[0], uids[1]])

    stash.release()
    assert _pump_until(qt_app, lambda: vm.stashingUid == uids[2])

    stash.release()
    assert _pump_until(qt_app, lambda: vm.stashingUid == -1), "left spinning at the end"
    assert stash.started == uids


def test_a_single_rows_own_button_names_that_row(qt_app, vm, stash):
    uids = _load(vm, 3)

    vm.stashTrack(uids[1])

    assert vm.stashingUid == uids[1]

    stash.release()
    assert _pump_until(qt_app, lambda: vm.stashingUid == -1)


def test_the_property_notifies_so_a_binding_follows_it(qt_app, vm, stash):
    """A QML binding is only as good as the notify signal behind it.

    Consecutive repeats are collapsed before comparing: `stashing_changed` backs
    three properties, so the run ending re-announces a `stashingUid` that has
    not moved. A binding re-evaluating once more than it strictly had to is
    free; one that never hears is the bug.
    """
    uids = _load(vm, 2)
    seen: list[int] = []
    vm.stashing_changed.connect(lambda: seen.append(vm.stashingUid))

    vm.stashAll()
    stash.release()
    assert _pump_until(qt_app, lambda: vm.stashingUid == uids[1])
    stash.release()
    assert _pump_until(qt_app, lambda: vm.stashingUid == -1)

    changes = [uid for i, uid in enumerate(seen) if i == 0 or uid != seen[i - 1]]
    assert changes == [uids[0], -1, uids[1], -1], (
        "every change has to be announced, including the gaps between rows"
    )


def test_the_status_cell_follows_the_queue(qt_app, vm, tmp_path):
    """The binding itself, against a real view model.

    Not covered by `test_gui_packages`, which loads the whole GUI but with an
    empty import list — the status cells are inside a Loader that never becomes
    active, so nothing there ever evaluates this. A renamed property would sail
    through it and fail only in front of the user.
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine

    from app.gui.gui_paths import bundled_gui_root

    nocturne = bundled_gui_root() / "nocturne"
    source = tmp_path / "Probe.qml"
    source.write_text(
        """
        import QtQuick
        import Nocturne
        import "file:///%s"

        Item {
            property alias cellBusy: cell.busy
            TrackStatusCell {
                id: cell
                statusMode: "import"
                uid: 7
                importState: "stash"
            }
        }
        """
        % (nocturne / "components" / "tracks").as_posix(),
        encoding="utf-8",
    )

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(nocturne))
    engine.rootContext().setContextProperty("importVM", vm)
    engine.rootContext().setContextProperty("searchVM", None)
    warnings: list = []
    engine.warnings.connect(warnings.extend)
    engine.load(QUrl.fromLocalFile(str(source)))

    roots = engine.rootObjects()
    assert roots, "probe failed to load:\n" + "\n".join(w.toString() for w in warnings)
    cell = roots[0]

    def busy():
        qt_app.processEvents()
        return cell.property("cellBusy")

    assert busy() is False
    vm._set_stashing(7)
    assert busy() is True, "the queue is on this row and it is not spinning"
    vm._set_stashing(8)
    assert busy() is False, "another row's turn should clear this one"
    vm._set_stashing(-1)
    assert busy() is False

    del engine


def test_a_failed_row_stops_spinning_too(qt_app, vm, monkeypatch):
    """`done` fires on every path out of the worker, so the spinner always clears."""

    def explode(result, track_service, cover_service, *args, **kwargs):
        raise RuntimeError("no")

    monkeypatch.setattr(import_workers, "stash_imported_track", explode)
    uids = _load(vm, 1)

    failures: list[int] = []
    vm.stash_failed.connect(lambda uid, message: failures.append(uid))

    vm.stashAll()
    assert _pump_until(qt_app, lambda: vm.stashingUid == -1)
    assert failures == uids
