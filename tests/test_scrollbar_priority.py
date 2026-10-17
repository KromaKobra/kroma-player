"""Overlapping resize grips must yield both hover and presses to scrollbars."""
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, QUrl, Qt
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest


@pytest.fixture(params=["navigation", "details", "window"])
def overlap(qt_app, request):
    directory = Path(__file__).resolve().parents[1] / "gui/nocturne"
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(directory))
    warnings = []
    engine.warnings.connect(lambda entries: warnings.extend(w.toString() for w in entries))
    engine.loadData(b'''
import QtQuick
import QtQuick.Controls
import Nocturne
import "components/library"
import "components/shell"
Window {
    id: probe
    width: 1000; height: 400; visible: true
    property int commits: 0
    property int windowResizes: 0
    property int rowCount: 50
    NavRail {
        id: nav; objectName: "nav"; height: parent.height
        reducedMotion: true
    }
    Item {
        anchors.left: nav.right; anchors.right: panel.left
        height: parent.height
        LibraryList {
            id: tracks; objectName: "tracks"; anchors.fill: parent
            model: probe.rowCount
            delegate: Item { width: tracks.width; height: 40 }
        }
    }
    Item {
        id: panel
        anchors.right: parent.right; width: handle.displayedWidth
        height: parent.height
        Flickable {
            id: details; objectName: "details"; anchors.fill: parent
            contentHeight: probe.rowCount * 40
            ScrollBar.vertical: EdgeScrollBar {}
        }
    }
    SidebarResizeHandle {
        id: handle; objectName: "handle"
        x: panel.x - width / 2; height: parent.height
        expandedWidth: 260; minimumExpandedWidth: 240
        maximumWidth: 520; rightSidebar: true; reducedMotion: true
        onResizeCommitted: probe.commits += 1
    }
    ResizeGrips {
        targetWindow: QtObject {
            property int visibility: Window.Windowed
            function startSystemResize(edges) { probe.windowResizes += 1 }
        }
    }
}
''', QUrl.fromLocalFile(str(directory / "__scrollbar_priority.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    QTest.qWait(50)
    right_edge = {"navigation": 240, "details": 740, "window": 1000}[request.param]
    bars = [obj for obj in window.findChildren(QObject)
            if obj.metaObject().className().startswith("EdgeScrollBar_")]
    bar = next(bar for bar in bars
               if round(bar.mapToScene(QPointF(bar.width(), 0)).x()) == right_edge)
    handle_name = "sidebarResizeHandle" if request.param == "navigation" else "handle"
    handle = window.findChild(QQuickItem, handle_name)
    yield window, bar, handle, request.param
    window.close()
    del engine
    assert not warnings, warnings


def _scroll_point(bar):
    thumb = bar.property("contentItem")
    return bar.mapToScene(QPointF(bar.width() - 2, thumb.y() + min(20, thumb.height() / 2))).toPoint()


@pytest.mark.parametrize("thumb_position", [0, 0.5])
def test_scrollbar_wins_hover_and_drag_in_overlap(overlap, thumb_position):
    window, bar, handle, side = overlap
    bar.setProperty("position", (1 - bar.property("size")) * thumb_position)
    QTest.qWait(30)
    position = bar.property("position")
    start = _scroll_point(bar)
    QTest.mouseMove(window, start)
    QTest.qWait(30)
    assert bar.property("hovered"), "resize grip intercepted scrollbar hover"
    if side != "window":
        assert not handle.property("containsMouse")
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start)
    assert bar.property("pressed"), "resize grip intercepted scrollbar press"
    assert not handle.property("pressed")
    # Leaving the overlap during the drag must keep the scrollbar's grab.
    end = start + QPoint(-40, 80)
    QTest.mouseMove(window, end, 50)
    assert bar.property("pressed")
    assert bar.property("position") > position
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end)
    assert window.property("commits") == 0
    assert window.property("windowResizes") == 0


def test_rest_of_divider_still_resizes(overlap):
    window, bar, handle, side = overlap
    if side == "window":
        # An edge away from scrollbars must still resize the window.
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(2, 200))
        assert window.property("windowResizes") == 1
        return
    start = (_scroll_point(bar) - QPoint(0, 52) if side == "navigation"
             else _scroll_point(bar) + QPoint(5, 0))
    QTest.mouseMove(window, start)
    QTest.qWait(30)
    assert handle.property("containsMouse")
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start)
    assert handle.property("pressed")
    assert not bar.property("pressed")
    old_width = handle.property("displayedWidth")
    # Crossing back over the scrollbar must preserve an existing resize grab.
    end = start + QPoint(-20, 80)
    QTest.mouseMove(window, end, 50)
    assert handle.property("pressed")
    assert handle.property("displayedWidth") != old_width
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end)


def test_hidden_scrollbar_yields_to_divider(overlap):
    window, bar, handle, side = overlap
    start = _scroll_point(bar)
    if side == "navigation":
        window.setHeight(900)
    else:
        window.setProperty("rowCount", 0)
    QTest.qWait(30)
    assert not bar.isVisible()
    QTest.mouseMove(window, start)
    QTest.qWait(30)
    if side != "window":
        assert handle.property("containsMouse")
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start)
    if side == "window":
        assert window.property("windowResizes") == 1
    else:
        assert handle.property("pressed")
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, start)


@pytest.mark.parametrize("track_part", ["above", "below"])
def test_empty_track_yields_hover_and_drag_to_resize(overlap, track_part):
    window, bar, handle, side = overlap
    # Test both sides of a thumb away from either end, using Qt's real thumb
    # geometry rather than assuming it fills any particular fraction of a view.
    bar.setProperty("position", (1 - bar.property("size")) / 2)
    QTest.qWait(30)
    thumb = bar.property("contentItem")
    y = (thumb.y() / 2 if track_part == "above"
         else (thumb.y() + thumb.height() + bar.height()) / 2)
    start = bar.mapToScene(QPointF(bar.width() - 2, y)).toPoint()
    position = bar.property("position")
    QTest.mouseMove(window, start)
    QTest.qWait(30)
    assert not bar.property("hovered"), "empty track must not claim hover"
    if side != "window":
        assert handle.property("containsMouse")
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start)
    assert not bar.property("pressed"), "empty track must not grab or jump the scrollbar"
    if side == "window":
        assert window.property("windowResizes") == 1
    else:
        assert handle.property("pressed")
        old_width = handle.property("displayedWidth")
        end = start + QPoint(-20, 40)
        QTest.mouseMove(window, end, 50)
        assert handle.property("pressed")
        assert handle.property("displayedWidth") != old_width
        start = end
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, start)
    assert bar.property("position") == pytest.approx(position)
