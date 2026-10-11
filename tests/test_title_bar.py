"""Exercise chrome hit testing and route restoration with real QML controls."""
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QPointF, QUrl, Qt
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from test_album_search_rows import backend, _load
from test_hidden_tabs import _call

GUI = Path(__file__).resolve().parents[1] / "gui" / "nocturne"


def item(window, name):
    found = window.findChild(QQuickItem, name)
    assert found is not None, name
    return found


def click(window, control):
    point = control.mapToScene(QPointF(control.width() / 2, control.height() / 2))
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point.toPoint())
    QTest.qWait(20)


@pytest.fixture
def chrome(qt_app):
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(GUI))
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
    engine.loadData(b'''
import QtQuick
import QtQuick.Window
import "components/shell"
Window {
    id: window
    width: 940; height: 400; visible: true
    property int backCount: 0
    QtObject {
        id: nativeWindow
        objectName: "nativeWindow"
        property int visibility: Window.Windowed
        property int moves: 0
        property int closes: 0
        function startSystemMove() { moves++ }
        function showNormal() { visibility = Window.Windowed }
        function showMaximized() { visibility = Window.Maximized }
        function showMinimized() { visibility = Window.Minimized }
        function close() { closes++ }
    }
    TitleBar {
        objectName: "titleBar"
        width: parent.width
        targetWindow: nativeWindow
        canGoBack: true
        onBackRequested: window.backCount++
    }
}
''', QUrl.fromLocalFile(str(GUI / "title-bar-test.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    qt_app.processEvents()
    yield window, qt_app
    assert not warnings, warnings
    window.close()
    del engine


def test_controls_and_search_do_not_start_window_drag(chrome):
    window, app = chrome
    native = window.findChild(QObject, "nativeWindow")
    click(window, item(window, "navigationBackButton"))
    assert window.property("backCount") == 1
    click(window, item(window, "globalSearchField"))
    QTest.keyClick(window, Qt.Key_A)
    assert item(window, "globalSearchField").property("text") == "a"
    assert native.property("moves") == 0
    assert not item(window, "navigationForwardButton").isEnabled()


def test_sidebar_header_is_draggable_and_double_click_maximizes(chrome):
    window, app = chrome
    native = window.findChild(QObject, "nativeWindow")
    # Blank space in the sidebar's header, away from the wordmark.
    point = QPointF(200, 32).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    assert native.property("moves") == 1
    QTest.mouseDClick(window, Qt.LeftButton, Qt.NoModifier, point)
    app.processEvents()
    assert native.property("visibility") == 4  # Window.Maximized
    maximize = item(window, "windowMaximizeButton")
    assert maximize.property("text") == "Restore"
    click(window, maximize)
    assert native.property("visibility") == 2  # Window.Windowed


def test_window_controls_support_keyboard_and_do_not_drag(chrome):
    window, app = chrome
    native = window.findChild(QObject, "nativeWindow")
    for name in ("windowMinimizeButton", "windowMaximizeButton", "windowCloseButton"):
        item(window, name).forceActiveFocus(Qt.TabFocusReason)
        QTest.keyClick(window, Qt.Key_Space)
    assert native.property("closes") == 1
    assert native.property("moves") == 0


def test_toolbar_fits_at_minimum_window_width_and_expanded_sidebar(chrome):
    window, app = chrome
    bar = item(window, "titleBar")
    bar.setProperty("sidebarWidth", 360)
    app.processEvents()
    search = item(window, "globalSearchField")
    minimize = item(window, "windowMinimizeButton")
    assert bar.height() == 64
    assert search.width() >= 180
    assert search.x() + search.width() + 60 <= minimize.mapToItem(bar, 0, 0).x()


@pytest.fixture
def application(qt_app, backend):
    from app.models.track import Artist, Track

    session = backend.get_service("db_session")
    second = Artist(name="Second artist")
    session.add(second)
    session.add(Track(title="Second track", duration_seconds=60, artists=[second]))
    session.commit()
    engine, window, warnings = _load(qt_app, backend)
    yield window, qt_app, backend, warnings
    window.close()
    del engine


def test_history_restores_specific_profiles_and_library_sections(application):
    window, app, registry, warnings = application
    artist = registry.get_service("artist_edit_view_model")
    _call(window, app, "goLibrary", 2)
    _call(window, app, "openArtist", 1)
    _call(window, app, "openArtist", 2)
    history = window.findChild(QObject, "navigationHistory")
    cursor = history.property("cursor")
    _call(window, app, "visitHistory", cursor - 1)
    assert artist.artistId == 1
    _call(window, app, "visitHistory", cursor)
    assert artist.artistId == 2
    _call(window, app, "visitHistory", cursor - 2)
    assert window.property("viewIndex") == 4
    assert item(window, "navRail").property("libraryMode") == 2
    assert not warnings, [w.toString() for w in warnings]


def test_history_branches_and_stays_bounded(application):
    window, app, registry, warnings = application
    history = window.findChild(QObject, "navigationHistory")
    history.setProperty("limit", 6)
    for index in [1, 4, 5, 0, 1, 4, 5, 0]:
        _call(window, app, "go", index)
    assert len(history.property("entries").toVariant()) == 6
    _call(window, app, "visitHistory", 3)
    _call(window, app, "go", 13)
    assert history.property("cursor") == 4
    assert not history.property("canGoForward")


def test_history_does_not_replace_unsaved_entity_edits(application):
    window, app, registry, warnings = application
    artist = registry.get_service("artist_edit_view_model")
    _call(window, app, "openArtist", 1)
    _call(window, app, "openArtist", 2)
    artist.name = "Unsaved artist"
    history = window.findChild(QObject, "navigationHistory")
    cursor = history.property("cursor")
    _call(window, app, "visitHistory", cursor - 1)
    dialog = window.findChild(QObject, "replaceEditorDialog")
    assert dialog.property("visible")
    assert history.property("cursor") == cursor
    assert artist.artistId == 2 and artist.isDirty
    dialog.close()
    assert artist.name == "Unsaved artist"
    _call(window, app, "visitHistory", cursor - 1)
    dialog.confirmed.emit()
    dialog.close()
    assert artist.artistId == 1 and not artist.isDirty


def test_global_search_restores_text_and_search_keyboard_focus(application):
    window, app, registry, warnings = application
    _call(window, app, "go", 4)
    QTest.keyClick(window, Qt.Key_F, Qt.ControlModifier)
    app.processEvents()
    search = item(window, "globalSearchField")
    assert search.property("inputFocus")
    for key in (Qt.Key_S, Qt.Key_E, Qt.Key_V):
        QTest.keyClick(window, key)
    assert search.property("text") == "sev"
    search_vm = registry.get_service("search_view_model")
    # Wait for the observable result, rather than racing a 260 ms debounce
    # against a fixed sleep on a loaded CI host.
    for _ in range(100):
        if search_vm.rowCount() > 0:
            break
        QTest.qWait(20)
    assert window.property("viewIndex") == 2
    assert search_vm.rowCount() > 0, (search.property("text"), search_vm.searchMode)
    history = window.findChild(QObject, "navigationHistory")
    cursor = history.property("cursor")
    _call(window, app, "go", 4)
    item(window, "titleBar").searchAccepted.emit("seven")
    _call(window, app, "visitHistory", cursor)
    assert search.property("text") == "sev"
    _call(window, app, "go", 4)
    QTest.keyClick(window, Qt.Key_F, Qt.ControlModifier)
    QTest.keyClick(window, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClick(window, Qt.Key_S)
    QTest.qWait(300)
    assert search.property("text") == "s"
    assert not warnings, [w.toString() for w in warnings]


def test_sidebar_materials_reach_header_and_follow_panel_width(application):
    window, app, registry, warnings = application
    window.setProperty("infoOpen", True)
    QTest.qWait(650)
    for surface_name, panel_name in [("navigationSurface", "navRail"),
                                     ("detailsSurface", "infoPanel")]:
        surface, panel = item(window, surface_name), item(window, panel_name)
        assert surface.y() == 0
        assert panel.y() == 64
        assert surface.width() == panel.width()


def test_history_restores_track_editors_and_albums(application):
    window, app, registry, warnings = application
    _call(window, app, "editTrack", 1)
    _call(window, app, "editTrack", 2)
    history = window.findChild(QObject, "navigationHistory")
    track_cursor = history.property("cursor")
    _call(window, app, "visitHistory", track_cursor - 1)
    assert registry.get_service("track_edit_view_model").trackId == 1
    _call(window, app, "visitHistory", track_cursor)
    _call(window, app, "openAlbum", 1)
    _call(window, app, "openAlbum", 2)
    _call(window, app, "visitHistory", history.property("cursor") - 1)
    assert registry.get_service("album_edit_view_model").albumId == 1
    assert registry.get_service("album_tracks_view_model").albumId == 1


def test_removed_history_item_keeps_current_route_and_profile(application):
    from app.models.track import Artist

    window, app, registry, warnings = application
    _call(window, app, "openArtist", 2)
    _call(window, app, "openArtist", 1)
    history = window.findChild(QObject, "navigationHistory")
    cursor = history.property("cursor")
    session = registry.get_service("db_session")
    removed = session.get(Artist, 2)
    session.delete(removed)
    session.commit()
    _call(window, app, "visitHistory", cursor - 1)
    assert history.property("cursor") == cursor
    assert window.property("viewIndex") == 9
    assert registry.get_service("artist_edit_view_model").artistId == 1


def test_history_popup_supports_keyboard_destination_selection(application):
    window, app, registry, warnings = application
    _call(window, app, "go", 4)
    _call(window, app, "go", 5)
    click(window, item(window, "navigationHistoryButton"))
    popup = window.findChild(QObject, "navigationHistoryPopup")
    assert popup.property("visible")
    QTest.keyClick(window, Qt.Key_Down)
    QTest.keyClick(window, Qt.Key_Return)
    app.processEvents()
    assert window.property("viewIndex") == 4
    assert not popup.property("visible")
    assert not warnings, [w.toString() for w in warnings]


def test_online_search_waits_for_enter_and_history_does_not_request_network(application, monkeypatch):
    from tests.fakes.providers import FakeRegistry, FakeTrackSource

    window, app, registry, warnings = application
    search = registry.get_service("search_view_model")
    source = FakeTrackSource()
    search._registry = FakeRegistry(sources=[source])
    search._on_providers_changed()
    search.setSource(source.key)
    requests = []
    monkeypatch.setattr(search, "_start_web_search", lambda key, query: requests.append(query))
    bar = item(window, "titleBar")
    bar.searchEdited.emit("first")
    QTest.qWait(300)
    assert requests == []
    bar.searchAccepted.emit("first")
    assert requests == ["first"]
    history = window.findChild(QObject, "navigationHistory")
    first = history.property("cursor")
    _call(window, app, "go", 4)
    bar.searchAccepted.emit("second")
    assert requests == ["first", "second"]
    _call(window, app, "visitHistory", first)
    assert item(window, "globalSearchField").property("text") == "first"
    assert requests == ["first", "second"]
    QTest.keyClick(window, Qt.Key_F, Qt.ControlModifier)
    QTest.keyClick(window, Qt.Key_Return)
    assert requests == ["first", "second", "first"]
