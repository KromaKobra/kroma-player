"""Mirror the left sidebar gestures, including closing to zero width."""
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QPointF, QUrl, Qt
from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from test_hidden_tabs import gui as application_gui

GUI = Path(__file__).resolve().parents[1] / "gui" / "nocturne"


@pytest.fixture
def sidebar(qt_app):
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(GUI))
    warnings = []
    engine.warnings.connect(lambda messages: warnings.extend(m.toString() for m in messages))
    engine.loadData(b'''
import QtQuick
import QtQuick.Window
import "components"
Window {
    id: window
    width: 1000; height: 720; visible: true
    property bool panelOpen: true
    property real preferredWidth: 320
    property bool reducedMotion: false
    Rectangle {
        id: panel
        objectName: "panel"
        anchors.right: parent.right
        height: parent.height
        width: handle.displayedWidth
        visible: width > 0
        color: "#101218"
    }
    SidebarResizeHandle {
        id: handle
        objectName: "handle"
        x: panel.x - width / 2
        height: panel.height
        expandedWidth: window.preferredWidth
        collapsed: !window.panelOpen
        collapsedWidth: 0
        minimumExpandedWidth: 240
        maximumWidth: 520
        rightSidebar: true
        reducedMotion: window.reducedMotion
        visible: panel.width > 0 || pressed
        onResizeCommitted: function (preferred, collapse) {
            window.preferredWidth = preferred
            window.panelOpen = !collapse
        }
    }
}
''', QUrl.fromLocalFile(str(GUI / "right-sidebar-probe.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    qt_app.processEvents()
    panel = window.findChild(QQuickItem, "panel")
    handle = window.findChild(QQuickItem, "handle")
    yield window, panel, handle
    window.close()
    assert not warnings, warnings
    del engine


def _point(handle):
    return handle.mapToScene(QPointF(handle.width() / 2, handle.height() / 2)).toPoint()


def _drag(window, handle, delta):
    start = _point(handle)
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(window, start + QPoint(delta, 0), 150)
    return start + QPoint(delta, 0)


def _release(window, point):
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, point)
    QTest.qWait(650)


def test_drag_tracks_pointer_and_reopens_at_custom_width(sidebar):
    window, panel, handle = sidebar
    end = _drag(window, handle, -100)
    assert panel.width() == pytest.approx(420, abs=1)
    _release(window, end)
    assert window.property("preferredWidth") == pytest.approx(420, abs=1)
    window.setProperty("panelOpen", False)
    QTest.qWait(650)
    assert panel.width() == 0
    assert not handle.isVisible()
    assert not handle.property("animating")
    window.setProperty("panelOpen", True)
    QTest.qWait(650)
    assert panel.width() == pytest.approx(420, abs=1)


def test_drag_right_beyond_threshold_closes_completely(sidebar):
    window, panel, handle = sidebar
    end = _drag(window, handle, 190)
    assert panel.width() == pytest.approx(130, abs=1)
    assert handle.property("pressed")
    _release(window, end)
    assert not window.property("panelOpen")
    assert panel.width() == 0
    assert not panel.isVisible()
    assert not handle.isVisible()
    assert window.property("preferredWidth") == 320


def test_small_rightward_drag_stays_open_at_minimum_width(sidebar):
    window, panel, handle = sidebar
    end = _drag(window, handle, 120)
    assert panel.width() == pytest.approx(200, abs=1)
    # A held resize tests the distance threshold independently of flick velocity.
    QTest.qWait(110)
    _release(window, end)
    assert window.property("panelOpen")
    assert panel.width() == 240


def test_toggling_mid_flight_retargets_at_presentation_width(sidebar):
    window, panel, handle = sidebar
    window.setProperty("panelOpen", False)
    QTest.qWait(80)
    midway = panel.width()
    assert 0 < midway < 320
    window.setProperty("panelOpen", True)
    assert panel.width() == midway
    QTest.qWait(650)
    assert panel.width() == 320
    assert not handle.property("animating")


def test_grab_closing_panel_tracks_from_current_width(sidebar):
    window, panel, handle = sidebar
    window.setProperty("panelOpen", False)
    QTest.qWait(60)
    midway = panel.width()
    end = _drag(window, handle, -100)
    assert panel.width() == pytest.approx(midway + 100, abs=1)
    assert not handle.property("animating")
    _release(window, end)
    assert window.property("panelOpen")
    assert panel.width() == pytest.approx(max(240, midway + 100), abs=1)


def test_drag_to_zero_keeps_pointer_capture_until_release(sidebar):
    window, panel, handle = sidebar
    end = _drag(window, handle, 350)
    assert panel.width() == 0
    assert handle.property("pressed")
    assert handle.isVisible()
    _release(window, end)
    assert not window.property("panelOpen")
    assert panel.width() == 0
    assert not handle.isVisible()


def test_fast_drag_and_window_constraint_keep_width_in_bounds(sidebar):
    window, panel, handle = sidebar
    end = _drag(window, handle, -300)
    assert panel.width() == 520
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end)
    for _ in range(12):
        QTest.qWait(20)
        assert 0 <= panel.width() <= 520
    handle.setProperty("maximumWidth", 280)
    QTest.qWait(650)
    assert panel.width() == 280
    assert window.property("preferredWidth") == 520
    handle.setProperty("maximumWidth", 520)
    QTest.qWait(650)
    assert panel.width() == 520


def test_reduced_motion_still_resizes_directly_and_snaps(sidebar):
    window, panel, handle = sidebar
    window.setProperty("reducedMotion", True)
    window.setProperty("panelOpen", False)
    assert panel.width() == 0
    window.setProperty("panelOpen", True)
    assert panel.width() == 320
    end = _drag(window, handle, 200)
    assert panel.width() == 120
    _release(window, end)
    assert panel.width() == 0
    assert not handle.property("animating")


def test_actual_window_reclaims_space_and_saves_closed_state(application_gui):
    window, qt_app = application_gui
    panel = window.findChild(QQuickItem, "infoPanel")
    handle = window.findChild(QQuickItem, "infoResizeHandle")
    content = window.findChild(QQuickItem, "contentArea")
    nav = window.findChild(QQuickItem, "navRail")
    settings = QQmlEngine.contextForObject(window).contextProperty("settingsVM")
    assert panel.width() == 0
    assert not handle.isVisible()
    window.setProperty("infoOpen", True)
    QTest.qWait(650)
    assert panel.width() == 320
    end = _drag(window, handle, -80)
    assert panel.width() == pytest.approx(400, abs=1)
    _release(window, end)
    assert window.property("infoWidth") == pytest.approx(400, abs=1)
    end = _drag(window, handle, 270)
    _release(window, end)
    assert not window.property("infoOpen")
    assert panel.width() == 0
    assert not handle.isVisible()
    assert content.width() == pytest.approx(window.width() - nav.width(), abs=1)
    QTest.keyClick(window, Qt.Key_I, Qt.ControlModifier)
    QTest.qWait(650)
    assert window.property("infoOpen")
    assert panel.width() == 400
    QTest.keyClick(window, Qt.Key_I, Qt.ControlModifier)
    QTest.qWait(650)
    window.close()
    assert settings.sessionValue("window.infoWidth", 0) == 400
    assert not settings.sessionValue("window.infoOpen", True)
