"""Only overflowing, visible viewports may show the shared scrollbar.

Exercise attached bars and bars reparented outside clipped library views, plus
the real navigation rail. Qt's AsNeeded policy alone does not hide a custom
contentItem, so loading the QML successfully is not enough to catch this bug.
"""
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, QUrl, Qt
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest


SURFACES = {
    "flickable": """
        Flickable {
            id: view; objectName: "view"; anchors.fill: parent
            contentWidth: width; contentHeight: probe.contentExtent
            ScrollBar.vertical: EdgeScrollBar {}
        }
    """,
    "scrollview": """
        ScrollView {
            id: view; objectName: "view"; anchors.fill: parent
            contentWidth: availableWidth; contentHeight: probe.contentExtent
            ScrollBar.vertical: EdgeScrollBar {}
            Item { width: 300; height: probe.contentExtent }
        }
    """,
    "list": """
        LibraryList {
            id: view; objectName: "view"; anchors.fill: parent
            scrollBarInset: 20
            model: probe.rowCount
            delegate: Item { width: view.width; height: 40 }
        }
    """,
    "grid": """
        LibraryGrid {
            id: view; objectName: "view"; anchors.fill: parent
            scrollBarInset: 20
            tileSize: 100; labelHeight: 0
            model: rows
            delegate: Item { width: view.cellWidth; height: view.cellHeight }
        }
    """,
}


@pytest.fixture
def surface(qt_app, request):
    directory = Path(__file__).resolve().parents[1] / "gui/nocturne"
    body = SURFACES.get(request.param, "NavRail { anchors.fill: parent }")
    qml = """
import QtQuick
import QtQuick.Controls
import Nocturne
import "components/library"
import "components/shell"
Window {
    id: probe
    width: 400; height: 900; visible: true
    property real viewportHeight: 200
    property real contentExtent: 0
    property int rowCount: 0
    property bool pageVisible: true
    ListModel { id: rows }
    onRowCountChanged: {
        rows.clear()
        for (var i = 0; i < rowCount; ++i) rows.append({title: "Track " + i})
    }
    Item {
        x: 20; y: 20; width: 360; height: probe.viewportHeight
        visible: probe.pageVisible
        BODY
    }
}
""".replace("BODY", body)
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(directory))
    warnings = []
    engine.warnings.connect(lambda entries: warnings.extend(w.toString() for w in entries))
    engine.loadData(qml.encode(), QUrl.fromLocalFile(str(directory / "__scrollbar_test.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    QTest.qWait(30)
    bars = [obj for obj in window.findChildren(QObject)
            if obj.metaObject().className().startswith("EdgeScrollBar_")]
    assert len(bars) == 1
    yield window, bars[0]
    window.close()
    del engine
    assert not warnings, warnings


def _set(window, **properties):
    for name, value in properties.items():
        assert window.setProperty(name, value)
    QTest.qWait(30)


@pytest.mark.parametrize("surface", SURFACES, indirect=True)
def test_visibility_tracks_content_and_viewport(surface):
    window, bar = surface
    assert not bar.isVisible(), "empty content must not show a scrollbar"

    _set(window, viewportHeight=200, contentExtent=80, rowCount=1)
    assert not bar.isVisible(), "content that fits must not show a scrollbar"
    QTest.mouseMove(window, QPoint(399, 50))
    QTest.qWait(150)
    assert not bar.isVisible(), "hover must not reveal an unnecessary scrollbar"

    _set(window, contentExtent=800, rowCount=6)
    assert bar.isVisible(), "overflowing content needs a scrollbar"

    view = window.findChild(QObject, "view")
    content_height = view.property("contentHeight")
    _set(window, viewportHeight=content_height)
    assert not bar.isVisible(), "an exact fit must not show a scrollbar"
    _set(window, viewportHeight=content_height + 20)
    assert not bar.isVisible(), "enlarging the viewport must hide the scrollbar"
    _set(window, viewportHeight=100)
    assert bar.isVisible(), "shrinking the viewport must restore the scrollbar"

    _set(window, pageVisible=False)
    assert not bar.isVisible(), "hidden pages must not leak reparented scrollbars"
    _set(window, pageVisible=True)
    assert bar.isVisible()
    assert view.setProperty("visible", False)
    QTest.qWait(30)
    assert not bar.isVisible(), "hiding just the view must also hide its scrollbar"
    assert view.setProperty("visible", True)
    QTest.qWait(30)
    assert bar.isVisible()
    _set(window, viewportHeight=0)
    assert not bar.isVisible(), "a zero-height viewport must not show a scrollbar"
    _set(window, viewportHeight=200, contentExtent=80, rowCount=1)
    assert not bar.isVisible(), "removing overflow must hide the scrollbar"
    _set(window, contentExtent=0, rowCount=0)
    assert not bar.isVisible(), "clearing content must hide the scrollbar"


@pytest.mark.parametrize("surface", ["sidebar"], indirect=True)
def test_sidebar_only_shows_scrollbar_when_destinations_overflow(surface):
    window, bar = surface
    _set(window, viewportHeight=800)
    assert not bar.isVisible(), "all sidebar destinations fit at this height"
    _set(window, viewportHeight=200)
    assert bar.isVisible(), "a short sidebar still needs access to clipped destinations"
    _set(window, viewportHeight=800)
    assert not bar.isVisible(), "restoring sidebar height must hide the scrollbar"


@pytest.mark.parametrize("surface", SURFACES, indirect=True)
def test_empty_track_is_not_interactive_without_a_resize_handle(surface):
    window, bar = surface
    _set(window, contentExtent=800, rowCount=60)
    assert bar.isVisible()
    bar.setProperty("position", (1 - bar.property("size")) / 2)
    QTest.qWait(30)
    position = bar.property("position")
    thumb = bar.property("contentItem")
    for y in (thumb.y() / 2, (thumb.y() + thumb.height() + bar.height()) / 2):
        point = bar.mapToScene(QPointF(bar.width() - 2, y)).toPoint()
        QTest.mouseMove(window, point)
        QTest.qWait(30)
        assert not bar.property("hovered")
        QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, point)
        assert not bar.property("pressed")
        QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, point)
        assert bar.property("position") == pytest.approx(position)
