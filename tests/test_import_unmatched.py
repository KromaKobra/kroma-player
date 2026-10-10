"""How the import screen treats a file that matched nothing.

Such a file can still be imported — its own tags carry a title and an artist,
which is enough to make a library entry — but it arrives without the album,
artwork or release date a match would have supplied. Two consequences, both
tested here.

The first is a decision, so "Import all" asks instead of making it: which rows
a given answer queues, and the number the question quotes. Neither of those
tests runs a stash — `_pump_stash` is stubbed out, because what a stash *does*
is the stash worker's business and is tested elsewhere. What matters is which
rows get that far.

The second is that an unmatched row has to *look* unmatched. Nothing else on
the row says so: the title and artist come off the file either way, so a row
Kroma could not identify is otherwise indistinguishable from one it is sure
about. It is marked, not disabled — still importable, just not vouched for.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.viewmodels.import_view_model import ImportViewModel  # noqa: E402
from tests.conftest import OwnedByIdOnly  # noqa: E402
from tests.fakes.providers import FakeAudio, registry_for  # noqa: E402
from tests.fakes.providers import from_legacy  # noqa: E402


class StubImportService(OwnedByIdOnly):
    def existing_ids(self, ids):
        return {}


class StubDownloads:
    allowed = True


def _row_legacy(title="Song", artist="A", *, matched=True, source_id="vid00000001"):
    """One search result, in the shape the worker hands over."""
    return {
        "title": title,
        "artist": artist,
        "album": "",
        "duration": 100,
        "source_id": source_id if matched else "",
        "cover_url": "",
        "source_path": f"/music/{title}.opus",
        "not_found": not matched,
    }


def _row(*args, **kwargs):
    """`_row_legacy`, as a core row — see `tests.fakes.providers.from_legacy`."""
    return from_legacy(_row_legacy(*args, **kwargs))


@pytest.fixture
def vm(qt_app, monkeypatch):
    model = ImportViewModel(
        import_service=StubImportService(),
        track_service=None,
        registry=registry_for(audio=FakeAudio()),
    )
    # Queueing is the subject; running the queue is not.
    monkeypatch.setattr(model, "_pump_stash", lambda: None)
    return model


def _load(vm, rows):
    """Put *rows* into the model the way a finished search would."""
    vm._on_search_finished(rows)
    return [r["uid"] for r in vm._results]


# -- the number the question quotes ---------------------------------------


def test_the_count_is_of_rows_the_answer_actually_decides(vm):
    _load(
        vm,
        [
            _row("Matched"),
            _row("Unmatched", matched=False),
            _row("Also unmatched", matched=False),
        ],
    )
    assert vm.unmatchedImportableCount == 2
    assert vm.notFoundCount == 2


def test_a_file_with_nothing_to_name_it_is_not_counted(vm):
    """It cannot be imported under either answer, so quoting it would mislead."""
    _load(
        vm,
        [
            _row("Has tags", matched=False),
            _row("No artist", artist="", matched=False),
            _row("", artist="", matched=False),
        ],
    )
    assert vm.notFoundCount == 3, "all three matched nothing"
    assert vm.unmatchedImportableCount == 1, "only one of them could be imported"


def test_an_unmatched_row_already_in_the_library_is_not_counted(vm):
    _load(vm, [_row("Already here", matched=False)])
    vm._results[0]["id"] = 42
    assert vm.unmatchedImportableCount == 0


def test_nothing_unmatched_means_nothing_to_ask(vm):
    _load(vm, [_row("One"), _row("Two")])
    assert vm.unmatchedImportableCount == 0


# -- what each answer queues ----------------------------------------------


def test_import_everything_queues_the_unmatched_rows_too(vm):
    uids = _load(vm, [_row("Matched"), _row("Unmatched", matched=False)])

    vm.stashAll(True)

    assert vm._stash_queue == uids


def test_matched_only_leaves_the_unmatched_rows_in_the_list(vm):
    uids = _load(vm, [_row("Matched"), _row("Unmatched", matched=False)])

    vm.stashAll(False)

    assert vm._stash_queue == [uids[0]]
    assert vm.result_count == 2, "skipping is not discarding — the rows stay"
    assert vm.unmatchedImportableCount == 1, "and can still be taken one at a time"


def test_the_no_argument_call_still_means_everything(vm):
    """QML's other callers, and every call that predates the question."""
    uids = _load(vm, [_row("Matched"), _row("Unmatched", matched=False)])

    vm.stashAll()

    assert vm._stash_queue == uids


def test_a_single_rows_own_button_never_skips_it(vm):
    """Clicking one unmatched row is already an answer to the question."""
    uids = _load(vm, [_row("Unmatched", matched=False)])

    vm.stashTrack(uids[0])

    assert vm._stash_queue == uids


def test_matched_only_skips_a_row_with_no_metadata_either(vm):
    """It was never importable; the answer does not change that."""
    uids = _load(
        vm,
        [_row("Matched"), _row("Nameless", artist="", matched=False)],
    )

    vm.stashAll(False)
    assert vm._stash_queue == [uids[0]]

    vm._stash_queue.clear()
    vm._stash_pending.clear()
    vm.stashAll(True)
    assert vm._stash_queue == [uids[0]], "still not importable under either answer"


# -- how an unmatched row looks -------------------------------------------
#
# The mark itself — an amber wash on the row and a badge over its artwork — is
# not covered here. It was checked by rendering the real window and looking at
# it, but every automated version came out order-dependent: a ListView builds
# its delegates while rendering, an offscreen window never renders unless it is
# grabbed, and a grab only produces rows in a process where no other test has
# loaded a QML engine first. A check that passes alone and fails in the suite
# teaches people to ignore failures, so what is left is the part that is both
# cheap and reliable — that the colour and the glyph the mark is built from
# exist, which is what a rename would break.


def test_the_marks_colour_and_glyph_exist(qt_app, tmp_path):
    """A binding to a colour or icon that is not there fails silently in QML."""
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine

    from app.gui.gui_paths import bundled_gui_root

    nocturne = bundled_gui_root() / "nocturne"
    source = tmp_path / "Probe.qml"
    source.write_text(
        """
        import QtQuick
        import Nocturne

        Item {
            property color warning: Theme.warning
            property string glyph: Icons.svg("warning", "#000000", 2.8)
        }
        """,
        encoding="utf-8",
    )

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(nocturne))
    engine.load(QUrl.fromLocalFile(str(source)))
    roots = engine.rootObjects()
    assert roots, "probe failed to load"

    assert roots[0].property("warning").name() == "#fbbf24"
    glyph = roots[0].property("glyph")
    assert glyph.startswith("data:image/svg+xml"), glyph[:40]
    assert "%3Cpath" in glyph or "<path" in glyph, "the warning glyph draws nothing"

    del engine
