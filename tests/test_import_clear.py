"""Showing only the import rows that still have work in them.

The import list accumulates on purpose — several folders, a CSV, files paired by
hand — and what the user is deciding on is the rows that can still be imported. A
row that is in the library is not one of those: it has nothing to press, nothing
to choose, and it dims itself to say so. Forty of them at the end of a forty-file
import are forty rows to read past to find the two that failed.

So by default the list does not *show* them. A match the library already has
arrives hidden, and a row that finishes importing goes off screen as it finishes.

It hides them; it does not throw them away. That distinction is what most of this
file is about, and there are three reasons for it:

* the setting is reversible. Turning it off over a list already gathered gives
  the rows back where they were, rather than leaving a scan and a match run's
  worth of work half destroyed by a switch in another tab;
* the count stays askable. ``inDatabaseCount`` and ``stateCounts["inLibrary"]``
  go on reporting rows that are off screen, which is the one moment that number
  is worth saying and the one moment nothing else on the page could say it;
* a folder re-imported by mistake is still recognised as a repeat, because the
  rows it would collide with are still in the list to collide with.

Two things beyond that are worth being careful about.

The first is arithmetic. Three of the totals describe the *list* — and now count
hidden rows, because a hidden row is in the list — while two describe the *run*
that just finished, which is what the completion toast quotes against
``searchTotal``.

The second is the departure. A row that finished did not go anywhere the user
sent it, so it must not be animated like one that was thrown out.
``imported_removed`` is what says so, and like ``queueVM.advance_removed`` it has
to be emitted while there is still a row to name.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.library.roles import FIELD_TO_ROLE  # noqa: E402
from app.settings.imports import (  # noqa: E402
    DEFAULT_CLEAR_IMPORTED,
    ImportSettings,
)
from app.settings.store import SettingsStore  # noqa: E402
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
    """The setting, without a session behind it."""

    def __init__(self, clear_imported: bool = True) -> None:
        self.clear_imported = clear_imported

    def set_clear_imported(self, value) -> None:
        self.clear_imported = bool(value)


def _vm(qt_app, existing=None, *, clearing=True):
    return ImportViewModel(
        import_service=_StubImportService(existing),
        track_service=None,
        registry=registry_for(audio=FakeAudio()),
        import_settings=_StubImports(clearing),
    )


@pytest.fixture
def vm(qt_app):
    """Two of the three rows below are already in the library."""
    return _vm(qt_app, {"vid-Known": 42, "vid-Older": 43})


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


def _titles(vm):
    """What is on screen — the rows the model exposes."""
    return [_read(vm, i, "title") for i in range(vm.rowCount())]


def _listed(vm):
    """What is in the list, hidden rows included.

    A different question from `_titles` now, and the difference is the whole
    point: `result_count` counts these, `rowCount` counts those.
    """
    return [result["title"] for result in vm._results]


# ======================================================================
# What a match run lists
# ======================================================================


def test_a_match_the_library_already_has_is_listed_but_not_shown(vm):
    vm._on_search_finished([_row("One"), _row("Known"), _row("Two")])

    assert _titles(vm) == ["One", "Two"]
    assert _listed(vm) == ["One", "Known", "Two"], (
        "kept, so the setting has something to give back and the count has "
        "something to count"
    )


def test_the_run_still_counts_it_as_a_match(vm):
    """It did match. `lastMatchedCount` is quoted against `searchTotal`."""
    vm._on_search_finished([_row("One"), _row("Known"), _row("Two")])

    assert vm.lastMatchedCount == 3, "the run matched three of the files it took"


def test_the_number_already_in_the_library_is_reported(vm):
    """What the completion toast quotes against `searchTotal`."""
    vm._on_search_finished([_row("One"), _row("Known"), _row("Older")])

    assert vm.lastInDatabaseCount == 2


def test_the_lists_own_totals_count_the_rows_it_is_hiding(vm):
    vm._on_search_finished([_row("One"), _row("Known"), _row("Gone", not_found=True)])

    assert vm.foundCount == 2, "both matched, whether or not both are shown"
    assert vm.notFoundCount == 1
    assert vm.inDatabaseCount == 1, "hidden, and still a row the library has"


def test_the_figure_is_about_the_run_and_not_the_list(vm):
    """A second run reports itself, not everything since the tab opened."""
    vm._on_search_finished([_row("Known"), _row("Older")])
    assert vm.lastInDatabaseCount == 2

    vm._on_search_finished([_row("One")])

    assert vm.lastInDatabaseCount == 0, "this run found nothing already there"


def test_a_run_that_is_entirely_already_imported_shows_nothing(vm):
    """And still has a list, which is the difference this setting now makes.

    The page stays up with an empty table and a rail saying every one of the two
    is already in the library. That is a report; the old behaviour — an empty
    page — was the same run looking like it had done nothing at all.
    """
    vm._on_search_finished([_row("Known"), _row("Older")])

    assert vm.rowCount() == 0
    assert vm.result_count == 2
    assert vm.hasResults is True
    assert vm.inDatabaseCount == 2
    assert vm.lastInDatabaseCount == 2


def test_a_hidden_row_is_a_row_and_gets_a_uid(vm):
    """It has to be nameable: `imported_removed` and the selection are uids."""
    vm._on_search_finished([_row("Known"), _row("One")])

    assert vm._results[0]["uid"] == 0
    assert vm.uidAt(0) == 1, "the first row on screen is the second one gathered"


def test_with_the_setting_off_it_is_listed_as_before(qt_app):
    off = _vm(qt_app, {"vid-Known": 42}, clearing=False)

    off._on_search_finished([_row("One"), _row("Known")])

    assert _titles(off) == ["One", "Known"]
    assert off.inDatabaseCount == 1
    assert _read(off, 1, "importState") == "inLibrary"
    assert off.lastInDatabaseCount == 1, "reported either way — it is true either way"


# ======================================================================
# A row that finishes
# ======================================================================


def test_a_row_that_finishes_leaves_the_screen(vm):
    vm._on_search_finished([_row("One"), _row("Two")])

    vm._on_stash_finished(vm.uidAt(0), 77)

    assert _titles(vm) == ["Two"]
    assert _listed(vm) == ["One", "Two"], "off screen, not out of the list"


def test_it_is_named_before_it_goes(vm):
    """`markAdvanceRemoved` is keyed by uid, and reads it off the row.

    Emitted after the removal there would be no row left to name, and the table
    would throw a track that simply finished off the right-hand edge. The same
    rule `queueVM.advance_removed` keeps — see tests/test_queue_removal.py.
    """
    vm._on_search_finished([_row("One"), _row("Two")])
    uid = vm.uidAt(0)
    still_there: list[int] = []
    vm.imported_removed.connect(lambda named: still_there.append(vm.uidAt(0)))

    vm._on_stash_finished(uid, 77)

    assert still_there == [uid], "announced after the row had already gone"


def test_the_uid_it_names_is_the_row_that_finished(vm):
    vm._on_search_finished([_row("One"), _row("Two")])
    named: list[int] = []
    vm.imported_removed.connect(named.append)

    vm._on_stash_finished(vm.uidAt(1), 77)

    assert named == [1]
    assert _titles(vm) == ["One"]


def test_the_totals_follow_the_rows_rather_than_the_screen(vm):
    """A row going off screen changes what it *is*, not whether it is counted."""
    vm._on_search_finished([_row("One"), _row("Two")])

    vm._on_stash_finished(vm.uidAt(0), 77)
    vm._on_stash_finished(vm.uidAt(0), 78)

    assert vm.rowCount() == 0, "both have finished and both are hidden"
    assert vm.result_count == 2
    assert (vm.foundCount, vm.notFoundCount, vm.inDatabaseCount) == (2, 0, 2)


def test_the_completion_is_still_announced(vm):
    """`stash_completed` is what refreshes the Database tab."""
    vm._on_search_finished([_row("One")])
    completions: list[tuple[int, int]] = []
    vm.stash_completed.connect(lambda uid, tid: completions.append((uid, tid)))

    vm._on_stash_finished(vm.uidAt(0), 77)

    assert completions == [(0, 77)]


def test_an_import_run_empties_the_list_down_to_what_could_not_go(vm):
    vm._on_search_finished(
        [_row("One"), _row("Two"), _row("Bare", not_found=True)]
    )
    vm._results[2]["title"] = ""
    vm._results[2]["artist"] = ""

    vm._on_stash_finished(vm.uidAt(0), 77)
    vm._on_stash_finished(vm.uidAt(0), 78)

    assert vm.rowCount() == 1
    assert _read(vm, 0, "importState") == "needsMetadata"


def test_the_run_is_not_left_looking_like_it_is_still_going(vm):
    """`isStashing` counts a queued uid, so a finished row must leave the queue.

    Through `_on_stash_done`, which is what the worker's own `done` reaches and
    is the only thing that clears a pending uid. It used to fall out of the
    removal instead — a row leaving took its queue entry with it — and a row
    that no longer leaves would have left the entry behind, with the button
    still offering to abort a run that had finished.
    """
    vm._on_search_finished([_row("One")])
    uid = vm.uidAt(0)
    # What `_start_stash` leaves behind for the row in flight: off the queue,
    # still pending, a stash active.
    vm._stash_pending = {uid}
    vm._stash_active = True
    assert vm.isStashing is True

    vm._on_stash_finished(uid, 77)
    vm._on_stash_done(uid)
    vm._on_stash_stopped()

    assert vm._stash_queue == []
    assert vm._stash_pending == set()
    assert vm.isStashing is False


def test_a_row_that_finishes_stays_when_the_setting_is_off(qt_app):
    off = _vm(qt_app, clearing=False)
    off._on_search_finished([_row("One"), _row("Two")])
    named: list[int] = []
    off.imported_removed.connect(named.append)

    off._on_stash_finished(off.uidAt(0), 77)

    assert _titles(off) == ["One", "Two"]
    assert _read(off, 0, "importState") == "inLibrary"
    assert off.inDatabaseCount == 1
    assert named == [], "nothing left, so nothing to announce"


# ======================================================================
# Turning it on and off
# ======================================================================


def test_turning_it_on_sweeps_what_is_already_listed(qt_app):
    """It has to mean what it says now, not only from the next import on."""
    vm = _vm(qt_app, clearing=False)
    vm._on_search_finished([_row("One"), _row("Two"), _row("Three")])
    vm._on_stash_finished(vm.uidAt(0), 77)
    vm._on_stash_finished(vm.uidAt(2), 78)
    named: list[int] = []
    vm.imported_removed.connect(named.append)

    vm.clearImported = True

    assert _titles(vm) == ["Two"]
    assert named == [0, 2], "both, and each named before it went"
    assert vm.inDatabaseCount == 2, "hidden, and still what the library has"
    assert vm.result_count == 3


def test_turning_it_off_again_gives_them_back_where_they_were(qt_app):
    """The reason the rows are kept at all.

    Not merely that they come back, but that they come back *in place* — the row
    that was between two others is between those two again, so a list the user
    has been reading does not rearrange itself under them.
    """
    vm = _vm(qt_app, clearing=False)
    vm._on_search_finished([_row("One"), _row("Two"), _row("Three")])
    vm._on_stash_finished(vm.uidAt(1), 77)
    vm.clearImported = True
    assert _titles(vm) == ["One", "Three"]

    vm.clearImported = False

    assert _titles(vm) == ["One", "Two", "Three"]
    assert _read(vm, 1, "importState") == "inLibrary"


def test_turning_it_off_leaves_a_list_with_nothing_hidden_alone(vm):
    vm._on_search_finished([_row("One"), _row("Two")])

    vm.clearImported = False

    assert _titles(vm) == ["One", "Two"]
    assert vm.clearImported is False


def test_a_match_the_library_has_comes_back_with_the_setting(vm):
    """Hidden on arrival, and still there to be shown."""
    vm._on_search_finished([_row("One"), _row("Known"), _row("Two")])
    assert _titles(vm) == ["One", "Two"]

    vm.clearImported = False

    assert _titles(vm) == ["One", "Known", "Two"]


def test_the_change_is_announced(vm):
    heard: list[bool] = []
    vm.clear_imported_changed.connect(lambda: heard.append(vm.clearImported))

    vm.clearImported = False
    vm.clearImported = True

    assert heard == [False, True]


def test_setting_it_to_what_it_already_is_says_nothing(vm):
    heard: list[bool] = []
    vm.clear_imported_changed.connect(lambda: heard.append(vm.clearImported))

    vm.clearImported = True

    assert heard == []


def test_turning_it_on_over_an_empty_list_is_a_no_op(qt_app):
    vm = _vm(qt_app, clearing=False)
    counts: list[int] = []
    vm.result_count_changed.connect(lambda: counts.append(vm.result_count))

    vm.clearImported = True

    assert counts == []


# ======================================================================
# What a hidden row is still good for
# ======================================================================
#
# Three things, and each is a reason the rows are kept rather than dropped.


def test_the_chip_can_still_count_them(vm):
    """`stateCounts` is what the legend draws, and it counts the whole list.

    The one number on the page that has to survive the rows being hidden: it is
    the only thing left saying the import found anything at all in the run where
    the library already had all of it.
    """
    vm._on_search_finished([_row("One"), _row("Known"), _row("Older")])

    assert vm.stateCounts["inLibrary"] == 2
    assert vm.stateCounts["stash"] == 1
    assert vm.inDatabaseCount == 2


def test_a_hidden_row_is_still_something_a_folder_can_repeat(vm):
    """Dedupe reads the list, so a re-import collides with what is off screen.

    Dropping the rows would have made a second pass at the same folder look like
    a fresh one — and, with the setting later turned off, put the same track in
    the list twice.
    """
    vm._on_search_finished([_row("One"), _row("Known")])

    vm._on_search_finished([_row("Known"), _row("Two")])

    assert _listed(vm) == ["One", "Known", "Two"]
    assert vm.duplicatesSkipped == 1


def test_a_hidden_row_cannot_be_thrown_off_a_list_it_is_not_on(vm):
    """The `in library` chip selects rows that may be off screen.

    "Remove from list" acts on a selection, and it is aimed at the list the user
    can see. A row they cannot see is not theirs to lose to that gesture.
    """
    vm._on_search_finished([_row("One"), _row("Known")])
    vm.setSelectedMultiple(vm.uidsInState("inLibrary"))

    vm.removeSelected()

    assert _listed(vm) == ["One", "Known"]
    assert vm.removableSelectedCount == 0


# ======================================================================
# Pairing a file by hand
# ======================================================================


def _web_result_legacy(source_id="vid-Known"):
    return {
        "title": "Known",
        "artist": "A",
        "album": "",
        "duration": 100,
        "release_date": None,
        "source_id": source_id,
        "cover_url": "",
        "cover_master_url": "",
    }


def _web_result(*args, **kwargs):
    return from_legacy(_web_result_legacy(*args, **kwargs))


def test_pairing_a_file_with_a_track_already_in_the_library_says_so(vm, tmp_path):
    """The most deliberate act on the page must not look like it did nothing."""
    audio = tmp_path / "track03.mp3"
    audio.write_bytes(b"")
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)

    vm.importFileAs(str(audio), _web_result())

    assert vm.result_count == 0
    assert errors and "already in your library" in errors[0]


def test_pairing_a_file_with_anything_else_still_works(vm, tmp_path):
    audio = tmp_path / "track03.mp3"
    audio.write_bytes(b"")

    vm.importFileAs(str(audio), _web_result("vid-Fresh"))

    assert _titles(vm) == ["Known"]


def test_with_the_setting_off_the_paired_row_is_listed(qt_app, tmp_path):
    off = _vm(qt_app, {"vid-Known": 42}, clearing=False)
    audio = tmp_path / "track03.mp3"
    audio.write_bytes(b"")

    off.importFileAs(str(audio), _web_result())

    assert _titles(off) == ["Known"]
    assert _read(off, 0, "importState") == "inLibrary"


# ======================================================================
# The setting itself
# ======================================================================


def test_it_is_on_out_of_the_box():
    assert DEFAULT_CLEAR_IMPORTED is True
    assert ImportSettings(None).clear_imported is True


def test_a_store_less_setting_takes_a_write_without_complaining():
    """A script or a test without a session must not have to guard for one."""
    settings = ImportSettings(None)
    settings.set_clear_imported(False)
    assert settings.clear_imported is True, "there was nowhere to write it"


def test_the_choice_survives_a_restart():
    """Written by `set_clear_imported`, which flushes, and read off disk again."""
    ImportSettings(SettingsStore({}, {})).set_clear_imported(False)

    assert ImportSettings(SettingsStore(None)).clear_imported is False


def test_a_hand_edited_value_falls_back_to_the_default():
    store = SettingsStore({}, {"app": {"clear_imported_rows": "yes please"}})

    assert ImportSettings(store).clear_imported is True


# ======================================================================
# On the real GUI
#
# QML will not complain about either of the things below. A misspelt property
# evaluates to `undefined` rather than raising, and a signal handler under the
# wrong name is simply never called — `test_gui_packages` loads this same window
# and passes in both cases. So the window is loaded and asked.
# ======================================================================


@pytest.fixture(scope="module")
def gui(qt_app, tmp_path_factory):
    """The real window over a throwaway vault: the model, and the objects."""
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine

    from app.artwork.cover_provider import CoverImageProvider
    from app.artwork.local_art_provider import LocalArtImageProvider
    from app.gui.gui_paths import bundled_gui_root
    from app.main import CONTEXT_PROPERTIES, _register_qml_types
    from app.utils.library_paths import set_library_root

    vault = tmp_path_factory.mktemp("vault") / "venomvault"
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

    yield roots[0], registry.get_service(CONTEXT_PROPERTIES["importVM"]), qt_app

    del engine


def test_the_settings_switch_reaches_the_backend(gui):
    from PySide6.QtCore import QObject

    window, model, qt_app = gui
    toggle = window.findChild(QObject, "clearImportedToggle")
    assert toggle is not None, "the import switch is gone or renamed"

    def knob():
        qt_app.processEvents()
        return toggle.property("checked")

    assert knob() is True, "on out of the box"

    # The switch never moves its own knob: it asks, and the knob follows the
    # backend. So drive the backend and watch the binding, then raise the signal
    # a press really raises and watch the backend.
    model.clearImported = False
    assert knob() is False

    toggle.toggled.emit(True)
    qt_app.processEvents()
    assert model.clearImported is True
    assert knob() is True


def test_a_leaving_row_is_marked_before_the_table_sees_it_go(gui):
    """`imported_removed` has to land on `markAdvanceRemoved`, or it fades wrong.

    The uid is what the `remove` transition asks about, so a handler under the
    wrong name means the row that finished is thrown off the right-hand edge
    like one the user removed by hand. Nothing errors; it just reads wrong.
    """
    from PySide6.QtCore import QObject

    window, model, qt_app = gui
    table = window.findChild(QObject, "importResultsTable")
    assert table is not None, "the import list is gone or renamed"

    def marked():
        return table.property("_advanceUids").property("size").toNumber()

    before = marked()
    model.imported_removed.emit(4242)
    qt_app.processEvents()

    assert marked() == before + 1, "the table never heard about the row"


def test_the_chips_appear_when_the_results_do(gui):
    """The legend is built while the rail that holds it is hidden.

    Which is every import: the rail is not shown until a run has results, so at
    the moment the chips are created there are none, and the legend measures
    zero. Sizing itself by *measuring* — reading its own positioner's height and
    hiding itself when that read 0 — latched there for good, because a
    positioner that is not effectively visible does not lay out, so the height
    it was waiting on could never arrive. It counts instead.

    Nothing about this shows up short of a loaded window. The counts binding is
    correct either way; it is the geometry that never moves.

    So the assertion is on the height, and on the height *while the page is not
    the one on screen* — which is the state the latch needed and the state this
    window is in. A legend that is the right size when nobody is looking is a
    legend that measured nothing to get there.
    """
    from PySide6.QtCore import QObject

    window, model, qt_app = gui
    legend = window.findChild(QObject, "importLegend")
    assert legend is not None, "the legend is gone or renamed"

    model.clearResults()
    qt_app.processEvents()
    assert legend.property("shownCount") == 0, "nothing to count yet"
    assert legend.property("height") == 0

    model._on_search_finished([_row("One"), _row("Two"), _row("Gone", not_found=True)])
    qt_app.processEvents()

    assert legend.property("shownCount") == 2, "two states are represented"
    assert legend.property("height") > 0, "the chips never took up any room"

    model.clearResults()
    qt_app.processEvents()


def test_the_list_says_it_is_waiting_rather_than_empty(gui):
    """`loading` is what puts the placeholder rows up, and it is a binding.

    Asked of the table rather than of the skeleton inside it: six TrackTables
    live in this window and each has one, so a `findChild` for the skeleton
    answers with whichever was built first — which is not this page's.

    The two halves both matter. It comes on while a scan or match is running with
    nothing found yet, which is the blank minute the placeholders exist for; and
    it goes off the moment the list has anything, including a list whose every
    row is hidden for being in the library. That last case is why the binding
    reads `result_count` and not the table's own `count`, which cannot tell
    "found nothing" from "found only things you already had".
    """
    from PySide6.QtCore import QObject

    window, model, qt_app = gui
    table = window.findChild(QObject, "importResultsTable")
    assert table is not None, "the import list is gone or renamed"

    def loading():
        qt_app.processEvents()
        return table.property("loading")

    model.clearResults()
    model._set_is_searching(True)
    assert loading() is True, "a scan with nothing found yet"

    model._on_search_finished([_row("One")])
    model._set_is_searching(False)
    assert loading() is False, "rows landed"

    model.clearResults()
    qt_app.processEvents()


def test_the_way_out_is_named_for_what_it_does(gui):
    """"Clear", because that is what pressing it does to the list.

    It used to say "Done", which reads as an acknowledgement — and the one thing
    it is not is an acknowledgement: the list is thrown away, and with it this
    page's slot in the rail. While a scan is running the same control is Cancel,
    which stops the run and leaves you here to pick another source.
    """
    from PySide6.QtCore import QObject

    window, model, qt_app = gui
    button = window.findChild(QObject, "importClearButton")
    assert button is not None, "the way out is gone or renamed"

    def label():
        qt_app.processEvents()
        return button.property("text")

    assert label() == "Clear"

    model._set_is_analyzing(True)
    try:
        assert label() == "Cancel"
    finally:
        model._set_is_analyzing(False)

    assert label() == "Clear"
