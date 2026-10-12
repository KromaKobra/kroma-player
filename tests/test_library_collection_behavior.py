"""Library input gestures through real QML, including controls nested inside entries."""
import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest
from app.models.track import Track

from test_tile_size import _call, _load, backend  # noqa: F401
from test_library_display_options import item, navigate, seed_playlists
from test_column_sort import descendant


def view_for(window, key):
    if key == "song":
        table = item(window, "libraryTable")
        return item(table, "songGridView" if table.property("gridMode") else "trackList")
    return item(window, key + "CollectionView")


def cell_at(window, key, index):
    view = view_for(window, key)
    view.setProperty("currentIndex", index)
    QTest.qWait(30)
    cell = view.property("currentItem")
    assert cell is not None
    return cell


def click_entry(window, cell, modifiers=Qt.NoModifier, double=False):
    point = cell.mapToScene(QPointF(cell.width() * 0.55, cell.height() * 0.5)).toPoint()
    if double:
        QTest.mouseDClick(window, Qt.LeftButton, modifiers, point)
    else:
        QTest.mouseClick(window, Qt.LeftButton, modifiers, point)
    QTest.qWait(30)


def model_for(window, backend, key):
    return window.findChild(QObject, "libraryTracks") if key == "song" else backend.get_service(key + "s_view_model")


@pytest.mark.parametrize("key", ["song", "artist", "album", "playlist"])
@pytest.mark.parametrize("grid", [False, True])
def test_repeat_click_and_empty_space_clear_selection_and_its_tint(qt_app, backend, key, grid):
    seed_playlists(backend)
    backend.get_service("settings_view_model").setValue(key + "Grid", grid)
    engine, window, warnings = _load(qt_app, backend)
    window.setWidth(1400)
    window.setHeight(1400)
    navigate(window, key)
    model = model_for(window, backend, key)
    view = view_for(window, key)
    first, second = cell_at(window, key, 0), cell_at(window, key, 1)
    assert view.height() > view.property("contentHeight")
    blank = view.mapToScene(QPointF(view.width() / 2, view.height() - 10)).toPoint()
    QTest.mouseMove(window, blank)
    first.setProperty("focus", False)
    QTest.qWait(250)
    background = item(first, "entrySelectionBackground")
    resting_color = background.property("color")

    click_entry(window, first)
    assert model.selectedUids() == [model.uidAt(0)]
    QTest.qWait(300)  # A second single click, outside the double-click interval.
    click_entry(window, first)
    QTest.mouseMove(window, blank)
    QTest.qWait(500)
    assert model.selectedUids() == []
    assert first.property("selected") is False
    assert background.property("color") == resting_color

    click_entry(window, first)
    click_entry(window, second, Qt.ControlModifier)
    assert len(model.selectedUids()) == 2
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, blank)
    QTest.qWait(250)
    assert model.selectedUids() == []
    assert first.property("selected") is False
    assert second.property("selected") is False
    assert background.property("color") == resting_color

    # Page headings and side margins clear too. Songs' list spans the page;
    # its margin is inside the viewport, beside the inset row cards.
    for point in (
        item(window, "songsTitle" if key == "song" else key + "Title").mapToScene(QPointF(1, 1)).toPoint(),
        view.mapToScene(QPointF(5 if key == "song" and not grid else -5,
                               view.height() - 10)).toPoint(),
    ):
        click_entry(window, first)
        assert model.selectedUids() == [model.uidAt(0)]
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(280)
        assert model.selectedUids() == []
    assert not warnings, [warning.toString() for warning in warnings]
    window.close()
    del engine


@pytest.mark.parametrize("key", ["song", "artist", "album", "playlist"])
@pytest.mark.parametrize("grid", [False, True])
def test_click_selects_modifiers_extend_and_double_click_opens(qt_app, backend, key, grid):
    seed_playlists(backend)
    backend.get_service("settings_view_model").setValue(key + "Grid", grid)
    engine, window, warnings = _load(qt_app, backend)
    navigate(window, key)
    model = model_for(window, backend, key)
    first, second = cell_at(window, key, 0), cell_at(window, key, 1)
    click_entry(window, first)
    assert model.selectedUids() == [model.uidAt(0)]
    assert window.property("viewIndex") == (3 if key == "playlist" else 4)
    click_entry(window, second, Qt.ControlModifier)
    assert set(model.selectedUids()) == {model.uidAt(0), model.uidAt(1)}
    click_entry(window, cell_at(window, key, 2), Qt.ShiftModifier)
    assert set(model.selectedUids()) == {model.uidAt(1), model.uidAt(2)}
    click_entry(window, second)
    click_entry(window, second)
    QTest.qWait(280)
    assert model.selectedUids() == []
    click_entry(window, second, double=True)
    QTest.qWait(200)
    assert window.property("viewIndex") == {"song": 7, "artist": 9, "album": 11, "playlist": 3}[key]
    if key == "playlist":
        assert item(window, "playlistsView").property("openId") == model.uidAt(1)
    assert not warnings, [warning.toString() for warning in warnings]
    window.close()
    del engine


@pytest.mark.parametrize("key", ["artist", "album", "playlist"])
@pytest.mark.parametrize("grid", [False, True])
def test_play_preserves_queue_and_context_snapshots_selection(qt_app, backend, key, grid):
    seed_playlists(backend)
    model = backend.get_service(key + "s_view_model")
    if key == "playlist":
        track_ids = backend.get_service("db_session").query(Track).all()
        for row in model._rows:
            model.addTracksToPlaylist(row["id"], [track.id for track in track_ids[:3]])
    settings = backend.get_service("settings_view_model")
    settings.setValue(key + "Grid", grid)
    queue = backend.get_service("queue_view_model")
    existing = backend.get_service("db_session").query(Track).all()[-1].id
    queue.append_track_ids([existing])
    engine, window, warnings = _load(qt_app, backend)
    navigate(window, key)
    cell = cell_at(window, key, 0)
    uid = model.uidAt(0)
    click_entry(window, cell)
    cell.forceActiveFocus(Qt.TabFocusReason)
    play = item(cell, "collectionPlayButton")
    point = play.mapToScene(QPointF(play.width()/2, play.height()/2)).toPoint()
    QTest.mouseDClick(window, Qt.LeftButton, Qt.NoModifier, point)
    QTest.qWait(80)
    assert queue.trackIds()[-1] == existing
    assert backend.get_service("player_view_model").currentSourceType == key
    assert backend.get_service("player_view_model").currentSourceId == uid
    assert cell.property("playing") is True
    assert model.selectedUids() == [uid]
    assert window.property("viewIndex") == (3 if key == "playlist" else 4)
    if key == "playlist": assert item(window, "playlistsView").property("openId") == -1
    click_entry(window, cell_at(window, key, 1), Qt.ControlModifier)
    controller = cell.property("controller")
    _call(controller, "openContext", uid)
    menu = window.findChild(QObject, key + "CollectionMenu")
    assert menu.property("selectionCount") == 2
    entries = menu.property("entries")
    model.clearSelection()
    assert menu.property("entries") == entries
    menu.close()
    assert not warnings, [warning.toString() for warning in warnings]
    window.close()
    del engine


@pytest.mark.parametrize("key", ["artist", "album", "playlist"])
def test_collection_column_controls_persist_and_restore_after_narrowing(qt_app, backend, key):
    seed_playlists(backend)
    settings = backend.get_service("settings_view_model")
    settings.setValue(key + "Grid", False)
    engine, window, warnings = _load(qt_app, backend)
    navigate(window, key)
    cell = cell_at(window, key, 0)
    controller = cell.property("controller")
    layout = controller.property("columnLayout")
    header = descendant(controller, "columnHeader_totalDuration")
    point = header.mapToScene(QPointF(header.width()/2, header.height()/2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    assert backend.get_service(key + "s_view_model").sortField == "totalDuration"
    _call(layout, "beginResize")
    _call(layout, "resizeTo", "totalDuration", 120)
    _call(layout, "endResize")
    _call(layout, "moveColumn", "totalDuration", 0)
    _call(layout, "toggle", "trackCount")
    saved = settings.value("columns." + key + "Collection", "")
    _call(layout, "setBudget", 170, 14)
    _call(layout, "setBudget", 650, 14)
    assert settings.value("columns." + key + "Collection", "") == saved
    window.close()
    del engine
    engine, window, warnings2 = _load(qt_app, backend)
    navigate(window, key)
    restored = cell_at(window, key, 0).property("controller").property("columnLayout")
    order = restored.property("order").toVariant()
    assert order[0] == "totalDuration" and "trackCount" not in order
    assert not warnings + warnings2, [warning.toString() for warning in warnings + warnings2]
    window.close()
    del engine


def test_playback_source_follows_entries_and_survives_queue_edits(qt_app, backend):
    player = backend.get_service("player_view_model")
    queue = backend.get_service("queue_view_model")
    ids = [row.id for row in backend.get_service("db_session").query(Track).all()][:3]
    player.playEntries([{"id": ids[0], "sourceType": "album", "sourceId": 42},
                        {"id": ids[1], "sourceType": "artist", "sourceId": 17}])
    assert (player.currentSourceType, player.currentSourceId) == ("album", 42)
    queue.removeAt(0)
    assert (player.currentSourceType, player.currentSourceId) == ("album", 42)
    player.play_queue_rows([0])
    assert (player.currentSourceType, player.currentSourceId) == ("artist", 17)
    player.pause()
    assert player.currentSourceId == 17
    queue.insertNextEntries([{"id": ids[2], "sourceType": "playlist", "sourceId": 8}])
    player.next()
    assert (player.currentSourceType, player.currentSourceId) == ("playlist", 8)
    player.previous()
    assert (player.currentSourceType, player.currentSourceId) == ("artist", 17)
    player.play_from_ids([ids[0]])
    assert (player.currentSourceType, player.currentSourceId) == ("", -1)
    player.stop_player()
    assert player.currentSourceType == ""


def test_song_list_keyboard_selects_and_opens_editor(qt_app, backend):
    engine, window, warnings = _load(qt_app, backend)
    navigate(window, "song")
    cell = cell_at(window, "song", 0)
    cell.forceActiveFocus(Qt.TabFocusReason)
    QTest.keyClick(window, Qt.Key_Space)
    model = model_for(window, backend, "song")
    assert model.selectedUids() == [model.uidAt(0)]
    QTest.keyClick(window, Qt.Key_Return)
    assert window.property("viewIndex") == 7
    assert not warnings, [warning.toString() for warning in warnings]
    window.close()
    del engine
