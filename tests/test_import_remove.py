"""Taking rows back out of the import list.

The import list accumulates — several folders, a CSV, files paired by hand — so
it is something the user builds up over a few minutes. Until now the only way to
take anything out of it was Done, which threw away the lot. One unwanted track in
a folder of forty meant starting the whole import again.

"Remove from list" is the small opposite of that. It deletes nothing: these rows
are candidates that have not been saved, so removing one only says "not this
time", and a row that *was* saved stays in the library with only its receipt
dropped.

What the tests are mostly about is that a row is in four places at once — the
list, the selection, the stash queue, and possibly the cover-prefetch slot — and
all four have to let go of it together. The queue is the one that shows: a stale
uid left in there keeps `isStashing` true, and the toolbar goes on offering to
abort a run with nothing left in it.

The row being written *right now* is the deliberate exception. It is already
inside a transcode or a download; removing it would not stop it, only hide it,
and the track would arrive in the library with no row to show for it. Stopping a
run is what Abort is for.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.importer.media_probe import ProbedFile  # noqa: E402
from app.library.roles import FIELD_TO_ROLE  # noqa: E402
from app.viewmodels.import_view_model import ImportViewModel  # noqa: E402
from tests.conftest import OwnedByIdOnly  # noqa: E402
from tests.fakes.providers import FakeAudio, registry_for  # noqa: E402
from tests.fakes.providers import from_legacy  # noqa: E402


# -- doubles ---------------------------------------------------------------


class _StubImportService(OwnedByIdOnly):
    def __init__(self, existing=None):
        self.existing = existing or {}

    def existing_ids(self, ids):
        return {
            yid: self.existing[yid]
            for yid in ids
            if yid and yid in self.existing
        }


class _StubDownloads:
    allowed = True


class _StubImports:
    """Rows that reach the library stay listed.

    Removing by hand is the subject here, and a row that is already in the
    library is one of the things it has to be able to take out. The shipped
    default would have taken it already — see tests/test_import_clear.py.
    """
    clear_imported = False


@pytest.fixture
def vm(qt_app):
    return ImportViewModel(
        import_service=_StubImportService(),
        track_service=None,
        registry=registry_for(audio=FakeAudio()),
        import_settings=_StubImports(),
    )


# -- helpers ---------------------------------------------------------------


def _read(vm, row, key):
    return vm.data(vm.index(row, 0, QModelIndex()), FIELD_TO_ROLE[key])


def _row_legacy(title, *, not_found=False):
    return {
        "title": title,
        "artist": "A",
        "album": "",
        "duration": 100,
        "source_id": "" if not_found else f"vid-{title}",
        "cover_url": "",
        "cover_master_url": "",
        "source_path": f"/music/{title}.opus",
        "release_date": None,
        "not_found": not_found,
    }


def _row(*args, **kwargs):
    """`_row_legacy`, as a core row — see `tests.fakes.providers.from_legacy`."""
    return from_legacy(_row_legacy(*args, **kwargs))


def _load(vm, titles):
    """Fill the list and return its uids, in row order."""
    vm._on_search_finished([_row(t) for t in titles])
    return [r["uid"] for r in vm._results]


def _titles(vm):
    return [_read(vm, i, "title") for i in range(vm.result_count)]


# ======================================================================
# The list
# ======================================================================


def test_one_selected_row_goes_and_the_rest_stay_put(vm):
    uids = _load(vm, ["One", "Two", "Three"])
    vm.setSelected(uids[1], True)

    vm.removeSelected()

    assert _titles(vm) == ["One", "Three"]
    assert [r["uid"] for r in vm._results] == [uids[0], uids[2]]


def test_a_broken_up_selection_takes_exactly_those_rows(vm):
    """Rows 0, 2 and 4 of five: three separate runs, and where an off-by-one hides."""
    uids = _load(vm, ["Zero", "One", "Two", "Three", "Four"])
    vm.setSelectedMultiple([uids[0], uids[2], uids[4]])

    vm.removeSelected()

    assert _titles(vm) == ["One", "Three"]


def test_a_contiguous_selection_is_one_run(vm):
    uids = _load(vm, ["Zero", "One", "Two", "Three"])
    vm.setSelectedMultiple([uids[1], uids[2]])

    vm.removeSelected()

    assert _titles(vm) == ["Zero", "Three"]


def test_removing_everything_puts_the_tab_back(vm):
    """An empty list is the state Done leaves behind, and reads the same."""
    uids = _load(vm, ["One", "Two"])
    vm.setSelectedMultiple(uids)

    vm.removeSelected()

    assert vm.result_count == 0
    assert vm.hasResults is False


def test_nothing_selected_is_a_no_op(vm):
    _load(vm, ["One", "Two"])
    counts: list[int] = []
    vm.result_count_changed.connect(lambda: counts.append(vm.result_count))

    vm.removeSelected()

    assert vm.result_count == 2
    assert counts == [], "a no-op must not announce a change"


# ======================================================================
# The header's totals
# ======================================================================


def test_the_totals_come_down_with_the_rows(vm):
    vm = ImportViewModel(
        import_service=_StubImportService({"vid-Known": 42}),
        track_service=None,
        registry=registry_for(audio=FakeAudio()),
        import_settings=_StubImports(),
    )
    vm._on_search_finished(
        [_row("One"), _row("Known"), _row("Gone", not_found=True)]
    )
    assert (vm.foundCount, vm.notFoundCount, vm.inDatabaseCount) == (2, 1, 1)
    uids = [r["uid"] for r in vm._results]

    vm.setSelectedMultiple([uids[1], uids[2]])
    vm.removeSelected()

    assert vm.foundCount == 1, "the matched row that went"
    assert vm.notFoundCount == 0, "the unmatched row that went"
    assert vm.inDatabaseCount == 0, "the row that was already in the library"


def test_an_unmatched_row_stops_being_offered_once_removed(vm):
    """`unmatchedImportableCount` is what the "import these too?" menu quotes."""
    vm._on_search_finished([_row("Gone", not_found=True), _row("One")])
    assert vm.unmatchedImportableCount == 1

    vm.setSelected(vm.uidAt(0), True)
    vm.removeSelected()

    assert vm.unmatchedImportableCount == 0


# ======================================================================
# The stash queue
# ======================================================================


def test_a_queued_row_leaves_the_queue_with_the_list(vm):
    """Otherwise the button goes on offering to abort a run with nothing in it."""
    uids = _load(vm, ["One", "Two"])
    # Queued by hand: `stashAll` would start the first one for real.
    vm._stash_pending = set(uids)
    vm._stash_queue = list(uids)
    assert vm.isStashing is True

    vm.setSelectedMultiple(uids)
    vm.removeSelected()

    assert vm._stash_queue == []
    assert vm._stash_pending == set()
    assert vm.isStashing is False


def test_removing_one_queued_row_leaves_the_others_queued(vm):
    uids = _load(vm, ["One", "Two", "Three"])
    vm._stash_pending = set(uids)
    vm._stash_queue = list(uids)

    vm.setSelected(uids[1], True)
    vm.removeSelected()

    assert vm._stash_queue == [uids[0], uids[2]]
    assert vm.isStashing is True


def test_the_prefetch_slot_lets_go_of_a_removed_row(vm):
    """The slot names a uid; a row that has gone must not keep it reserved."""
    uids = _load(vm, ["One", "Two"])
    vm._prefetch_uid = uids[0]
    vm._prefetch_bytes = b"art"

    vm.setSelected(uids[0], True)
    vm.removeSelected()

    assert vm._prefetch_uid == -1
    assert vm._prefetch_bytes is None


def test_a_prefetch_for_a_surviving_row_is_left_alone(vm):
    uids = _load(vm, ["One", "Two"])
    vm._prefetch_uid = uids[1]
    vm._prefetch_bytes = b"art"

    vm.setSelected(uids[0], True)
    vm.removeSelected()

    assert vm._prefetch_uid == uids[1]
    assert vm._prefetch_bytes == b"art"


# ======================================================================
# The row being written right now
# ======================================================================


def test_the_row_in_flight_stays_and_the_rest_go(vm):
    uids = _load(vm, ["One", "Two", "Three"])
    vm._stash_active = True
    vm._set_stashing(uids[1])

    vm.setSelectedMultiple(uids)
    vm.removeSelected()

    assert _titles(vm) == ["Two"], "the row being written keeps its place"


def test_the_count_says_how_many_will_really_go(vm):
    """The menu label is bound to this; quoting 3 and taking 2 would be a lie."""
    uids = _load(vm, ["One", "Two", "Three"])
    vm.setSelectedMultiple(uids)
    assert vm.removableSelectedCount == 3

    vm._stash_active = True
    vm._set_stashing(uids[1])

    assert vm.removableSelectedCount == 2


def test_the_count_is_re_announced_when_a_row_goes_in_flight(vm):
    """The menu label is a binding, so a right answer nobody hears is no answer.

    `removableSelectedCount` notifies on `stats_changed`, but the row it excludes
    changes through the stashing path. Caught by driving the real QML: the label
    read "Remove 3 from list" while the property already said 2.
    """
    uids = _load(vm, ["One", "Two", "Three"])
    vm.setSelectedMultiple(uids)
    heard: list[int] = []
    vm.stats_changed.connect(lambda: heard.append(vm.removableSelectedCount))

    vm._stash_active = True
    vm._set_stashing(uids[1])

    assert heard and heard[-1] == 2


def test_the_count_is_re_announced_when_the_run_ends(vm):
    """The other half of "which row is in flight" is `_stash_active`."""
    uids = _load(vm, ["One", "Two"])
    vm.setSelectedMultiple(uids)
    vm._stash_active = True
    vm._set_stashing(uids[0])
    heard: list[int] = []
    vm.stats_changed.connect(lambda: heard.append(vm.removableSelectedCount))

    vm._set_stashing(-1)
    vm._on_stash_stopped()

    assert heard and heard[-1] == 2


def test_selecting_only_the_in_flight_row_offers_nothing(vm):
    uids = _load(vm, ["One", "Two"])
    vm._stash_active = True
    vm._set_stashing(uids[0])
    vm.setSelected(uids[0], True)

    assert vm.removableSelectedCount == 0
    vm.removeSelected()
    assert vm.result_count == 2


def test_a_finished_row_is_removable(vm):
    """It is in the library; taking the receipt off the list changes nothing there."""
    uids = _load(vm, ["One", "Two"])
    vm._results[0]["id"] = 77          # as `_on_stash_finished` would leave it
    vm.setSelected(uids[0], True)

    assert vm.removableSelectedCount == 1
    vm.removeSelected()

    assert _titles(vm) == ["Two"]


# ======================================================================
# Afterwards
# ======================================================================


def test_the_selection_forgets_the_rows_that_went(vm):
    uids = _load(vm, ["One", "Two", "Three"])
    vm.setSelectedMultiple([uids[0], uids[1]])

    vm.removeSelected()

    assert vm._selected_ids == set()


def test_a_removed_file_can_be_imported_again(vm):
    """The skip is about what is on screen; taking a row off takes back the veto."""
    _load(vm, ["One"])
    probed = ProbedFile(path=Path("/music/One.opus"), title="One", artist="A")
    assert vm._without_listed([probed]) == ([], 1)

    vm.setSelected(vm.uidAt(0), True)
    vm.removeSelected()

    kept, skipped = vm._without_listed([probed])
    assert len(kept) == 1
    assert skipped == 0
