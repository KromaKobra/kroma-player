"""Sidebar toggle discovery, input ownership, and focus across presentations."""
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QPointF, QUrl, Qt
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from test_sidebar import _visual_item

GUI = Path(__file__).resolve().parents[1] / "gui" / "nocturne"


@pytest.fixture
def sidebar_header(qt_app):
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(GUI))
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
    engine.loadData(b'''
import QtQuick
import QtQuick.Window
import "components"
Window {
    id: window
    width: 940; height: 500; visible: true
    property int moves: 0
    QtObject {
        id: nativeWindow
        function startSystemMove() { window.moves++ }
    }
    TitleBar {
        id: header
        objectName: "header"
        width: parent.width; z: 10
        targetWindow: nativeWindow
        sidebarWidth: rail.width
        sidebarToggleControl: rail.headerToggle
    }
    NavRail {
        id: rail
        objectName: "rail"
        y: header.height
        height: window.height - y
        headerHost: header
        headerHovered: header.sidebarHovered
    }
}
''', QUrl.fromLocalFile(str(GUI / "sidebar-header-test.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    qt_app.processEvents()
    rail = window.findChild(QQuickItem, "rail")
    toggle = window.findChild(QQuickItem, "sidebarToggle")
    yield window, rail, toggle
    assert not warnings, warnings
    window.close()
    del engine


def point(item):
    return item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()


def test_hover_reveals_header_control_from_sidebar_and_header(sidebar_header):
    window, rail, toggle = sidebar_header
    QTest.mouseMove(window, QPoint(800, 300))
    QTest.qWait(180)
    assert toggle.opacity() == 0
    QTest.mouseMove(window, QPoint(100, 350))
    QTest.qWait(180)
    assert toggle.opacity() == 1
    assert point(toggle).y() == 32
    assert toggle.x() + toggle.width() == rail.width() - 12
    QTest.mouseMove(window, QPoint(80, 32))
    QTest.qWait(180)
    assert toggle.opacity() == 1
    # Hover observation leaves ordinary header presses to native dragging.
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(80, 32))
    assert window.property("moves") == 1
    QTest.mouseMove(window, QPoint(800, 300))
    QTest.qWait(180)
    assert toggle.opacity() == 0


def test_collapse_moves_control_to_its_own_entry_and_expands_again(sidebar_header):
    window, rail, toggle = sidebar_header
    QTest.mouseMove(window, QPoint(100, 350))
    QTest.qWait(180)
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point(toggle))
    QTest.qWait(650)
    assert rail.property("collapsed")
    assert rail.width() == pytest.approx(64, abs=0.25)
    assert toggle.y() >= rail.y()
    assert toggle.x() == pytest.approx(12, abs=0.25)
    QTest.mouseMove(window, QPoint(800, 300))
    QTest.qWait(180)
    assert toggle.opacity() == 1
    assert window.property("moves") == 0
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point(toggle))
    QTest.qWait(650)
    assert not rail.property("collapsed")
    assert point(toggle).y() == 32
    assert window.property("moves") == 0


def test_keyboard_focus_reveals_control_and_survives_relocation(sidebar_header):
    window, rail, toggle = sidebar_header
    rail.setProperty("reducedMotion", True)
    QTest.mouseMove(window, QPoint(800, 300))
    assert toggle.opacity() == 0
    toggle.forceActiveFocus(Qt.TabFocusReason)
    assert toggle.opacity() == 1
    QTest.keyClick(window, Qt.Key_Space)
    assert rail.width() == 64
    assert toggle.y() == 72
    assert toggle.hasActiveFocus()
    QTest.keyClick(window, Qt.Key_Space)
    assert rail.width() == 240
    assert toggle.y() == 14
    assert toggle.hasActiveFocus()


def test_reversing_relocation_continues_from_displayed_position(sidebar_header):
    window, rail, toggle = sidebar_header
    toggle.forceActiveFocus(Qt.TabFocusReason)
    QTest.keyClick(window, Qt.Key_Space)
    QTest.qWait(60)
    midpoint = toggle.y()
    assert 14 < midpoint < 72
    QTest.keyClick(window, Qt.Key_Space)
    assert abs(toggle.y() - midpoint) < 8
    for _ in range(25):
        QTest.qWait(20)
        assert 14 <= toggle.y() <= 72
    assert toggle.y() == 14
    assert not rail.property("collapsed")


def start_resize(window, rail):
    handle = rail.findChild(QQuickItem, "sidebarResizeHandle")
    start = point(handle), rail.width()
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start[0])
    return handle, start


def move_resize(window, start, width):
    end = start[0] + QPoint(round(width - start[1]), 0)
    QTest.mouseMove(window, end, 20)
    return end


@pytest.mark.parametrize("reduced_motion", [False, True])
def test_held_resize_moves_toggle_before_release_in_both_directions(sidebar_header, reduced_motion):
    window, rail, toggle = sidebar_header
    rail.setProperty("reducedMotion", reduced_motion)
    handle, start = start_resize(window, rail)
    # Resistance above the collapse threshold moves the button progressively,
    # while the stored collapsed state is still unchanged.
    move_resize(window, start, 180)
    first_y = toggle.y()
    move_resize(window, start, 170)
    assert 14 < first_y < toggle.y() < 72
    assert not rail.property("collapsed")
    for _ in range(2):
        move_resize(window, start, handle.property("effectiveCollapseThreshold") - 10)
        QTest.qWait(350)
        assert handle.property("pressed")
        assert handle.property("previewCollapsed")
        assert not rail.property("collapsed")
        assert toggle.y() == 72
        # The collapsed entry gets space before release, so it cannot cover Home.
        home = _visual_item(rail, "nav_Home")
        assert toggle.y() + toggle.height() < home.mapToScene(QPointF(0, 0)).y()
        expand = handle.property("effectiveExpansionThreshold")
        end = move_resize(window, start, expand)
        QTest.qWait(350)
        assert handle.property("pressed")
        assert not handle.property("previewCollapsed")
        assert not rail.property("collapsed")
        assert toggle.y() == 14
    before_release = toggle.y()
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end)
    assert toggle.y() == before_release


def test_cancelling_held_collapse_returns_toggle_with_sidebar(sidebar_header):
    window, rail, toggle = sidebar_header
    handle, start = start_resize(window, rail)
    move_resize(window, start, 130)
    QTest.qWait(350)
    assert toggle.y() == 72
    assert not rail.property("collapsed")
    handle.setEnabled(False)
    QTest.qWait(650)
    assert rail.width() == 240
    assert toggle.y() == 14
    assert not rail.property("collapsed")


@pytest.mark.parametrize("reduced_motion", [False, True])
def test_expanding_closed_sidebar_moves_toggle_up_while_mouse_is_held(sidebar_header, reduced_motion):
    window, rail, toggle = sidebar_header
    rail.setProperty("reducedMotion", True)
    rail.setProperty("collapsed", True)
    rail.setProperty("reducedMotion", reduced_motion)
    assert toggle.y() == 72
    handle, start = start_resize(window, rail)
    end = move_resize(window, start, handle.property("effectiveExpansionThreshold") + 1)
    if not reduced_motion:
        QTest.qWait(40)
        assert 14 < toggle.y() < 72
    QTest.qWait(350)
    assert handle.property("pressed")
    assert rail.property("collapsed")
    assert toggle.y() == 14
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end)
    assert not rail.property("collapsed")
    assert toggle.y() == 14
