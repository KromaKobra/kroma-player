"""Where an imported track's cover art comes from, and when.

Three things are pinned here:

**A row's artwork is a list, walked best first.** Whatever identified the row
put the largest picture it knows of at the front. Not every candidate exists,
and a missing one can be served as HTTP 404 *whose body is a placeholder
image*, so anything that accepted "some bytes came back" would silently make
that placeholder the album art. The walk keys off a failed request, and it
stops at the first hit. Which URLs a catalogue offers, and in which order, is
the plugin's to say — its own tests pin that.

**A file nothing identified keeps its own picture.** With no URLs to walk —
no plugin, or nothing matched — the stash falls back to the art embedded in the
user's own file, which is what the import list was showing all along.

**The fetch happens during the previous row.** Stashes run one at a time and
spend most of that time transcoding and encoding on the CPU, so the next row's
artwork is pulled down the wire during it. What matters is that the optimisation
cannot change the outcome: a prefetch that misses, fails, or belongs to a row
that never runs has to leave the stash doing exactly what it did before.
"""
from __future__ import annotations

import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.importer import import_workers, stash  # noqa: E402
from app.viewmodels.import_view_model import ImportViewModel  # noqa: E402
from tests.conftest import OwnedByIdOnly  # noqa: E402
from tests.fakes.providers import FakeAudio, registry_for  # noqa: E402
from tests.fakes.providers import VIDEO, from_legacy  # noqa: E402

TIMEOUT = 5.0

URLS = [
    "https://img.example/big.webp",
    "https://img.example/big.jpg",
    "https://img.example/medium.webp",
    "https://img.example/small.jpg",
]


# ======================================================================
# The walk
# ======================================================================


def test_a_missing_candidate_falls_through_to_the_next(monkeypatch):
    asked: list[str] = []

    def fetch(url, **kw):
        asked.append(url)
        # The largest is absent, as it is for plenty of older uploads.
        return b"medium-image" if "medium.webp" in url else None

    monkeypatch.setattr(stash, "fetch_bytes", fetch)

    assert stash.fetch_first(URLS) == b"medium-image"
    # Stopped at the first hit rather than fetching every candidate.
    assert asked == URLS[:3]


def test_nothing_at_all_is_not_mistaken_for_artwork(monkeypatch):
    """The 404 placeholder trap: `None` is the only signal of a miss.

    `fetch_bytes` lets `urlopen` raise on 404 and swallows it, so a placeholder
    body never reaches a caller. This pins that the walk keys off that `None`
    and does not, say, take the shortest response.
    """
    monkeypatch.setattr(stash, "fetch_bytes", lambda url, **kw: None)

    assert stash.fetch_first(URLS) is None


def test_a_stop_ends_the_walk(monkeypatch):
    asked: list[str] = []
    monkeypatch.setattr(stash, "fetch_bytes", lambda url, **kw: asked.append(url))

    assert stash.fetch_first(URLS, should_stop=lambda: True) is None
    assert asked == []


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._payload


def _serve(monkeypatch, available: str | None):
    """Answer only the URL containing *available*; 404 the rest.

    Patches the socket rather than `fetch_bytes`, because `fetch_bytes` is
    what these tests are about — stubbing it would remove the logging they
    are checking and leave them passing on an empty log.
    """
    def urlopen(request, timeout=None):
        url = request.full_url
        if available and available in url:
            return _FakeResponse(b"image-bytes")
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)


def test_walking_past_a_missing_candidate_is_not_a_warning(monkeypatch, caplog):
    """Routine 404s must not be logged as problems.

    A list of candidates exists precisely because the best of them may not.
    Warning on each miss would put alarming lines per imported track into the
    log and bury the case that is actually worth seeing — every candidate
    missing, which the walk reports once, itself.
    """
    _serve(monkeypatch, "small.jpg")

    with caplog.at_level("WARNING", logger=stash.logger.name):
        assert stash.fetch_first(URLS) == b"image-bytes"

    assert caplog.records == [], f"warned on a routine miss: {caplog.text}"


def test_every_candidate_missing_is_still_reported(monkeypatch, caplog):
    """The one artwork failure worth a warning, reported once, not four times."""
    _serve(monkeypatch, None)

    with caplog.at_level("WARNING", logger=stash.logger.name):
        assert stash.fetch_first(URLS) is None

    assert len(caplog.records) == 1, f"expected one warning, got {caplog.text}"


def test_a_lone_fetch_still_warns_normally(monkeypatch, caplog):
    """Outside the walk a failure is unexpected, and still says so."""
    _serve(monkeypatch, None)

    with caplog.at_level("WARNING", logger=stash.logger.name):
        assert stash.fetch_bytes("https://example.invalid/cover.jpg") is None

    assert len(caplog.records) == 1


def test_a_row_with_no_urls_fetches_nothing(monkeypatch):
    monkeypatch.setattr(
        stash, "fetch_bytes",
        lambda url, **kw: pytest.fail("fetched without a URL"),
    )

    assert stash.fetch_first([]) is None


# ======================================================================
# The handover into the stash
# ======================================================================


@pytest.fixture
def stubbed_stash(monkeypatch, tmp_path):
    """`stash_imported_track` with its slow parts removed.

    Leaves the artwork decision — the only thing these tests are about — running
    for real, and records what reached `finalize_and_stash`.
    """
    source = tmp_path / "song.opus"
    source.write_bytes(b"not really opus")

    seen: dict = {}

    monkeypatch.setattr(stash, "ensure_opus", lambda src, out: src)
    monkeypatch.setattr(stash, "read_embedded_art", lambda path: None)

    def finalize(opus_path, track_info, cover_bytes, track_service,
                 cover_service=None, lyrics_service=None):
        seen["cover_bytes"] = cover_bytes
        seen["track_info"] = track_info
        return 7

    monkeypatch.setattr(stash, "finalize_and_stash", finalize)
    seen["result"] = {
        "source_path": str(source), "title": "T",
        "external_ids": {VIDEO: "vid1"}, "artwork_urls": list(URLS),
    }
    return seen


def test_supplied_artwork_is_used_without_fetching(monkeypatch, stubbed_stash):
    monkeypatch.setattr(
        stash, "fetch_first",
        lambda urls, **kw: pytest.fail("fetched despite being handed artwork"),
    )

    stash.stash_imported_track(
        stubbed_stash["result"], track_service=None, cover_bytes=b"prefetched",
    )

    assert stubbed_stash["cover_bytes"] == b"prefetched"


def test_without_supplied_artwork_it_fetches_its_own(monkeypatch, stubbed_stash):
    monkeypatch.setattr(stash, "fetch_first", lambda urls, **kw: b"fetched")

    stash.stash_imported_track(stubbed_stash["result"], track_service=None)

    assert stubbed_stash["cover_bytes"] == b"fetched"


def test_the_rows_ids_reach_the_track(monkeypatch, stubbed_stash):
    monkeypatch.setattr(stash, "fetch_first", lambda urls, **kw: None)

    stash.stash_imported_track(stubbed_stash["result"], track_service=None)

    assert stubbed_stash["track_info"]["external_ids"] == {VIDEO: "vid1"}


def test_a_file_nothing_identified_keeps_its_own_picture(monkeypatch, stubbed_stash):
    """The v1 gap: a row with nothing online used to arrive with no cover at all,
    though the import list had been drawing the file's own art the whole time."""
    monkeypatch.setattr(stash, "fetch_first", lambda urls, **kw: None)
    monkeypatch.setattr(stash, "read_embedded_art", lambda path: b"embedded")
    result = {**stubbed_stash["result"], "external_ids": {}, "artwork_urls": []}

    stash.stash_imported_track(result, track_service=None)

    assert stubbed_stash["cover_bytes"] == b"embedded"


def test_online_artwork_wins_over_the_files_own(monkeypatch, stubbed_stash):
    monkeypatch.setattr(stash, "fetch_first", lambda urls, **kw: b"online")
    monkeypatch.setattr(stash, "read_embedded_art", lambda path: b"embedded")

    stash.stash_imported_track(stubbed_stash["result"], track_service=None)

    assert stubbed_stash["cover_bytes"] == b"online"


# ======================================================================
# The prefetch, through the queue
# ======================================================================


class StubImportService(OwnedByIdOnly):
    def existing_ids(self, ids):
        return {}


class GatedStash:
    """Holds each row open until the test lets it go, recording its artwork."""

    def __init__(self) -> None:
        self._permits = threading.Semaphore(0)
        self.started: list[int] = []
        self.covers: dict[int, bytes | None] = {}

    def release(self, count: int = 1) -> None:
        for _ in range(count):
            self._permits.release()

    def __call__(self, result, track_service, cover_service, should_stop=None,
                 *, cover_bytes=None, registry=None, lyrics_service=None):
        uid = int(result["uid"])
        self.started.append(uid)
        self.covers[uid] = cover_bytes
        assert self._permits.acquire(timeout=TIMEOUT), "the test never released a stash"
        return 900 + uid


def _pump_until(qt_app, predicate, timeout: float = TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if predicate():
            return True
        time.sleep(0.002)
    return False


def _pump_for(qt_app, seconds: float) -> None:
    """Keep the event loop turning for a while, expecting nothing.

    The counterpart to `_pump_until`, for asserting something does *not*
    happen. A plain `sleep` would not do: the restart being watched for is
    delivered by a queued signal, so a test that only slept would report every
    regression as a pass.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.002)


def _row_legacy(title):
    return {
        "title": title,
        "artist": "A",
        "album": "",
        "duration": 100,
        "source_id": f"vid{title}",
        "cover_url": "",
        # Stands in for a URL: what matters is that each row has its own.
        "cover_master_url": f"vid{title}",
        "source_path": f"/music/{title}.opus",
        "not_found": False,
    }


def _row(*args, **kwargs):
    """`_row_legacy`, as a core row — see `tests.fakes.providers.from_legacy`."""
    return from_legacy(_row_legacy(*args, **kwargs))


@pytest.fixture
def gate(monkeypatch):
    gated = GatedStash()
    monkeypatch.setattr(import_workers, "stash_imported_track", gated)
    return gated


@pytest.fixture
def vm(qt_app, gate):
    """A view model that is quiet again before the test lets go of it.

    Teardown aborts first and only then waits, because waiting alone does not
    converge: while a run is still live, a finishing prefetch starts the next
    one, so `_prefetch_thread` can be None at the instant it is sampled and
    non-None immediately after. Aborting empties the queue and clears
    `_stash_active`, which is what stops new work from appearing underneath the
    wait.

    Both pairs are then sampled in a single predicate rather than one after the
    other, so the wait cannot return while the second is still alive. Getting
    this wrong does not fail a test — it drops the last reference to a worker
    whose thread is still running, which is the crash `own_worker` exists to
    prevent, and it surfaces as an occasional segfault in an unrelated test.
    """
    model = ImportViewModel(
        import_service=StubImportService(),
        track_service=None,
        registry=registry_for(audio=FakeAudio()),
    )
    yield model

    model.abortStash()
    gate.release(100)          # let whatever is mid-row off its semaphore
    quiet = _pump_until(
        qt_app,
        lambda: model._stash_thread is None and model._prefetch_thread is None,
    )
    assert quiet, "a worker thread outlived its test"


def _load(vm, count):
    vm._on_search_finished([_row(f"Song {i}") for i in range(count)])
    return [r["uid"] for r in vm._results]


def test_the_next_rows_art_arrives_before_its_stash_does(qt_app, vm, gate, monkeypatch):
    """The whole point: row 1's artwork is fetched while row 0 is being written."""
    monkeypatch.setattr(
        import_workers, "fetch_first",
        lambda urls, **kw: f"art-{urls[0]}".encode(),
    )
    uids = _load(vm, 2)
    vm.stashAll()

    # Row 0 is in flight and had nothing prefetched for it — it is the first.
    assert _pump_until(qt_app, lambda: gate.started == [uids[0]])
    assert gate.covers[uids[0]] is None

    # Row 1's art lands in the slot while row 0 is still held open.
    assert _pump_until(qt_app, lambda: vm._prefetch_bytes is not None)
    assert vm._prefetch_uid == uids[1]

    gate.release()
    assert _pump_until(qt_app, lambda: gate.started == uids)
    assert gate.covers[uids[1]] == b"art-vidSong 1"


def test_every_row_after_the_first_is_written_with_prefetched_art(
    qt_app, vm, gate, monkeypatch,
):
    """The feature, stated over a whole run rather than a single handover.

    Only the first row can pay for its own artwork; from then on each row is
    written with art that was fetched during its predecessor. Three separate
    triggers can start a prefetch — a row starting, the queue being filled, and
    the slot freeing — so this is what notices if the overlap stops happening,
    which no single-handover test can.
    """
    monkeypatch.setattr(
        import_workers, "fetch_first",
        lambda urls, **kw: f"art-{urls[0]}".encode(),
    )
    uids = _load(vm, 4)
    vm.stashAll()

    for index, uid in enumerate(uids):
        assert _pump_until(qt_app, lambda: uid in gate.covers), f"row {index} never ran"
        gate.release()

    assert gate.started == uids
    assert gate.covers[uids[0]] is None
    for uid, row in zip(uids[1:], ("Song 1", "Song 2", "Song 3")):
        assert gate.covers[uid] == f"art-vid{row}".encode(), f"row {row} fetched late"


def test_the_slot_is_emptied_by_the_row_it_belongs_to(qt_app, vm, gate, monkeypatch):
    monkeypatch.setattr(import_workers, "fetch_first", lambda urls, **kw: b"art")
    uids = _load(vm, 2)
    vm.stashAll()

    assert _pump_until(qt_app, lambda: vm._prefetch_bytes is not None)
    gate.release()
    assert _pump_until(qt_app, lambda: gate.started == uids)

    # Consumed exactly once: nothing is left holding the bytes afterwards.
    assert vm._prefetch_bytes is None
    assert vm._prefetch_uid == -1


def test_a_failed_prefetch_leaves_the_stash_to_try_again(qt_app, vm, gate, monkeypatch):
    """A miss must be banked as *no prefetch*, not as "this row has no art".

    Otherwise one failed request permanently costs that row its artwork, and
    nothing anywhere would retry it.
    """
    monkeypatch.setattr(import_workers, "fetch_first", lambda urls, **kw: None)
    uids = _load(vm, 2)
    vm.stashAll()

    assert _pump_until(qt_app, lambda: gate.started == [uids[0]])
    assert _pump_until(qt_app, lambda: vm._prefetch_uid == uids[1])
    # Settled, and empty — so the stash is handed None and fetches for itself.
    assert vm._prefetch_bytes is None

    gate.release()
    assert _pump_until(qt_app, lambda: gate.started == uids)
    assert gate.covers[uids[1]] is None


def test_a_failing_row_does_not_spin_the_prefetch(qt_app, vm, gate, monkeypatch):
    """The slot stays claimed after a miss, so it is not re-tried in a loop.

    `_on_prefetch_stopped` re-enters `_maybe_start_prefetch` to pick up the
    next target. If a settled-but-empty slot were treated as free, a row whose
    art 404s quickly would restart forever for the length of the current stash.
    """
    attempts: list[str] = []

    def fetch(urls, **kw):
        attempts.append(urls[0])
        return None

    monkeypatch.setattr(import_workers, "fetch_first", fetch)
    _load(vm, 2)
    vm.stashAll()

    assert _pump_until(qt_app, lambda: len(attempts) >= 1)
    # Pumped, not slept: the restart would arrive on `thread.finished`, which a
    # sleeping test would never deliver and would therefore never catch.
    _pump_for(qt_app, 0.25)

    assert len(attempts) == 1, f"prefetch restarted: {attempts}"


def test_aborting_drops_the_slot(qt_app, vm, gate, monkeypatch):
    monkeypatch.setattr(import_workers, "fetch_first", lambda urls, **kw: b"art")
    uids = _load(vm, 3)
    vm.stashAll()

    assert _pump_until(qt_app, lambda: vm._prefetch_bytes is not None)
    vm.abortStash()

    # The queued rows are gone, so the row the slot named is gone with them.
    assert vm._prefetch_uid == -1
    assert vm._prefetch_bytes is None

    gate.release(10)
    assert _pump_until(qt_app, lambda: not vm.isStashing)
    assert gate.started == [uids[0]], "an aborted row was written anyway"


def test_a_lingering_prefetch_does_not_hold_the_abort_open(qt_app, vm, gate, monkeypatch):
    """Abort answers to the stash queue only.

    A prefetch is not work the user asked for, so a slow one must not keep
    `isStashing` true after the row in flight has stopped — that would leave the
    abort button hanging on a network timeout.
    """
    release_fetch = threading.Event()

    def slow_fetch(urls, **kw):
        release_fetch.wait(TIMEOUT)
        return b"art"

    monkeypatch.setattr(import_workers, "fetch_first", slow_fetch)
    _load(vm, 3)
    vm.stashAll()

    assert _pump_until(qt_app, lambda: vm._prefetch_thread is not None)
    vm.abortStash()
    gate.release(10)

    # The fetch is still parked, and the run is over regardless.
    assert _pump_until(qt_app, lambda: not vm.isStashing)
    release_fetch.set()


def test_the_csv_path_prefetches_nothing(qt_app, vm, monkeypatch):
    """CSV rows are downloaded, and a download brings its own artwork.

    Staged rather than driven through `stashAll`, because the CSV path builds a
    download worker instead of a stash worker and this is only about the guard.
    `_stash_active` and a queued row are set by hand so the check under test is
    the one that actually stops it — otherwise the "no run in progress" guard
    above it would return first and the test would pass either way.

    CSV mode is set *before* the rows are loaded, because that is when a row is
    stamped with what it will do when saved. The guard asks the row, so rows
    loaded first and re-labelled after would still be the local ones they were
    built as — which is the point of stamping them.
    """
    monkeypatch.setattr(
        import_workers, "fetch_first",
        lambda urls, **kw: pytest.fail("prefetched on the download path"),
    )
    vm._set_download_mode(True)
    uids = _load(vm, 2)
    vm._stash_active = True
    vm._stash_queue.append(uids[1])

    vm._maybe_start_prefetch()

    assert vm._prefetch_thread is None
    assert vm._prefetch_uid == -1


def test_nothing_is_prefetched_outside_a_run(qt_app, vm, monkeypatch):
    """No speculating when the user is not importing.

    The mirror of the test above: a queued row, but no run in progress.
    """
    monkeypatch.setattr(
        import_workers, "fetch_first",
        lambda urls, **kw: pytest.fail("prefetched with no stash running"),
    )
    uids = _load(vm, 2)
    vm._stash_queue.append(uids[1])

    vm._maybe_start_prefetch()

    assert vm._prefetch_thread is None
    assert vm._prefetch_uid == -1
