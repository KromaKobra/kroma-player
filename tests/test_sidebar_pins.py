"""Durable shortcuts, bounded metadata reads, and real QML menu interactions."""
from pathlib import Path

import pytest
from PySide6.QtCore import Q_ARG, QMetaObject, QObject, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from sqlalchemy import delete, event, insert, select, text
from sqlalchemy.exc import IntegrityError

from app.database.connection import DatabaseManager
from app.database.schema import create_schema
from app.events.event_bus import EventBus
from app.models.playlist import Playlist, PlaylistTrack
from app.models.sidebar_pin import SidebarPin
from app.models.track import Album, Artist, Track
from app.repositories.sidebar_pin_repository import SidebarPinRepository
from app.viewmodels.pins_view_model import PinsViewModel
from test_sidebar import _visual_item
from test_album_search_rows import backend, _load  # noqa: F401

GUI = Path(__file__).resolve().parents[1] / "gui" / "nocturne"


@pytest.fixture
def pins(tmp_path):
    manager = DatabaseManager.from_path(tmp_path / "tracks.db")
    session = manager.create_session()
    artist = Artist(name="Ada")
    album = Album(name="Evening")
    track = Track(title="Still", duration_seconds=180, artists=[artist], albums=[album])
    playlist = Playlist(name="Night drive")
    session.add_all([track, playlist])
    session.flush()
    session.add(PlaylistTrack(playlist_id=playlist.id, track_id=track.id, position=0))
    session.commit()
    rows = {"track": track, "album": album, "artist": artist, "playlist": playlist}
    bus = EventBus()
    repo = SidebarPinRepository(session)
    vm = PinsViewModel(repo, bus)
    yield vm, repo, rows, bus, manager
    session.close()
    manager.engine.dispose()


def test_order_deduplication_and_restart(pins):
    vm, repo, rows, _, manager = pins
    for kind in ("album", "track", "playlist", "artist"):
        assert vm.pin(kind, rows[kind].id)
        assert not vm.pin(kind, rows[kind].id)
    assert [r["contentType"] for r in vm.items] == ["album", "track", "playlist", "artist"]
    assert vm.items[1]["subtitle"] == "Ada"
    assert vm.items[0]["subtitle"] == "Album · 1 track"
    assert vm.unpin("track", rows["track"].id)
    assert vm.pin("track", rows["track"].id)
    with manager.create_session() as session:
        restored = PinsViewModel(SidebarPinRepository(session), EventBus())
        assert restored.items == vm.items
        assert restored.items[-1]["contentType"] == "track"


@pytest.mark.parametrize("kind", ["track", "album", "artist", "playlist"])
def test_bulk_deletion_cascades_without_reusing_a_pin(pins, kind):
    vm, repo, rows, _, _ = pins
    row = rows[kind]
    uid = row.id
    assert vm.pin(kind, uid)
    if kind == "track":
        row.artists.clear()
        row.albums.clear()
    elif kind in ("album", "artist"):
        row.tracks.clear()
    repo.session.flush()
    # Exercise SQL deletion too: no service callback is needed for integrity.
    repo.session.execute(delete(type(row)).where(type(row).id == uid))
    repo.session.commit()
    assert repo.session.execute(select(SidebarPin.id)).all() == []
    replacement = type(row)(id=uid, **({"title": "Replacement", "duration_seconds": 180} if kind == "track"
                                     else {"name": "Replacement"}))
    repo.session.add(replacement)
    repo.session.commit()
    vm.refresh()
    assert not vm.items
    assert not vm.isPinned(kind, uid)


@pytest.mark.parametrize("values", [
    {"position": 0}, {"position": 0, "track_id": 1, "album_id": 1},
    {"position": -1, "track_id": 1}, {"position": 0, "track_id": 99999},
])
def test_database_rejects_invalid_pins(pins, values):
    _, repo, _, _, _ = pins
    with pytest.raises(IntegrityError):
        repo.session.execute(insert(SidebarPin).values(**values))
        repo.session.commit()
    repo.session.rollback()


def test_existing_vault_gains_table_idempotently(pins):
    _, _, _, _, manager = pins
    SidebarPin.__table__.drop(manager.engine)
    create_schema(manager.engine)
    create_schema(manager.engine)
    with manager.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM sidebar_pins")).scalar() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM tracks")).scalar() == 1


def test_live_metadata_coalesces_and_menu_checks_never_query(pins, qt_app):
    vm, repo, rows, bus, manager = pins
    assert vm.pin("track", rows["track"].id)
    assert vm.pin("artist", rows["artist"].id)
    statements = []
    def record(*args): statements.append(args[2])
    rows["track"].title = "Renamed"
    rows["track"].cover_hash = "a" * 32
    rows["artist"].name = "Grace"
    repo.session.commit()
    event.listen(manager.engine, "before_cursor_execute", record)
    try:
        for _ in range(30):
            assert vm.isPinned("track", rows["track"].id)
            bus.emit("track.updated", rows["track"])
        assert not statements
        qt_app.processEvents()
        assert len(statements) == 3  # pins, ordered track credits, artist counts
    finally:
        event.remove(manager.engine, "before_cursor_execute", record)
    assert vm.items[0]["title"] == "Renamed"
    assert vm.items[0]["subtitle"] == "Grace"
    assert vm.items[0]["coverHash"] == "a" * 32
    assert vm.items[1]["title"] == "Grace"


def test_summary_query_count_does_not_grow_with_pins(pins):
    vm, repo, _, _, manager = pins
    repo.session.add_all([Track(title=f"Track {i}", duration_seconds=180) for i in range(30)])
    repo.session.commit()
    for uid in repo.session.scalars(select(Track.id)):
        repo.pin("track", uid)
    statements = []
    def record(*args): statements.append(args[2])
    event.listen(manager.engine, "before_cursor_execute", record)
    try:
        vm.refresh()
    finally:
        event.remove(manager.engine, "before_cursor_execute", record)
    assert len(vm.items) == 31
    assert len(statements) == 2


def test_invalid_and_missing_targets_do_not_create_shortcuts(pins):
    vm, _, _, _, _ = pins
    for kind, uid in (("other", 1), ("track", -1), ("track", 99999)):
        assert not vm.pin(kind, uid)
    assert not vm.items


@pytest.fixture
def pin_ui(pins, qt_app):
    vm, _, _, _, _ = pins
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(GUI))
    engine.rootContext().setContextProperty("pinsVM", vm)
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
    engine.loadData(b'''
import QtQuick
import QtQuick.Controls
import "components/shell"
import "components/controls"
Window {
    width: 600; height: 780; visible: true
    property string activatedType: ""
    property int activatedId: -1
    NavRail {
        objectName: "rail"; height: parent.height
        reducedMotion: true
        pinModel: pinsVM
        onPinActivated: function(kind, id) { activatedType = kind; activatedId = id }
    }
    PinMenuRow {
        objectName: "sourcePinAction"; x: 300; y: 50
        contentType: "track"; contentId: 1
    }
}
''', QUrl.fromLocalFile(str(GUI / "pin-test.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    qt_app.processEvents()
    yield window, window.findChild(QQuickItem, "rail"), warnings
    assert not warnings, warnings
    window.close()
    del engine


def test_pin_action_heading_collapsed_and_unpin_menu(pins, pin_ui, qt_app):
    vm, _, rows, _, _ = pins
    window, rail, _ = pin_ui
    heading = _visual_item(rail, "pinnedHeading")
    action = window.findChild(QQuickItem, "sourcePinAction")
    assert not heading.isVisible()
    assert action.isVisible()
    QMetaObject.invokeMethod(action, "triggered", Qt.DirectConnection)
    qt_app.processEvents()
    assert vm.isPinned("track", rows["track"].id)
    assert heading.isVisible()
    assert not action.isVisible()
    pin = _visual_item(rail, "pinned_track_1")
    point = pin.mapToScene(QPointF(pin.width() / 2, pin.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    assert window.property("activatedType") == "track"
    assert window.property("activatedId") == 1
    rail.setProperty("collapsed", True)
    assert rail.width() == 64
    pin.forceActiveFocus()
    QTest.keyClick(window, Qt.Key_Menu)
    qt_app.processEvents()
    menu = rail.findChild(QObject, "sidebarPinMenu")
    assert menu.property("visible")
    unpin = window.findChild(QQuickItem, "unpinFromSidebar")
    assert unpin is not None
    QMetaObject.invokeMethod(unpin, "triggered", Qt.DirectConnection)
    qt_app.processEvents()
    assert not vm.items
    assert not heading.isVisible()
    assert action.isVisible()


def test_sidebar_right_click_and_overflow_remain_reachable(pins, pin_ui, qt_app):
    vm, repo, rows, _, _ = pins
    window, rail, _ = pin_ui
    for kind, row in rows.items():
        vm.pin(kind, row.id)
    qt_app.processEvents()
    pin = _visual_item(rail, "pinned_track_1")
    point = pin.mapToScene(QPointF(pin.width() / 2, pin.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.RightButton, Qt.NoModifier, point)
    qt_app.processEvents()
    menu = rail.findChild(QObject, "sidebarPinMenu")
    assert menu.property("visible")
    assert menu.property("contentType") == "track"
    QMetaObject.invokeMethod(menu, "close", Qt.DirectConnection)
    QTest.qWait(180)
    assert window.property("activatedId") == -1
    for i in range(20):
        track = Track(title=f"Extra {i}", duration_seconds=60)
        repo.session.add(track)
        repo.session.commit()
        repo.pin("track", track.id)
    vm.refresh()
    qt_app.processEvents()
    last = vm.items[-1]
    pin = _visual_item(rail, f"pinned_track_{last['contentId']}")
    pin.forceActiveFocus()
    qt_app.processEvents()
    point = pin.mapToScene(QPointF(pin.width() / 2, pin.height() / 2))
    assert 0 < point.y() < window.height() - 57
    QTest.keyClick(window, Qt.Key_Space)
    assert window.property("activatedId") == last["contentId"]


def test_collection_and_search_menus_pin_the_correct_content(qt_app, backend):
    from test_tab_navigation import _call

    service = backend.get_service("playlist_service")
    playlist = service.create_playlist("Late night")
    session = backend.get_service("db_session")
    targets = {"artist": session.scalar(select(Artist.id)),
               "album": session.scalar(select(Album.id)), "playlist": playlist}
    for name in ("artists_view_model", "albums_view_model"):
        backend.get_service(name).refresh()
    engine, window, warnings = _load(qt_app, backend)
    vm = backend.get_service("pins_view_model")
    for kind, uid in targets.items():
        if kind == "playlist":
            _call(window, qt_app, "go", 3)
        else:
            _call(window, qt_app, "goLibrary", 1 if kind == "artist" else 2)
        collection = next(item for item in window.findChildren(QQuickItem)
                          if item.property("viewKey") == kind
                          and item.metaObject().indexOfMethod("openContext(QVariant)") >= 0)
        assert QMetaObject.invokeMethod(collection, "openContext", Qt.DirectConnection,
                                        Q_ARG("QVariant", uid))
        qt_app.processEvents()
        menu = window.findChild(QObject, kind + "CollectionMenu")
        action = menu.findChild(QQuickItem, "pinToSidebar")
        assert action.isVisible()
        QMetaObject.invokeMethod(action, "triggered", Qt.DirectConnection)
        qt_app.processEvents()
        assert vm.isPinned(kind, uid)
        assert not action.isVisible()
        QMetaObject.invokeMethod(menu, "close", Qt.DirectConnection)

    # Artists and albums in search share TrackTable's context menu. Its pin
    # must refer to the collection ID, even though the row has no track ID.
    for kind in ("artist", "album"):
        uid = targets[kind]
        vm.unpin(kind, uid)
        search = backend.get_service("search_view_model")
        search.searchMode = "artists" if kind == "artist" else "albums"
        search.text_search("Seven" if kind == "artist" else "Horizon")
        _call(window, qt_app, "go", 2)
        table = next(item for item in window.findChildren(QQuickItem)
                     if item.property("listVM") == search
                     and item.metaObject().indexOfMethod(
                         "openTrackMenu(QVariant,QVariant,QVariant,QVariant,QVariant,QVariant)") >= 0)
        assert QMetaObject.invokeMethod(table, "openTrackMenu", Qt.DirectConnection,
            *[Q_ARG("QVariant", value) for value in (search.uidAt(0), -1, 0, "", kind, uid)])
        menu = table.findChild(QObject, "trackContextMenu")
        action = menu.findChild(QQuickItem, "pinToSidebar")
        assert action.isVisible()
        QMetaObject.invokeMethod(action, "triggered", Qt.DirectConnection)
        assert vm.isPinned(kind, uid)
        QMetaObject.invokeMethod(menu, "close", Qt.DirectConnection)
    assert not warnings, [w.toString() for w in warnings]
    window.close()
    del engine
