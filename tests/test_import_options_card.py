"""The options card, loaded for real, over a scan the backend actually made.

`test_import_options` pins the arithmetic. This pins the wiring, which is the
part QML will not complain about: a misspelt property is not an error, it is
`undefined`, and a card bound to `preview.selcted` draws a blank where a number
should be and loads perfectly clean.

Two things are worth the cost of a window.

**The list is the control.** Every container the scan found has to reach the
card as its own row — a format the card cannot draw is a format the user cannot
turn off, and the only sign of it is a shorter list than the folder.

**The figures move, and they move together.** The card recomputes by asking the
backend rather than by adding anything up, so unticking a row has to change the
total under the list. A card that had kept its own copy would still look right
on the way in and stop being right on the first click.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="module")
def gui(qt_app, tmp_path_factory):
    """The real window over a throwaway vault. `test_settings_tabs`' fixture."""
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

    yield roots[0], qt_app, registry

    del engine


def _settle(qt_app, ms=400):
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.01)


@pytest.fixture
def card(gui):
    """The card, opened over a scan of three containers.

    The analysis is pushed in rather than scanned off a disk: what is under test
    is the card, and a temporary folder of real audio would make this a test of
    `probe_media_file` that happens to open a window.
    """
    from PySide6.QtCore import QObject

    from app.importer.media_probe import ProbedFile
    from app.services.import_service import ImportAnalysis

    window, qt_app, registry = gui
    vm = registry.get_service("import_view_model")

    def probed(name, tagged=True):
        return ProbedFile(
            path=Path(f"/music/{name}"),
            title="Title" if tagged else "",
            artist="Artist" if tagged else "",
        )

    vm._analysis = ImportAnalysis(
        files=[
            probed("a.opus"), probed("b.opus"),
            probed("c.mp3"), probed("d.mp3"), probed("e.mp3", tagged=False),
            probed("f.flac"),
        ]
    )
    vm.analysis_ready.emit()
    _settle(qt_app)

    dialog = window.findChild(QObject, "importOptionsDialog")
    assert dialog is not None, "the options card is gone or renamed"
    assert dialog.property("visible"), (
        "analysis_ready did not open the card — ImportView's handler is under "
        "the wrong name, or it is still opening one of the two menus"
    )

    yield dialog, window, qt_app, vm

    dialog.setProperty("_committed", True)  # do not cancel the fixture's scan
    dialog.close()
    _settle(qt_app)
    vm._analysis = None


def _rows(window):
    """The format rows, reached through the Repeater that made them.

    Not ``findChild``: a Repeater's delegates are visual children of the item
    the Repeater sits in and are not in the QObject tree beneath it, so a search
    from the window finds every other control on the card and none of these.
    """
    from PySide6.QtCore import QMetaObject, QObject, Qt, Q_ARG, Q_RETURN_ARG
    from PySide6.QtQuick import QQuickItem

    repeater = window.findChild(QObject, "importFormatRepeater")
    assert repeater is not None, "the format list is gone or renamed"

    found = {}
    for i in range(repeater.property("count")):
        item = QMetaObject.invokeMethod(
            repeater, "itemAt", Qt.DirectConnection,
            Q_RETURN_ARG(QQuickItem), Q_ARG(int, i),
        )
        found[item.objectName().removeprefix("importFormat_")] = item
    return found


def _row(window, ext):
    return _rows(window).get(ext)


def test_every_container_the_scan_found_gets_a_row(card):
    """A format with no row is a format that cannot be turned off, and nothing
    on screen says it is missing."""
    dialog, window, _, _ = card

    for ext in ("opus", "mp3", "flac"):
        assert _row(window, ext) is not None, f"{ext} did not reach the card"


def test_a_row_names_its_container_and_how_many_of_it_there_are(card):
    _, window, _, _ = card

    assert _row(window, "mp3").property("label") == "mp3"
    assert _row(window, "mp3").property("count") == "3"


def test_a_row_says_what_it_will_cost(card):
    """The one place a file type is told it means a transcode before the user
    commits to it."""
    _, window, _, _ = card

    assert _row(window, "opus").property("detail") == "filed as-is"
    assert _row(window, "mp3").property("detail") == "transcoded to Opus"


def test_the_total_under_the_list_is_the_whole_scan_to_start_with(card):
    from PySide6.QtCore import QObject

    _, window, _, _ = card
    total = window.findChild(QObject, "importSelectionCount")

    assert "6 of 6 files" in total.property("text")


def test_unticking_a_container_moves_the_total(card):
    """The click, not the property: `checked` is bound to the preview, so
    writing it would change the knob and tell nothing else. What the card
    listens to is the row saying it was clicked."""
    from PySide6.QtCore import QObject

    _, window, qt_app, _ = card
    total = window.findChild(QObject, "importSelectionCount")

    _row(window, "mp3").toggled.emit(False)
    _settle(qt_app)

    assert "3 of 6 files" in total.property("text")
    assert _row(window, "mp3").property("checked") is False, (
        "the row drew its own tick instead of the one the card handed back — "
        "the count and the box can now disagree"
    )


def test_the_fallback_switch_moves_it_too(card):
    from PySide6.QtCore import QObject

    _, window, qt_app, _ = card
    total = window.findChild(QObject, "importSelectionCount")

    window.findChild(QObject, "importFallbackToggle").toggled.emit(False)
    _settle(qt_app)

    assert "5 of 6 files" in total.property("text"), "one file has no usable tags"


def test_turning_everything_off_stops_the_button_rather_than_the_run(card):
    """Otherwise the card starts a run whose only possible outcome is a toast
    reading "No files match the selected import options"."""
    from PySide6.QtCore import QObject

    _, window, qt_app, _ = card
    confirm = window.findChild(QObject, "importOptionsConfirm")

    for ext in ("opus", "mp3", "flac"):
        _row(window, ext).toggled.emit(False)
    _settle(qt_app)

    assert confirm.property("enabledAction") is False


def test_cancelling_throws_the_scan_away(card):
    """The scan is finished and nothing else on the page can reach it, so a card
    dismissed without this strands it — and the next import reports that one is
    already running."""
    from PySide6.QtCore import QObject

    _, window, qt_app, vm = card

    window.findChild(QObject, "importOptionsCancel").clicked.emit()
    _settle(qt_app)

    assert vm._analysis is None


def test_a_second_scan_is_not_drawn_with_the_first_ones_figures(card):
    """`openFor` sets every stateful field. A card that set some of them would
    come up wearing whichever of the last folder's numbers it forgot."""
    from PySide6.QtCore import QObject

    from app.importer.media_probe import ProbedFile
    from app.services.import_service import ImportAnalysis

    dialog, window, qt_app, vm = card

    _row(window, "mp3").toggled.emit(False)
    _settle(qt_app)

    dialog.setProperty("_committed", True)
    dialog.close()
    _settle(qt_app)

    vm._analysis = ImportAnalysis(
        files=[ProbedFile(path=Path("/music/only.opus"), title="T", artist="A")]
    )
    vm.analysis_ready.emit()
    _settle(qt_app)

    assert _row(window, "mp3") is None, "the first scan's mp3 row outlived it"
    assert "1 of 1 file" in window.findChild(
        QObject, "importSelectionCount"
    ).property("text")
