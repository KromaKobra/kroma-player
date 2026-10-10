"""Downloads run one at a time, and the view model is what makes that true.

The limit exists because every download ends by writing through the single
SQLAlchemy session the whole application shares, which is not thread-safe. So
what these tests care about is not the tidiness of the state but the two things
a concurrent writer could sneak through:

* a second worker is never *constructed*, let alone started, while one is live;
* the slot is freed when the thread has **finished**, not when the work has —
  releasing on the work would let the next download start beside a worker that
  is still unwinding, which is the case the limit is meant to exclude.

Neither real threads nor a network are involved: `QThread` and
`FetchAudioWorker` are replaced with stand-ins that record what they were asked
to do, so the ordering under test is driven by hand rather than waited for.
Downloads are keyed by the row's uid — a row is what the user pressed.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Signal

from app.viewmodels import search_view_model as svm
from app.viewmodels.search_view_model import SearchViewModel
from tests.fakes.providers import FakeAudio, FakeRegistry, FakeTrackSource, row


class _FakeWorker(QObject):
    """A FetchAudioWorker that does nothing until a test tells it to."""

    finished = Signal(int, int)
    failed = Signal(int, str)
    done = Signal(int)
    progress = Signal(int, str, float)

    #: Every worker ever constructed, so "none was started" can be asserted
    #: rather than assumed — the download must not even be built.
    built: list["_FakeWorker"] = []

    def __init__(self, uid, row, track_service, registry, cover_service=None,
                 metadata_override=None, lyrics_service=None):
        super().__init__()
        self.uid = uid
        _FakeWorker.built.append(self)

    def cancel(self):
        pass

    def moveToThread(self, thread):  # noqa: N802 - the fake thread is not a QThread
        pass

    def run(self):
        pass

    def deleteLater(self):  # noqa: N802 - no event loop here to process it
        pass


class _FakeThread(QObject):
    """A QThread stand-in whose start/quit/finish a test drives by hand.

    ``quit`` deliberately does not emit ``finished``, exactly as the real one
    does not: it asks the thread's event loop to stop, and ``finished`` arrives
    afterwards. Keeping those separate is the whole point — it is the window in
    which a worker is done working but not yet gone.

    ``finish`` then calls ``wait`` before anything else hears about it, because
    that is the order ``own_worker`` wires the real signal up in and there is no
    real thread here to enforce it.
    """

    started = Signal()
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.start_count = 0
        self.quit_count = 0
        self.wait_count = 0

    def wait(self, *args):
        self.wait_count += 1
        return True

    def start(self):
        self.start_count += 1
        # Not `started.emit()`: the real thread runs the worker on its own
        # thread, and a test that wants the work to happen says so itself.

    def quit(self):
        self.quit_count += 1

    def finish(self):
        """What the real thread's event loop reports once it has stopped."""
        self.finished.emit()

    def deleteLater(self):  # noqa: N802
        pass


class _StubTrackService:
    def find_owned(self, rows):
        return [None for _ in rows]


@pytest.fixture
def registry():
    """An audio source on, so these tests are about the cap and nothing else."""
    return FakeRegistry(sources=[FakeTrackSource()], audio=FakeAudio())


@pytest.fixture
def vm(repo, cache, event_bus, registry, monkeypatch):
    _FakeWorker.built = []
    monkeypatch.setattr(svm, "QThread", _FakeThread)
    monkeypatch.setattr(svm, "FetchAudioWorker", _FakeWorker)
    model = SearchViewModel(
        track_repository=repo,
        registry=registry,
        event_bus=event_bus,
        track_service=_StubTrackService(),
        row_cache=cache,
    )
    source = registry.sources[0]
    model.setSource(source.key)
    model._on_web_search_finished(source.key, [
        row(f"Song {n}", "A", video=f"video{n:06d}") for n in range(20)
    ])
    yield model
    model._download_thread = None
    model._download_worker = None


def _uids(vm):
    return list(vm._uids)


def _record(signal) -> list:
    seen: list = []
    signal.connect(seen.append)
    return seen


def A(vm):
    return _uids(vm)[0]


def B(vm):
    return _uids(vm)[1]


# -- the cap -------------------------------------------------------------


def test_one_download_starts_and_claims_the_slot(vm):
    vm.downloadRow(A(vm))

    assert vm.isDownloading is True
    assert vm.downloadingUid == A(vm)
    assert vm._download_thread.start_count == 1


def test_a_second_track_is_refused_while_one_is_downloading(vm):
    busy = _record(vm.download_busy)
    vm.downloadRow(A(vm))
    vm.downloadRow(B(vm))

    assert busy == [B(vm)]
    assert vm.downloadingUid == A(vm), "the running one is left alone"
    assert [w.uid for w in _FakeWorker.built] == [A(vm)], (
        "the refused download must not even be constructed"
    )


def test_a_click_spree_starts_exactly_one_download(vm):
    """The reported case: many buttons pressed before any of them finishes."""
    busy = _record(vm.download_busy)
    for n in range(20):
        vm.downloadRow(_uids(vm)[n])

    assert len(_FakeWorker.built) == 1
    assert len(busy) == 19
    assert vm.downloadingUid == _uids(vm)[0]


def test_clicking_the_downloading_row_again_says_nothing(vm):
    """Asking for what is already happening is not a refusal to report."""
    busy = _record(vm.download_busy)
    blocked = []
    vm.download_blocked.connect(lambda uid, reason: blocked.append(uid))
    vm.downloadRow(A(vm))
    vm.downloadRow(A(vm))

    assert busy == []
    assert blocked == []
    assert len(_FakeWorker.built) == 1


def test_an_unknown_row_neither_starts_nor_refuses(vm):
    busy = _record(vm.download_busy)
    vm.downloadRow(-1)
    vm.downloadRow(999_999)

    assert busy == []
    assert vm.isDownloading is False
    assert _FakeWorker.built == []


# -- when the slot is freed ----------------------------------------------


def test_the_slot_is_still_held_while_the_worker_unwinds(vm):
    """`done` means the work is over, not that the thread is gone."""
    vm.downloadRow(A(vm))
    worker, thread = _FakeWorker.built[0], vm._download_thread

    worker.finished.emit(A(vm), 42)
    worker.done.emit(A(vm))

    assert thread.quit_count == 1, "the thread was asked to stop"
    assert vm.isDownloading is True, "but it has not stopped yet"

    busy = _record(vm.download_busy)
    vm.downloadRow(B(vm))
    assert busy == [B(vm)]
    assert len(_FakeWorker.built) == 1, "no second worker beside the first"


def test_the_next_download_runs_once_the_thread_has_finished(vm):
    vm.downloadRow(A(vm))
    worker, thread = _FakeWorker.built[0], vm._download_thread
    worker.done.emit(A(vm))
    thread.finish()

    assert vm.isDownloading is False
    assert vm.downloadingUid == -1
    assert vm._download_worker is None, "and the references went with it"

    vm.downloadRow(B(vm))
    assert vm.downloadingUid == B(vm)
    assert len(_FakeWorker.built) == 2


def test_a_failed_download_frees_the_slot_too(vm):
    """Whatever went wrong, the next download must not be locked out by it."""
    vm.downloadRow(A(vm))
    worker, thread = _FakeWorker.built[0], vm._download_thread
    worker.failed.emit(A(vm), "the source fell over")
    worker.done.emit(A(vm))
    thread.finish()

    assert vm.isDownloading is False
    vm.downloadRow(B(vm))
    assert vm.downloadingUid == B(vm)


def test_the_state_change_is_announced_at_both_ends(vm):
    """QML dims and undims the other buttons off this signal."""
    changes = []
    vm.downloading_changed.connect(lambda: changes.append(vm.downloadingUid))

    vm.downloadRow(A(vm))
    worker, thread = _FakeWorker.built[0], vm._download_thread
    worker.done.emit(A(vm))
    thread.finish()

    assert changes == [A(vm), -1]


def test_a_refusal_announces_nothing(vm):
    """A turned-away click must not make the running row's spinner flicker."""
    vm.downloadRow(A(vm))
    changes = []
    vm.downloading_changed.connect(changes.append)

    vm.downloadRow(B(vm))
    assert changes == []


# -- against the other refusal -------------------------------------------


def test_no_source_is_answered_as_no_source_not_as_busy(vm, registry):
    """Turned off mid-download, the next click is told the truth that matters.

    Both refusals apply, and "turn a plugin on" is the one the user can act on
    — reporting "busy" would send them to wait for a queue that would refuse
    them again at the end of it.
    """
    vm.downloadRow(A(vm))
    registry.audio = None

    busy = _record(vm.download_busy)
    blocked = []
    vm.download_blocked.connect(lambda uid, reason: blocked.append(uid))
    vm.downloadRow(B(vm))

    assert blocked == [B(vm)]
    assert busy == []
    assert len(_FakeWorker.built) == 1


# -- the GUI's view of it ------------------------------------------------


@pytest.mark.parametrize("name", ["downloadingUid", "isDownloading"])
def test_the_gui_cannot_assign_the_download_state(vm, name):
    """A writable property would be a way to hand out the slot from QML."""
    meta = vm.metaObject().property(vm.metaObject().indexOfProperty(name))

    assert meta.isValid(), f"{name} is not exposed to QML at all"
    assert meta.isWritable() is False, "a writable property would be a bypass"
