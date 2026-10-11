"""Songs header semantics and layout, without an audio device or a real vault."""
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl, Qt
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from test_hidden_tabs import gui as application_gui


@pytest.fixture
def header(qt_app):
    directory = Path(__file__).resolve().parents[1] / "gui" / "nocturne"
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(directory))
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
    engine.loadData(b'''
import QtQuick
import Nocturne
import "components"
Window {
    id: window
    width: 900; height: 240; visible: true
    color: Theme.bg
    property int playRequests: 0
    property int shuffleRequests: 0
    property int optionsRequests: 0
    SongsHeader {
        objectName: "header"
        width: parent.width
        onPlayRequested: window.playRequests++
        onShuffleRequested: window.shuffleRequests++
        onOptionsRequested: window.optionsRequests++
    }
}
''', QUrl.fromLocalFile(str(directory / "songs-header-test.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    qt_app.processEvents()
    yield window, window.findChild(QQuickItem, "header"), qt_app
    assert not warnings, warnings
    window.close()
    del engine


def test_summary_updates_and_empty_library_disables_playback(header):
    window, item, app = header
    summary = item.findChild(QQuickItem, "songsSummary")
    play = item.findChild(QQuickItem, "songsPlayButton")
    shuffle = item.findChild(QQuickItem, "songsShuffleButton")
    assert summary.property("text") == "0 songs · 0 sec"
    assert not play.isEnabled()
    assert not shuffle.isEnabled()
    item.setProperty("songCount", 1)
    item.setProperty("totalDuration", 180)
    app.processEvents()
    assert summary.property("text") == "1 song · 3 min"
    assert play.isEnabled()
    assert not shuffle.isEnabled()
    item.setProperty("songCount", 154)
    item.setProperty("totalDuration", 36780)
    app.processEvents()
    assert summary.property("text") == "154 songs · 10 hr 13 min"
    assert shuffle.isEnabled()


def test_all_actions_support_keyboard_activation(header):
    window, item, app = header
    item.setProperty("songCount", 2)
    for name, counter in [("songsPlayButton", "playRequests"),
                          ("songsShuffleButton", "shuffleRequests"),
                          ("songsMoreButton", "optionsRequests")]:
        button = item.findChild(QQuickItem, name)
        button.forceActiveFocus(Qt.TabFocusReason)
        QTest.keyClick(window, Qt.Key_Space)
        app.processEvents()
        assert window.property(counter) == 1


def test_narrow_header_stacks_actions_without_overlap(header):
    window, item, app = header
    actions = item.findChild(QQuickItem, "songsActions")
    summary = item.findChild(QQuickItem, "songsSummary")
    assert not item.property("stacked")
    window.setWidth(360)
    app.processEvents()
    assert item.property("stacked")
    assert actions.y() > summary.mapToItem(item, 0, summary.height()).y()
    assert actions.x() + actions.width() <= item.width() - item.property("sideMargin")
    assert actions.y() + actions.height() < item.property("implicitHeight")
    window.setWidth(900)
    app.processEvents()
    assert not item.property("stacked")


def test_more_menu_changes_sort_and_preserves_checked_selection(application_gui):
    from PySide6.QtCore import QObject, QPointF

    window, app = application_gui
    window.findChild(QQuickItem, "navRail").libraryNavigated.emit(0)
    QTest.qWait(250)
    more = window.findChild(QQuickItem, "songsMoreButton")
    more.forceActiveFocus(Qt.TabFocusReason)
    QTest.keyClick(window, Qt.Key_Space)
    QTest.qWait(150)
    menu = window.findChild(QObject, "songsSortMenu")
    assert menu.property("visible")
    query = window.findChild(QObject, "libraryTracks")
    original_sort = query.property("sortField")
    original_ascending = query.property("sortAscending")

    def choose(name):
        row = window.findChild(QQuickItem, name)
        # Open through the actual button, including after an earlier choice.
        if not menu.property("visible"):
            more.forceActiveFocus(Qt.TabFocusReason)
            QTest.keyClick(window, Qt.Key_Space)
            QTest.qWait(150)
        point = row.mapToScene(QPointF(row.width() / 2, row.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(150)
        assert row.property("checked")

    try:
        for key in ("album", "duration", "artist", "stashedDate", "title", "title"):
            choose("songsSort_" + key)
            assert query.property("sortField") == key
        for name, ascending in [("songsSortDescending", False),
                                ("songsSortAscending", True),
                                ("songsSortAscending", True)]:
            choose(name)
            assert query.property("sortAscending") == ascending
    finally:
        menu.close()
        window.findChild(QObject, "songsSort_" + original_sort).triggered.emit()
        direction = "songsSortAscending" if original_ascending else "songsSortDescending"
        window.findChild(QObject, direction).triggered.emit()
