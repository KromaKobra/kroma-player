"""Scrolling the navigation rail and the settings tab strip.

Hovering either bar and turning the wheel moves the selection one entry, the way
it does over a browser's tab strip. The mechanism is ``Nocturne/WheelSteps.qml``:
a wheel event is continuous and a tab change is not, so deltas accumulate and
what gets tested is the accumulator, not the delta.

Three things about that are worth a test, and none of them is an error in QML.

**The wheel has to reach the bar at all.** Both rails sit under a window-wide
backdrop and a window-wide deselect ``MouseArea``, and every slot carries a
``MouseArea`` of its own on top. Nothing about that arrangement raises an error
if the wheel is swallowed on the way through — the bar simply does not answer.
So the tests here post real ``QWheelEvent``s at the bar's actual window
coordinates rather than calling into the handler.

**The rail steps by slot, not by view index.** The two coincide only while no
transient tab (Edit, Import, Backing) is up, and the transient tabs are exactly
when a rail scroll is most likely to be used. ``+1`` on the view index would
walk off Settings into a page the rail is not drawing; ``+1`` on the slot lands
on the thing you can see below it. The isolated fixture below exists to force
that case, which the real window will not produce without a dirty editor.

**A flick must not empty the rail.** One two-finger swipe delivers hundreds of
pixels across dozens of events. Handed straight to a six-entry rail, every one
of them is a view change, a selection clear and a preference write.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Longer than WheelSteps.minInterval, so a test that means two steps gets two.
BETWEEN_STEPS = 0.2


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


@pytest.fixture(scope="module")
def transient_rail(qt_app):
    """A rail of its own, with two transient slots showing.

    The real window only grows those slots for a dirty editor or a live import,
    neither of which a test wants to arrange to ask a question about
    arithmetic. NavRail imports nothing but Nocturne, so it stands up alone.
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine

    from app.gui.gui_paths import bundled_gui_root

    directory = bundled_gui_root() / "nocturne"

    qml = """
import QtQuick
import Nocturne
import "components"

Window {
    id: probe
    width: 200
    height: 600
    visible: true

    // The owner, playing main.qml's part: the rail reports and this assigns.
    property int index: 5
    property int navigations: 0

    NavRail {
        objectName: "rail"
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        currentIndex: probe.index
        showEdit: true
        showImport: true
        onNavigated: function (i) {
            probe.index = i
            probe.navigations += 1
        }
    }
}
"""
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(directory))
    engine.loadData(qml.encode(), QUrl.fromLocalFile(str(directory / "probe.qml")))

    roots = engine.rootObjects()
    assert roots, "the isolated rail failed to load"
    qt_app.processEvents()

    yield roots[0], qt_app

    del engine


def _item(window, name):
    from PySide6.QtQuick import QQuickItem

    item = window.findChild(QQuickItem, name)
    assert item is not None, f"{name} is gone or renamed"
    return item


def _centre(item, y=None):
    """Where in the window to aim, in scene coordinates.

    Taken off the item itself rather than written down here, so a rail that
    moves or changes width does not quietly start being scrolled somewhere it
    is not.
    """
    from PySide6.QtCore import QPointF

    return item.mapToScene(
        QPointF(item.width() / 2, item.height() / 2 if y is None else y)
    )


def _wheel(window, qt_app, point, *, angle=0, pixels=0, phase=None):
    from PySide6.QtCore import QCoreApplication, QPoint, Qt
    from PySide6.QtGui import QWheelEvent

    event = QWheelEvent(
        point,
        point,
        QPoint(0, pixels),
        QPoint(0, angle),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.NoScrollPhase if phase is None else phase,
        False,
    )
    QCoreApplication.sendEvent(window, event)
    qt_app.processEvents()


def _notch(window, qt_app, point, direction):
    """One turn of a mouse wheel: -1 for down/next, +1 for up/previous."""
    _wheel(window, qt_app, point, angle=120 * direction)
    # The steps are throttled, so back-to-back notches with no time between
    # them are a flick as far as WheelSteps is concerned — which is what the
    # flick test below is for, and the opposite of what this helper means.
    time.sleep(BETWEEN_STEPS)
    qt_app.processEvents()


def _go(window, qt_app, index):
    """Change views the way a rail click does.

    Through the signal, not ``setProperty``: main.qml's ``go()`` is what clears
    the selection and writes the preference, and a property write would skip it
    and leave the rest of the window disagreeing with the number.
    """
    _item(window, "navRail").navigated.emit(index)
    _settle(qt_app)


def _settle(qt_app, seconds=0.45):
    """Pump past the cross-fade, which outlasts the property change."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.02)


def test_a_notch_over_the_rail_moves_one_destination(gui):
    """Down for the next, up for the previous, one per notch.

    Posted as a real event at the rail's real coordinates: everything about
    whether the wheel survives the backdrop, the deselect MouseArea and the
    per-slot MouseAreas on top of it is only answered this way.
    """
    window, qt_app = gui
    rail = _item(window, "navRail")

    _go(window, qt_app, 0)
    point = _centre(rail, y=100)

    _notch(window, qt_app, point, -1)
    assert window.property("viewIndex") == 1, (
        "a notch down over the rail did not move to the next destination — "
        "either the wheel never reached NavRail, or the step was not reported"
    )

    _notch(window, qt_app, point, -1)
    assert window.property("viewIndex") == 2

    _notch(window, qt_app, point, +1)
    assert window.property("viewIndex") == 1, "a notch up should go back one"


def test_the_rail_stops_at_its_ends(gui):
    """No wrap. The top and bottom of the rail are meant to feel like ends."""
    window, qt_app = gui
    rail = _item(window, "navRail")
    point = _centre(rail, y=100)

    _go(window, qt_app, 0)
    _notch(window, qt_app, point, +1)
    assert window.property("viewIndex") == 0, "scrolling up at the top wrapped"

    _go(window, qt_app, 5)
    _notch(window, qt_app, point, -1)
    assert window.property("viewIndex") == 5, "scrolling down at the bottom wrapped"


def test_a_trackpad_flick_does_not_tear_through_the_rail(gui):
    """One swipe is not five view changes.

    Thirty events' worth of pixels with no pause between them — a hard flick,
    and far more than the whole rail is long. What stops it is the floor
    between steps, so the count that matters is how many fit in the time this
    takes rather than how many the pixels would otherwise buy.
    """
    window, qt_app = gui
    rail = _item(window, "navRail")
    point = _centre(rail, y=100)

    _go(window, qt_app, 0)

    started = time.monotonic()
    for _ in range(30):
        _wheel(window, qt_app, point, pixels=-40)
    elapsed = time.monotonic() - started

    moved = window.property("viewIndex")
    # 1200px of swipe is more than twenty steps' worth at the pixel threshold.
    # What it may actually spend is one step per throttle interval, and the
    # loop above is nowhere near long enough to afford the whole rail.
    ceiling = 2 + int(elapsed / 0.13)
    assert 0 < moved <= min(5, ceiling), (
        f"a single flick moved {moved} destinations in {elapsed:.2f}s — the "
        "step throttle is not holding"
    )


def test_the_rail_steps_by_slot_not_by_view_index(transient_rail):
    """With a transient tab up, the next slot down is not the next view index.

    Sitting on Settings (view 5, slot 5) with the editor and import slots
    showing, one notch down is the editor — view 7. Stepping the view index
    instead would ask for view 6, which is the backing workbench: a page with
    no slot in this rail at all, and one the marker could not be drawn on.
    """
    window, qt_app = transient_rail
    rail = _item(window, "rail")
    point = _centre(rail, y=100)

    assert window.property("index") == 5

    _notch(window, qt_app, point, -1)
    assert window.property("index") == 7, (
        "the step walked the view index rather than the rail — with a "
        "transient slot up those are different numbers"
    )

    _notch(window, qt_app, point, -1)
    assert window.property("index") == 8, "and on to the next slot down"

    _notch(window, qt_app, point, -1)
    assert window.property("index") == 8, "which is the last slot; no wrap"

    _notch(window, qt_app, point, +1)
    assert window.property("index") == 7, "back up the way it came"


def test_a_notch_over_the_settings_tabs_moves_one_tab(gui):
    """The same over the underline strip, and still through ``selected``.

    The index has to come back around through SettingsView for the preference
    write and the page glide to happen at all, which is the rule
    test_settings_tabs states: the bar draws what it is handed.
    """
    window, qt_app = gui

    _go(window, qt_app, 5)

    bar = _item(window, "settingsTabBar")
    bar.selected.emit(0)
    _settle(qt_app)

    point = _centre(bar)

    _notch(window, qt_app, point, -1)
    assert bar.property("currentIndex") == 1, (
        "a notch over the tab strip did not move to the next tab — either the "
        "wheel never reached the bar, or the step was not routed through "
        "selected() and SettingsView never pushed the new index back down"
    )

    _notch(window, qt_app, point, +1)
    assert bar.property("currentIndex") == 0

    _notch(window, qt_app, point, +1)
    assert bar.property("currentIndex") == 0, "the first tab is the first tab"
