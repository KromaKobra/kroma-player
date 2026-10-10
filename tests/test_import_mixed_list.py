"""One import list, holding rows of every kind.

Import a folder, then a CSV, and the folder's rows used to vanish — silently,
along with whatever had been selected in them. The list was allowed to be all
stashed or all downloaded and nothing in between, not because the data could not
hold both (every row has carried its own ``is_download`` since the day imports
started accumulating) but because the *header* could not: one button, one verb,
one sentence describing the whole list.

So the header stopped speaking for the list from the run, and started asking the
rows. The two pending counts are what it asks with, and this is the surface that
proves it, driven through the real loaded GUI rather than the view model alone:

* a misspelt property is not an error in QML. It evaluates to ``undefined``, and
  the button would simply keep whichever verb it happened to start with;
* the status cell decides each row's icon and whether its button works, and it
  does so *without* asking the backend, so the two can drift apart with nothing
  to notice — see ``test_the_status_cells_rule_mirrors_the_backends`` in
  ``test_import_append.py`` for the other half of that mirror.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import (
    QMetaObject,
    QObject,
    Q_RETURN_ARG,
    Qt,
    QUrl,
)
from PySide6.QtQml import QQmlApplicationEngine

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.gui.gui_paths import bundled_gui_root  # noqa: E402
from app.main import CONTEXT_PROPERTIES, _register_qml_types  # noqa: E402
from tests.fakes.providers import (  # noqa: E402
    FakeAudio,
    FakeIdentity,
    FakeRegistry,
    from_legacy,
    use_registry,
)


@pytest.fixture(scope="module")
def gui(qt_app, tmp_path_factory):
    """The whole interface, loaded offscreen over a throwaway vault."""
    from app.artwork.cover_provider import CoverImageProvider
    from app.artwork.local_art_provider import LocalArtImageProvider
    from app.bootstrap import create_service_registry
    from app.utils.library_paths import set_library_root

    vault = tmp_path_factory.mktemp("mixedvault") / "venomvault"
    (vault / ".data").mkdir(parents=True)
    set_library_root(vault)

    registry = create_service_registry()
    _register_qml_types(registry)
    # A plugin that can fetch and identify, as if one were installed and on:
    # without it every fetched row here would be `unavailable`, which is right
    # for a plain install and not what this list is about.
    use_registry(
        registry.get_service(CONTEXT_PROPERTIES["importVM"]),
        FakeRegistry(audio=FakeAudio(), identity=FakeIdentity()),
    )

    directory = bundled_gui_root() / "nocturne"
    engine = QQmlApplicationEngine()
    engine.addImageProvider(
        "cover", CoverImageProvider(registry.get_service("cover_store"))
    )
    engine.addImageProvider("localart", LocalArtImageProvider())

    context = engine.rootContext()
    for qml_name, service in CONTEXT_PROPERTIES.items():
        context.setContextProperty(qml_name, registry.get_service(service))

    warnings: list = []
    engine.warnings.connect(lambda w: warnings.extend(w))
    engine.addImportPath(str(directory))
    engine.load(QUrl.fromLocalFile(str(directory / "main.qml")))

    roots = engine.rootObjects()
    assert roots, "the GUI failed to load:\n" + "\n".join(
        w.toString() for w in warnings
    )

    # Shown, and standing on the Import page. Both are load-bearing: a ListView
    # that is never visible never builds a delegate, and a delegate that is
    # never built is a `required property` that is never resolved — which is
    # exactly the failure the new role could introduce, and it would sail
    # through a test that only loaded the window.
    roots[0].setProperty("visible", True)
    roots[0].setProperty("viewIndex", 8)
    qt_app.processEvents()

    yield roots[0], registry.get_service(CONTEXT_PROPERTIES["importVM"]), \
        qt_app, warnings

    del engine


@pytest.fixture
def vm(gui):
    """The real import view model, emptied between tests."""
    _, model, qt_app, _ = gui
    yield model
    model.clearResults()
    # `_all_six` turns this off to keep an `inLibrary` row on the list. The
    # model is the registry's, shared by every test in the module, so put it
    # back — see tests/test_import_clear.py for the setting's own tests.
    model.clearImported = True
    qt_app.processEvents()


def _row_legacy(title, source_id, source_path, not_found=False):
    return {
        "title": title,
        "artist": "A",
        "album": "",
        "duration": 100,
        "source_id": "" if not_found else source_id,
        "cover_url": "",
        "cover_master_url": "",
        "source_path": source_path,
        "release_date": None,
        "not_found": not_found,
    }


def _row(*args, **kwargs):
    """`_row_legacy`, as a core row — see `tests.fakes.providers.from_legacy`."""
    return from_legacy(_row_legacy(*args, **kwargs))


def _add_local(vm, *rows):
    vm._set_download_mode(False)
    vm._on_search_finished(list(rows))


def _add_fetched(vm, *rows):
    vm._set_download_mode(True)
    vm._on_search_finished(list(rows))


def _walk(obj):
    yield obj
    for child in obj.children():
        yield from _walk(child)


def _import_header(window):
    """The Import page's header — the one carrying the subtitle under test."""
    for obj in _walk(window):
        if obj.metaObject().className().startswith("ViewHeader") \
                and str(obj.property("title")) == "Import":
            return obj
    raise AssertionError("the Import header is gone or renamed")


def _legend(window):
    for obj in _walk(window):
        if obj.metaObject().className().startswith("ImportLegend"):
            return obj
    raise AssertionError("the import legend is gone or renamed")


def _all_six(vm):
    """One row in every state, built the way the flows really build them.

    `inLibrary` is a state the list only holds when it is set to keep finished
    rows, so that is turned off here. The default takes them off as they land —
    which is the same classification doing its job, tested where it belongs.
    """
    vm.clearImported = False
    _add_local(
        vm,
        _row("Matched opus", "vidopus0001", "/music/a.opus"),
        _row("Matched flac", "vidflac0001", "/music/b.flac"),
        _row("Tagged", "", "/music/c.mp3", not_found=True),
    )
    _add_local(vm, _row("Bare", "", "/music/d.wav", not_found=True))
    vm._results[3]["title"] = ""
    vm._results[3]["artist"] = ""
    _add_fetched(
        vm,
        _row("Fetch me", "vidfetch001", ""),
        _row("Gone", "", "", not_found=True),
    )
    # The first row lands, which is the only way into `inLibrary`.
    vm._on_stash_finished(vm.uidAt(0), 42)


def _pump(qt_app, timeout: float = 1.0):
    """Let bindings settle. The header's text is behind more than one hop."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)


# ======================================================================
# The list itself
# ======================================================================


def test_a_folder_and_a_csv_gather_into_one_list(gui, vm):
    """The whole feature, in the order a user would produce it."""
    _, _, qt_app, _ = gui

    _add_local(vm, _row("Local One", "vidloc00001", "/music/one.opus"),
                   _row("Local Two", "vidloc00002", "/music/two.opus"))
    _add_fetched(vm, _row("From CSV", "vidcsv00001", ""))
    qt_app.processEvents()

    assert vm.result_count == 3
    assert [r["is_download"] for r in vm._results] == [False, False, True]
    assert (vm.pendingDownloadCount, vm.pendingStashCount) == (1, 2)


# ======================================================================
# What the header says about it
# ======================================================================


def _label(gui, window):
    _, _, qt_app, _ = gui
    _pump(qt_app, 0.15)
    button = window.findChild(QObject, "importActionButton")
    assert button is not None, "the import action button is gone or renamed"
    return str(button.property("text"))


def test_a_mixed_list_is_an_import(gui, vm):
    """"Download all" over a folder of local files would be a lie."""
    window, _, _, _ = gui
    _add_local(vm, _row("Local", "vidloc00001", "/music/one.opus"))
    _add_fetched(vm, _row("Fetched", "vidcsv00001", ""))

    assert _label(gui, window) == "Import all"


def test_a_list_of_nothing_but_downloads_still_says_download(gui, vm):
    """The wording a CSV run has always had, kept for the case it describes."""
    window, _, _, _ = gui
    _add_fetched(vm, _row("Fetched", "vidcsv00001", ""))

    assert _label(gui, window) == "Download all"


def test_local_rows_alone_are_an_import(gui, vm):
    window, _, _, _ = gui
    _add_local(vm, _row("Local", "vidloc00001", "/music/one.opus"))

    assert _label(gui, window) == "Import all"


def test_a_fetched_row_with_nothing_to_fetch_does_not_claim_the_verb(gui, vm):
    """It is not pending — no id — so the list is local as far as the button goes."""
    window, _, _, _ = gui
    _add_local(vm, _row("Local", "vidloc00001", "/music/one.opus"))
    _add_fetched(vm, _row("Gone", "", "", not_found=True))

    assert _label(gui, window) == "Import all"


def test_the_subtitle_names_a_list_of_more_than_one_kind(gui, vm):
    """Once nothing is running the list answers, and a gathered one says so."""
    window, _, qt_app, _ = gui
    header = _import_header(window)

    _add_local(vm, _row("Local", "vidloc00001", "/music/one.opus"))
    _pump(qt_app, 0.15)
    assert str(header.property("subtitle")) == "From your own files"

    _add_fetched(vm, _row("Fetched", "vidcsv00001", ""))
    _pump(qt_app, 0.15)
    assert str(header.property("subtitle")) == "Your own files and downloads"


# ======================================================================
# Nothing broke on the way
# ======================================================================


def test_the_row_delegate_resolves_the_new_role(gui, vm):
    """`isDownload` is a *required* property of the row, so it has to be a role.

    The delegate `TrackTable` builds is shared by the queue, the library,
    playlists and search results, and Qt refuses to build one whose required
    properties the model has no role for. Adding a field to the catalog adds the
    role to every list at once — but only a delegate that is actually
    constructed proves it, and one is only constructed for a list that is on
    screen with rows in it.
    """
    window, _, qt_app, warnings = gui
    before = len(warnings)

    _add_local(vm, _row("Local", "vidloc00001", "/music/one.opus"))
    _add_fetched(vm, _row("Fetched", "vidcsv00001", ""))
    _pump(qt_app, 0.4)

    unresolved = [
        w.toString() for w in warnings[before:]
        if "Required property" in w.toString()
    ]
    assert not unresolved, "\n".join(unresolved)


# ======================================================================
# Telling the six apart
# ======================================================================


def test_a_list_can_hold_every_state_at_once(gui, vm):
    """The list this whole marking system exists for."""
    _, _, qt_app, _ = gui
    _all_six(vm)
    _pump(qt_app, 0.2)

    from app.viewmodels.import_view_model import ALL_STATES

    assert dict(vm.stateCounts) == {state: 1 for state in ALL_STATES}


def test_the_legend_counts_what_the_rows_are(gui, vm):
    """The bar and the rows read one source, so they cannot disagree."""
    window, _, qt_app, _ = gui
    _all_six(vm)
    _pump(qt_app, 0.2)

    counts = _legend(window).property("counts")
    counts = counts.toVariant() if hasattr(counts, "toVariant") else counts

    assert dict(counts) == dict(vm.stateCounts)


def test_a_legend_chip_selects_its_rows_in_the_list(gui, vm):
    """The whole of "manage them": pick out a state, then Remove from list.

    Goes through the real table rather than the model alone, because the table
    keeps its own copy of the selection — selection outlives the delegates that
    draw it — and a backend-driven change reaches that copy only through
    `selectionChanged`.
    """
    window, _, qt_app, _ = gui
    _all_six(vm)
    _pump(qt_app, 0.2)

    wanted = vm.uidsInState("needsMetadata")
    assert len(wanted) == 1
    expected_rows = [i for i in range(vm.rowCount()) if vm.uidAt(i) in wanted]

    vm.setSelectedMultiple(wanted)
    _pump(qt_app, 0.2)

    assert set(vm.selectedUids()) == set(wanted)

    # Asked of the table, not of the model. `TrackTable.selectedRows` walks its
    # *own* copy of the selection for a model with no `selectedRows` of its own
    # — which this one is — so the answer is only right if `selectionChanged`
    # reached it and `_resyncSelection` re-read `selectedUids`. Drop either and
    # this comes back empty while the model still says the rows are picked.
    table = next(o for o in _walk(window)
                 if o.metaObject().className().startswith("TrackTable")
                 and o.property("statusMode") == "import")
    rows = QMetaObject.invokeMethod(
        table, "selectedRows", Qt.DirectConnection, Q_RETURN_ARG("QVariant")
    )
    assert rows.toVariant() == expected_rows, "the table never heard about it"


def test_the_gui_reported_no_binding_errors(gui):
    """Bindings fail silently at runtime; this is the only place they surface.

    Runs last on purpose: by now every test above has put rows of both kinds on
    the Import page with the window shown, so the delegates and the status cells
    inside them have been built and their bindings evaluated.
    """
    _, _, _, warnings = gui

    fatal = [
        w.toString() for w in warnings
        if any(s in w.toString() for s in (
            "is not a function", "undefined", "Unable to assign",
            "Cannot read property", "is not available", "TypeError",
            "ReferenceError", "Required property",
        ))
    ]
    assert not fatal, "\n".join(fatal)
