"""Real pointer and keyboard interactions with the resizable QML sidebar."""
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QPointF, QUrl, Qt
from PySide6.QtQml import QQmlApplicationEngine
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
    id: probe
    width: 1000; height: 720; visible: true
    property int index: 0
    property int mode: 0
    NavRail {
        objectName: "rail"
        height: parent.height
        currentIndex: probe.index
        libraryMode: probe.mode
        onNavigated: function (i) { probe.index = i }
        onLibraryNavigated: function (m) { probe.mode = m; probe.index = 4 }
    }
}
''', QUrl.fromLocalFile(str(GUI / "sidebar-probe.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    qt_app.processEvents()
    rail = window.findChild(QQuickItem, "rail")
    yield window, rail
    assert not warnings, warnings
    window.close()
    del engine


def _point(item):
    return item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()


def _visual_item(parent, name):
    # Repeater delegates have a visual parent, not necessarily QObject ownership.
    if parent.objectName() == name:
        return parent
    for child in parent.childItems():
        found = _visual_item(child, name)
        if found is not None:
            return found
    return None


def _click(window, item):
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, _point(item))
    QTest.qWait(30)


def _settle():
    QTest.qWait(650)


def test_toggle_animates_and_can_reverse_without_jumping(sidebar):
    window, rail = sidebar
    toggle = rail.findChild(QQuickItem, "sidebarToggle")
    _click(window, toggle)
    QTest.qWait(65)
    midway = rail.width()
    assert 64 < midway < 240
    _click(window, toggle)
    # Retargeting starts at the presentation width, not the previous target.
    assert abs(rail.width() - midway) < 30
    _settle()
    assert rail.width() == pytest.approx(240, abs=0.25)
    assert not rail.property("collapsed")


def _drag(window, rail, delta):
    handle = rail.findChild(QQuickItem, "sidebarResizeHandle")
    start = _point(handle)
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(window, start + QPoint(delta, 0), 150)
    return start + QPoint(delta, 0)


def test_drag_tracks_mouse_and_toggle_restores_custom_width(sidebar):
    window, rail = sidebar
    end = _drag(window, rail, 72)
    assert rail.width() == pytest.approx(312, abs=1)
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end)
    QTest.qWait(50)
    assert rail.width() == pytest.approx(312, abs=1)
    _settle()
    assert rail.property("expandedWidth") == pytest.approx(312, abs=1)
    toggle = rail.findChild(QQuickItem, "sidebarToggle")
    _click(window, toggle)
    _settle()
    assert rail.width() == pytest.approx(64, abs=0.25)
    _click(window, toggle)
    _settle()
    assert rail.width() == pytest.approx(312, abs=1)


def test_drag_left_snaps_and_can_expand_from_icons(sidebar):
    window, rail = sidebar
    end = _drag(window, rail, -110)
    handle = rail.findChild(QQuickItem, "sidebarResizeHandle")
    assert handle.property("previewCollapsed")
    assert not rail.property("collapsed")
    assert handle.property("pressed")
    _settle()
    assert rail.width() == 64
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end)
    _settle()
    assert rail.property("collapsed")
    assert rail.width() == pytest.approx(64, abs=0.25)
    assert rail.property("expandedWidth") == 240
    end = _drag(window, rail, 160)
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end)
    _settle()
    assert not rail.property("collapsed")
    assert rail.width() == pytest.approx(224, abs=1)


def test_reduced_motion_and_keyboard_activation(sidebar):
    window, rail = sidebar
    rail.setProperty("reducedMotion", True)
    toggle = rail.findChild(QQuickItem, "sidebarToggle")
    toggle.forceActiveFocus()
    QTest.keyClick(window, Qt.Key_Space)
    assert rail.width() == 64
    QTest.keyClick(window, Qt.Key_Space)
    assert rail.width() == 240


def test_concept_destinations_use_existing_library_modes(sidebar):
    window, rail = sidebar
    for label, mode in [("Songs", 0), ("Albums", 2), ("Artists", 1)]:
        destination = _visual_item(rail, "nav_" + label)
        _click(window, destination)
        assert window.property("index") == 4
        assert window.property("mode") == mode
        assert destination.property("selected")
    _click(window, _visual_item(rail, "nav_Home"))
    assert window.property("index") == 13
    assert not rail.findChild(QQuickItem, "nav_Backing tracks")
    assert not rail.findChild(QQuickItem, "nav_Library health")


def test_width_limits_preserve_preferred_size(sidebar):
    window, rail = sidebar
    rail.setProperty("reducedMotion", True)
    rail.setProperty("expandedWidth", 340)
    rail.setProperty("maximumWidth", 260)
    assert rail.width() == 260
    assert rail.property("expandedWidth") == 340
    rail.setProperty("maximumWidth", 360)
    assert rail.width() == 340


def test_window_routes_home_library_and_persists_sidebar(application_gui):
    from PySide6.QtQml import QQmlEngine

    window, qt_app = application_gui
    rail = window.findChild(QQuickItem, "navRail")
    settings = QQmlEngine.contextForObject(window).contextProperty("settingsVM")
    rail.setProperty("reducedMotion", True)
    rail.setProperty("expandedWidth", 280)
    rail.setProperty("collapsed", False)
    _click(window, _visual_item(rail, "nav_Home"))
    _settle()
    assert window.property("viewIndex") == 13
    assert window.findChild(QQuickItem, "homeView").isVisible()
    content = window.findChild(QQuickItem, "contentArea")
    assert content.x() == rail.width()
    for label, mode in [("Albums", 2), ("Artists", 1), ("Songs", 0)]:
        _click(window, _visual_item(rail, "nav_" + label))
        assert window.property("viewIndex") == 4
        assert rail.property("libraryMode") == mode
        assert window.findChild(QQuickItem, "libraryModePill").property("currentIndex") == mode
    QTest.keyClick(window, Qt.Key_B, Qt.ControlModifier)
    qt_app.processEvents()
    assert rail.width() == 64
    window.close()
    assert settings.sessionValue("window.sidebarWidth", 0) == 280
    assert settings.sessionValue("window.sidebarCollapsed", False)


def test_fast_drag_release_stays_within_width_limits(sidebar):
    window, rail = sidebar
    end = _drag(window, rail, 250)
    assert rail.width() == 360
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end)
    for _ in range(12):
        QTest.qWait(20)
        assert 64 <= rail.width() <= 360


def test_grabbing_a_closing_sidebar_starts_at_displayed_width(sidebar):
    window, rail = sidebar
    _click(window, rail.findChild(QQuickItem, "sidebarToggle"))
    QTest.qWait(60)
    start_width = rail.width()
    end = _drag(window, rail, 90)
    assert rail.width() == pytest.approx(start_width + 90, abs=1)
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end)
    _settle()
    assert not rail.property("collapsed")
