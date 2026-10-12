"""A moving sidebar edge relays out what is on screen, and nothing else.

Every page in the window stays alive while hidden, to keep its scroll position
and state. Anchored, each of them followed the content area through every frame
of a sidebar drag — track tables, grids and their artwork relaid out behind the
one page anyone could see. `SizeWhileVisible` holds a hidden page at the size it
last had and catches it up in the same turn it is shown.

The rounded-corner masks are the other half. A mask is a multisampled layer, so
every change of size reallocates it; while `LiveResize` is active a mask that
follows its view holds its size and settles once the edge stops.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Q_ARG, QMetaObject, QPoint, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from test_hidden_tabs import gui as application_gui  # noqa: F401 (fixture)
from test_hidden_tabs import _call

GUI = Path(__file__).resolve().parents[1] / "gui" / "nocturne"
PLAYLISTS_VIEW_INDEX = 3


def _item(window, name):
    item = window.findChild(QQuickItem, name)
    assert item is not None, f"{name} is gone or renamed"
    return item


def _grip(handle):
    # The rail's half of the grip: the other half lies under the content area.
    return handle.mapToScene(QPointF(handle.width() / 4, handle.height() / 2)).toPoint()


@pytest.fixture
def mask_probe(qt_app):
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(GUI))
    warnings = []
    engine.warnings.connect(lambda messages: warnings.extend(m.toString() for m in messages))
    engine.loadData(b'''
import QtQuick
import QtQuick.Window
import Nocturne
import "components/artwork"
Window {
    id: probe
    width: 300; height: 300; visible: true
    property bool live: false
    property real size: 100
    property bool late: false
    Binding { target: LiveResize; property: "active"; value: probe.live }
    CornerMask {
        objectName: "mask"
        targetWidth: probe.size; targetHeight: probe.size
        radius: 8
    }
    Loader {
        active: probe.late
        sourceComponent: CornerMask {
            objectName: "lateMask"
            targetWidth: probe.size; targetHeight: probe.size
            radius: 8
        }
    }
}
''', QUrl.fromLocalFile(str(GUI / "mask-probe.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    qt_app.processEvents()
    yield window
    window.setProperty("live", False)
    window.close()
    assert not warnings, warnings
    del engine


def test_mask_holds_its_size_while_an_edge_moves(mask_probe):
    window = mask_probe
    mask = _item(window, "mask")
    assert (mask.width(), mask.height()) == (100, 100)

    window.setProperty("live", True)
    window.setProperty("size", 140)
    assert (mask.width(), mask.height()) == (100, 100), (
        "the mask followed its target mid-drag, reallocating its layer every frame"
    )

    window.setProperty("live", False)
    assert (mask.width(), mask.height()) == (140, 140), (
        "the mask did not settle to its exact size when the edge stopped"
    )


def test_mask_created_mid_drag_starts_at_its_target(mask_probe):
    window = mask_probe
    window.setProperty("live", True)
    window.setProperty("size", 120)
    window.setProperty("late", True)
    late = _item(window, "lateMask")
    assert (late.width(), late.height()) == (120, 120), (
        "a mask built during a drag has nothing to hold and must not start empty"
    )


def test_hidden_pages_hold_their_size_while_the_rail_moves(application_gui):
    window, qt_app = application_gui
    _call(window, qt_app, "goLibrary", 0)
    content = _item(window, "contentArea")
    nav = _item(window, "navRail")
    handle = _item(window, "sidebarResizeHandle")
    songs = _item(window, "libraryTable")
    albums = _item(window, "albumCollection")
    playlists = _item(window, "playlistsView")

    assert songs.isVisible() and not albums.isVisible() and not playlists.isVisible()
    held_albums, held_playlists = albums.width(), playlists.width()

    start = _grip(handle)
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start)
    end = start + QPoint(60, 0)
    QTest.mouseMove(window, end, 150)
    assert nav.property("moving")
    assert content.width() != pytest.approx(held_playlists, abs=1), (
        "the drag did not change the content area, so this proves nothing"
    )

    # The page on screen follows the edge; the ones behind it do not.
    assert songs.width() == pytest.approx(content.width(), abs=0.5)
    assert playlists.width() == held_playlists, "a hidden page relaid out with the drag"
    assert albums.width() == held_albums, "a hidden library sub-page relaid out with the drag"

    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end)
    QTest.qWait(650)
    assert not nav.property("moving")

    # Shown, a held page catches up the moment it turns visible — the first
    # tick of its fade-in, before that frame is drawn — so it is never stale.
    ok = QMetaObject.invokeMethod(
        window, "go", Qt.DirectConnection, Q_ARG("QVariant", PLAYLISTS_VIEW_INDEX)
    )
    assert ok
    for _ in range(100):
        if playlists.isVisible():
            break
        qt_app.processEvents()
        QTest.qWait(1)
    assert playlists.isVisible()
    assert playlists.width() == pytest.approx(content.width(), abs=0.5)
    assert playlists.height() == pytest.approx(content.height(), abs=0.5)

    # Put the rail back for whatever runs next against this window.
    _call(window, qt_app, "goLibrary", 0)
    start = _grip(handle)
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(window, start - QPoint(60, 0), 150)
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, start - QPoint(60, 0))
    QTest.qWait(650)
