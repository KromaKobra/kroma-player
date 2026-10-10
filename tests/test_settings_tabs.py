"""The Settings page's seven tabs, and the two things QML will not tell you about.

Settings was one column of ten sections. It is seven tabs now, one file each under
``gui/nocturne/views/settings/``, with the content gliding sideways as you move
between them. Splitting a page into tabs invites two mistakes, and neither of
them is an error in QML — both leave a window that loads clean and behaves
wrong, which is why they are asked about here rather than left to
``test_gui_packages``.

**The first is putting the tabs behind a Loader.** It is the obvious way to make
a tab lazy, and it silently costs three things: a tab's scroll position, the
``Connections`` declared inside it — ``BackingTab`` listens for
``backingVM.error_occurred`` and has to keep its device pill in step with the
workbench's identical one — and the import switch's reachability, which
``test_import_clear`` depends on. Laziness is bought elsewhere: each tab hangs
its backend refresh off its own ``active``, so no tab probes anything until it
is looked at.

**The second is a control that owns its own index.** ``Pill`` assigns to its own
``currentIndex`` when clicked, which destroys whatever binding the caller put
there — that is the whole reason ``BackingTab`` and ``AppearanceTab`` carry
``syncPills``/``syncThemePill`` functions to push state back into their pills by
hand. ``SettingsTabBar`` is written the way ``NavRail`` documents instead: it
reports the click and draws whatever it is handed. If someone later adds
``root.currentIndex = index`` to its ``onClicked``, nothing errors — the bar
goes on looking right when clicked, and the restored-preference path quietly
stops working. So the click is raised here without the click, and the index has
to come back around through the view to arrive.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TAB_OBJECT_NAMES = (
    "appearanceTab",
    "playbackTab",
    "backingTab",
    "libraryTab",
    "pluginsTab",
    "systemTab",
    "aboutTab",
)


@pytest.fixture(scope="module")
def gui(qt_app, tmp_path_factory):
    """The real window over a throwaway vault."""
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

    yield roots[0], qt_app

    del engine


def _tab_bar(window):
    from PySide6.QtCore import QObject

    bar = window.findChild(QObject, "settingsTabBar")
    assert bar is not None, "the settings tab bar is gone or renamed"
    return bar


def _go(window, qt_app, index):
    """Change tabs the way a click does.

    Not ``setProperty("currentIndex", …)``: that writes through the metaobject
    without disturbing the QML binding, so the bar shows the new number for a
    moment and nothing downstream ever hears about it. The click is a signal,
    and the signal is what the view is listening to.
    """
    _tab_bar(window).selected.emit(index)
    _settle(qt_app)


def _settle(qt_app, timeout_ms=2000):
    """Pump until the glide has finished.

    ``x`` is under a Behavior, so reading it straight after the switch catches
    a page mid-flight — somewhere between where it was and where it is going,
    which is every value the assertions care about and a few they do not.
    """
    import time

    from PySide6.QtCore import QObject

    started = time.monotonic()
    deadline = started + timeout_ms / 1000.0
    # Longer than the glide itself, so "nothing moved between two polls" cannot
    # be answered before anything has had the chance to start moving.
    floor = started + 0.4

    previous = None
    while time.monotonic() < deadline:
        qt_app.processEvents()
        windows = qt_app.topLevelWindows()
        if not windows:
            return
        current = tuple(
            (windows[0].findChild(QObject, name) or QObject()).property("x")
            for name in TAB_OBJECT_NAMES
        )
        if current == previous and time.monotonic() > floor:
            return
        previous = current
        time.sleep(0.02)


def test_every_tab_is_built_whether_or_not_it_is_the_one_showing(gui):
    """No Loaders. A tab that does not exist cannot hear a signal or glide in."""
    from PySide6.QtCore import QObject

    window, _ = gui

    for name in TAB_OBJECT_NAMES:
        assert window.findChild(QObject, name) is not None, (
            f"{name} was not built — a Loader would also cost this tab its "
            "scroll position and any Connections declared inside it"
        )


def test_exactly_one_tab_reports_itself_current(gui):
    """`index === currentIndex` decides everything: which page is up, which way
    the others wait, and which one takes the wheel. A comparison against the
    wrong property is not an error — it just leaves the page blank."""
    from PySide6.QtCore import QObject

    window, qt_app = gui

    for wanted in range(len(TAB_OBJECT_NAMES)):
        _go(window, qt_app, wanted)

        current = [
            name
            for name in TAB_OBJECT_NAMES
            if window.findChild(QObject, name).property("current")
        ]
        assert current == [TAB_OBJECT_NAMES[wanted]], (
            f"with tab {wanted} selected, {current} reported themselves current"
        )


def test_the_waiting_tabs_sit_on_the_side_they_live_on(gui):
    """The spatial cue. A tab left of the one showing waits off the left edge and
    one to its right waits off the right, so moving right always brings the new
    page in from the right — without anything having to remember which way the
    last move went, and still correct for a tab restored at startup."""
    from PySide6.QtCore import QObject

    window, qt_app = gui

    _go(window, qt_app, 3)

    resting = [
        window.findChild(QObject, name).property("x") for name in TAB_OBJECT_NAMES
    ]

    assert all(x < 0 for x in resting[:3]), (
        f"tabs left of the current one should wait off the left edge: {resting}"
    )
    assert resting[3] == 0, f"the current tab should be at rest: {resting}"
    assert all(x > 0 for x in resting[4:]), (
        f"tabs right of the current one should wait off the right edge: {resting}"
    )


def test_the_tab_bar_does_not_own_its_own_index(gui):
    """NavRail's rule, not Pill's: report the click, draw what you are handed.

    Raised as the signal rather than as a click, so the only thing that can move
    the bar is the view pushing the new index back down. A bar that set its own
    index would pass a click test and fail this one.
    """
    window, qt_app = gui
    bar = _tab_bar(window)

    _go(window, qt_app, 0)
    assert bar.property("currentIndex") == 0

    bar.selected.emit(4)
    _settle(qt_app)

    assert bar.property("currentIndex") == 4, (
        "the view never pushed the new index back down — either the handler is "
        "under the wrong name, or the bar is writing its own index and the "
        "view's binding has been destroyed"
    )
