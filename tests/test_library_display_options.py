"""Library presentation through real menus, delegates, models and persistence."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from test_tile_size import _call, _load, backend  # noqa: F401


COLLECTIONS = [
    ("song", "songsViewOptionsMenu", "songGridView", False),
    ("artist", "artistViewOptionsMenu", "artistCollectionView", True),
    ("album", "albumViewOptionsMenu", "albumCollectionView", True),
    ("playlist", "playlistViewOptionsMenu", "playlistCollectionView", True),
]


def item(root, name):
    result = root.findChild(QQuickItem, name)
    assert result is not None, name
    return result


def current_cell(window, key):
    # Delegates belong to the view's visual tree, not its QObject child tree.
    view = item(window, "songGridView" if key == "song" else key + "CollectionView")
    view.setProperty("currentIndex", 0)
    for _ in range(50):
        cell = view.property("currentItem")
        if cell is not None:
            return cell
        QTest.qWait(20)
    pytest.fail(f"{key} did not instantiate its current delegate")


def click(window, target):
    point = target.mapToScene(QPointF(target.width() / 2, target.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    QTest.qWait(160)


def navigate(window, key):
    if key == "playlist":
        _call(window, "go", 3)
    else:
        window.findChild(QQuickItem, "navRail").libraryNavigated.emit(
            {"song": 0, "artist": 1, "album": 2}[key]
        )
    QTest.qWait(240)


def open_menu(window, key):
    name = {"song": "songsMoreButton", "playlist": "playlistViewOptionsButton"}.get(
        key, "libraryCollectionOptionsButton"
    )
    button = item(window, name)
    button.forceActiveFocus(Qt.TabFocusReason)
    QTest.keyClick(window, Qt.Key_Space)
    QTest.qWait(160)


def seed_playlists(backend):
    vm = backend.get_service("playlists_view_model")
    for name in ("Evening", "Morning", "Travel"):
        vm.createPlaylist(name)


@pytest.mark.parametrize("key", ["artist", "album", "playlist"])
def test_collections_sort_from_their_own_button_in_both_layouts(qt_app, backend, key):
    seed_playlists(backend)
    engine, window, warnings = _load(qt_app, backend)
    navigate(window, key)
    button = item(window, key + "SortButton")
    menu = window.findChild(QObject, key + "SortMenu")
    options = window.findChild(QObject, key + "ViewOptionsMenu")
    model = backend.get_service(key + "s_view_model")
    settings = backend.get_service("settings_view_model")

    for grid_mode in (True, False):
        if options.property("gridMode") is not grid_mode:
            open_menu(window, key)
            click(window, item(window, key + "LayoutOption"))
        assert button.isVisible()
        fields = ["name", "trackCount", "dateAdded"]
        if key == "playlist":
            fields.append("recentlyModified")
        for field in fields:
            if field == "recentlyModified":
                click(window, button)
                click(window, item(window, key + "SortAscending"))
                assert model.sortAscending
            button.forceActiveFocus(Qt.TabFocusReason)
            QTest.keyClick(window, Qt.Key_Space)
            QTest.qWait(160)
            assert menu.property("visible")
            assert not options.property("visible")
            click(window, item(window, key + "Sort_" + field))
            assert model.sortField == field
            assert settings.value(key + "Sort", None) == field
            if field == "recentlyModified":
                assert not model.sortAscending
                assert settings.value(key + "SortAsc", None) is False
        for ascending in (True, False):
            click(window, button)
            click(window, item(window, key + "Sort" + ("Ascending" if ascending else "Descending")))
            assert model.sortAscending is ascending
            assert settings.value(key + "SortAsc", None) is ascending
        open_menu(window, key)
        assert options.property("visible")
        assert not menu.property("visible")
        assert not options.findChild(QQuickItem, key + "Sort_name")
        options.close()
        QTest.qWait(160)

    assert not warnings, [w.toString() for w in warnings]
    window.close()
    del engine


def test_collection_headers_match_songs_and_fit_narrow_pages(qt_app, backend):
    seed_playlists(backend)
    engine, window, warnings = _load(qt_app, backend)
    song_title = item(window, "songsTitle")
    song_summary = item(window, "songsSummary")
    window.setWidth(940)
    window.setProperty("infoWidth", 380)
    window.setProperty("infoOpen", True)
    QTest.qWait(300)
    for key, title in (("artist", "Artists"), ("album", "Albums"), ("playlist", "Playlists")):
        navigate(window, key)
        heading = item(window, key + "Title")
        summary = item(window, key + "Summary")
        header = item(window, "playlistHeader" if key == "playlist" else "libraryCollectionHeader")
        assert heading.property("text") == title
        assert heading.property("font") == song_title.property("font")
        assert summary.property("font") == song_summary.property("font")
        assert summary.property("color") == song_summary.property("color")
        actions = item(window, key + "Actions")
        assert header.property("stacked") is (key == "playlist")
        if header.property("stacked"):
            assert actions.y() > summary.mapToItem(header, 0, summary.height()).y()
        else:
            assert actions.x() > heading.mapToItem(header, heading.width(), 0).x()
        assert actions.x() + actions.width() <= header.width() - header.property("sideMargin")
        assert actions.y() + actions.height() < header.property("implicitHeight")
    assert not warnings, [w.toString() for w in warnings]
    window.close()
    del engine


@pytest.mark.parametrize("key,menu_name,grid_name,default_grid", COLLECTIONS)
def test_display_menu_switches_layout_and_density_and_remembers_it(
    qt_app, backend, key, menu_name, grid_name, default_grid
):
    seed_playlists(backend)
    engine, window, warnings = _load(qt_app, backend)
    navigate(window, key)
    menu = window.findChild(QObject, menu_name)
    assert menu.property("gridMode") is default_grid
    grid = item(item(window, "libraryTable") if key == "song" else window, grid_name)
    count = grid.property("count")
    if key == "song":
        count = window.findChild(QObject, "libraryTracks").property("count")

    open_menu(window, key)
    assert menu.property("visible")
    layout = item(window, key + "LayoutOption")
    compact = item(window, key + "CompactOption")
    slider = item(window, key + "TileSlider")
    # Only playlists have an action above their display controls.
    separator = menu.property("displaySeparator")
    if key != "playlist":
        assert not separator.isVisible()
        assert layout.y() >= separator.y()
    else:
        assert layout.y() > separator.y()
    assert compact.y() > layout.y()
    assert compact.isVisible() is not default_grid
    assert slider.isVisible() is default_grid
    click(window, layout)
    assert menu.property("gridMode") is not default_grid

    if not menu.property("gridMode"):
        grid = item(window, grid_name)
        assert grid.property("listMode") is True
        open_menu(window, key)
        normal_height = (item(window, "libraryTable").property("rowHeight")
                         if key == "song" else grid.property("cellHeight"))
        click(window, compact)
        dense_height = (item(window, "libraryTable").property("rowHeight")
                        if key == "song" else grid.property("cellHeight"))
        assert dense_height < normal_height
        open_menu(window, key)
        click(window, layout)

    grid = item(item(window, "libraryTable") if key == "song" else window, grid_name)
    assert grid.property("listMode") is False
    assert grid.property("count") == count
    open_menu(window, key)
    before = grid.property("columns")
    control = slider.findChild(QQuickItem, "tileSizeControl")
    control.forceActiveFocus(Qt.TabFocusReason)
    QTest.keyClick(window, Qt.Key_Left)
    QTest.qWait(30)
    assert grid.property("columns") == before + 1
    assert menu.property("visible"), "adjusting tile size must keep the menu open"
    settings = backend.get_service("settings_view_model")
    assert settings.value(key + "Grid", None) is True
    size = settings.value(key + "TileSize", None)
    assert 120 <= size <= 300
    assert not warnings, [w.toString() for w in warnings]
    window.close()
    del engine

    engine, restored, warnings = _load(qt_app, backend)
    navigate(restored, key)
    assert restored.findChild(QObject, menu_name).property("gridMode")
    assert item(item(restored, "libraryTable") if key == "song" else restored, grid_name).property("columns") == before + 1
    for other, other_menu, _, other_default in COLLECTIONS:
        if other != key:
            assert restored.findChild(QObject, other_menu).property("gridMode") is other_default
    assert not warnings, [w.toString() for w in warnings]
    restored.close()
    del engine


def test_song_selection_and_context_actions_survive_a_layout_switch(qt_app, backend):
    engine, window, warnings = _load(qt_app, backend)
    navigate(window, "song")
    table = item(window, "libraryTable")
    query = window.findChild(QObject, "libraryTracks")
    first, second = query.uidAt(0), query.uidAt(1)
    _call(table, "handleClick", first, False, False)
    _call(table, "handleClick", second, True, False)
    selected = [query.trackIdAt(i) for i in (0, 1)]
    selected_uids = query.selectedUids()
    assert len(selected) == 2
    open_menu(window, "song")
    click(window, item(window, "songLayoutOption"))
    grid = item(table, "songGridView")
    assert query.selectedUids() == selected_uids
    assert not item(table, "trackList").isVisible()
    assert grid.isVisible()
    assert table.property("count") == 8
    assert item(table, "trackEmptyState").property("opacity") == 0
    _call(table, "openTrackMenu", second, query.trackIdAt(1), 1, "Track")
    assert table.property("_menuTrackIds").toVariant() == selected
    assert table.property("_menuRows") == [0, 1]
    QTest.keyClick(window, Qt.Key_Escape)
    # Returning to the list preserves the same model and selection owner.
    open_menu(window, "song")
    click(window, item(window, "songLayoutOption"))
    assert query.selectedUids() == selected_uids
    assert item(table, "trackList").isVisible()
    assert not warnings, [w.toString() for w in warnings]
    window.close()
    del engine


def test_compact_songs_fit_their_artwork_and_restore_density(qt_app, backend):
    engine, window, warnings = _load(qt_app, backend)
    navigate(window, "song")
    table = item(window, "libraryTable")
    normal_height = table.property("rowHeight")
    open_menu(window, "song")
    compact = item(window, "songCompactOption")
    click(window, compact)
    assert table.property("rowHeight") < normal_height
    assert table.property("artworkSize") < table.property("rowHeight")
    assert table.property("count") == 8
    open_menu(window, "song")
    assert compact.property("checked")
    click(window, compact)
    assert table.property("rowHeight") == normal_height
    assert not backend.get_service("settings_view_model").value("songCompact", True)
    assert not warnings, [w.toString() for w in warnings]
    window.close()
    del engine


def test_song_grid_reports_only_its_viewport_for_lazy_fetching(qt_app, backend):
    from app.models.track import Track

    session = backend.get_service("db_session")
    session.add_all(Track(title=f"Extra {i}", duration_seconds=180) for i in range(120))
    session.commit()
    engine, window, warnings = _load(qt_app, backend)
    navigate(window, "song")
    table = item(window, "libraryTable")
    open_menu(window, "song")
    click(window, item(window, "songLayoutOption"))
    grid = item(table, "songGridView")
    grid.setProperty("contentY", 10 * grid.property("cellHeight"))
    QTest.qWait(100)
    query = window.findChild(QObject, "libraryTracks")
    assert query._visible_first > 0
    assert query._visible_last > query._visible_first
    assert query._visible_last < query.property("count") - 1
    assert table.property("count") == 128
    assert item(table, "trackEmptyState").property("opacity") == 0
    assert not warnings, [w.toString() for w in warnings]
    window.close()
    del engine


@pytest.mark.parametrize("width", [601, 777, 1031])
def test_every_slider_stop_reaches_the_requested_column_count(qt_app, backend, width):
    engine, window, warnings = _load(qt_app, backend)
    navigate(window, "artist")
    grid = item(window, "artistCollectionView")
    grid.setWidth(width)
    qt_app.processEvents()
    slider = item(window, "artistTileSlider")
    for columns in range(slider.property("_minCols"), slider.property("_maxCols") + 1):
        _call(slider, "applyColumns", columns)
        qt_app.processEvents()
        assert grid.property("columns") == columns
        assert slider.property("columns") == columns
    assert not warnings, [w.toString() for w in warnings]
    window.close()
    del engine


@pytest.mark.parametrize("key", ["artist", "album", "playlist"])
def test_layout_switch_keeps_the_visible_item_and_only_one_live_view(qt_app, backend, key):
    from app.models.track import Album, Artist

    if key == "playlist":
        vm = backend.get_service("playlists_view_model")
        for index in range(100):
            vm.createPlaylist(f"Playlist {index:03}")
    else:
        session = backend.get_service("db_session")
        entity = Artist if key == "artist" else Album
        session.add_all(entity(name=f"Extra {index:03}") for index in range(100))
        session.commit()
        backend.get_service(key + "s_view_model").refresh()

    engine, window, warnings = _load(qt_app, backend)
    navigate(window, key)
    name = key + "CollectionView"
    grid = item(window, name)
    anchor = 4 * grid.property("columns")
    grid.setProperty("contentY", 4 * grid.property("cellHeight"))
    QTest.qWait(100)
    expected_count = grid.property("count")
    open_menu(window, key)
    click(window, item(window, key + "LayoutOption"))
    listing = item(window, name)
    assert listing.property("listMode") is True
    assert listing.property("reuseItems") is True
    assert listing.property("count") == expected_count
    assert round(listing.property("contentY") / listing.property("cellHeight")) == anchor
    assert len(window.findChildren(QQuickItem, name)) == 1
    content_y = listing.property("contentY")
    navigate(window, "song")
    navigate(window, key)
    assert item(window, name).property("contentY") == content_y
    open_menu(window, key)
    click(window, item(window, key + "LayoutOption"))
    restored_grid = item(window, name)
    assert restored_grid.property("listMode") is False
    assert round(restored_grid.property("contentY") / restored_grid.property("cellHeight")) * restored_grid.property("columns") == anchor
    assert len(window.findChildren(QQuickItem, name)) == 1
    assert not warnings, [w.toString() for w in warnings]
    window.close()
    del engine


@pytest.mark.parametrize("key,destination", [("artist", 9), ("album", 11), ("playlist", 3)])
@pytest.mark.parametrize("list_mode", [False, True])
def test_collection_cells_open_their_entity_with_the_keyboard(qt_app, backend, key, destination, list_mode):
    seed_playlists(backend)
    backend.get_service("settings_view_model").setValue(key + "Grid", not list_mode)
    engine, window, warnings = _load(qt_app, backend)
    navigate(window, key)
    cell = current_cell(window, key)
    entity_id = int(cell.property(key + "Id"))
    cell.forceActiveFocus(Qt.TabFocusReason)
    QTest.keyClick(window, Qt.Key_Return)
    QTest.qWait(200)
    assert window.property("viewIndex") == destination
    if key == "playlist":
        assert item(window, "playlistsView").property("openId") == entity_id
    else:
        assert item(window, key + "View").property(key + "Id") == entity_id
    assert not warnings, [w.toString() for w in warnings]
    window.close()
    del engine


@pytest.mark.parametrize("list_mode", [False, True])
def test_playlist_context_and_play_button_keep_their_actions(qt_app, backend, list_mode):
    from app.models.track import Track

    vm = backend.get_service("playlists_view_model")
    playlist_id = vm.createPlaylist("Evening")
    ids = [track.id for track in backend.get_service("db_session").query(Track).all()][:3]
    vm.addTracksToPlaylist(playlist_id, ids)
    backend.get_service("settings_view_model").setValue("playlistGrid", not list_mode)
    engine, window, warnings = _load(qt_app, backend)
    navigate(window, "playlist")
    cell = current_cell(window, "playlist")
    cell.forceActiveFocus(Qt.TabFocusReason)
    QTest.keyClick(window, Qt.Key_Menu)
    QTest.qWait(100)
    page = item(window, "playlistsView")
    assert page.property("_menuId") == playlist_id
    assert page.property("_menuName") == "Evening"
    assert page.property("_menuCount") == 3
    QTest.keyClick(window, Qt.Key_Escape)
    cell.forceActiveFocus(Qt.TabFocusReason)
    play = item(cell, "collectionPlayButton")
    assert play.isVisible()
    click(window, play)
    QTest.qWait(100)
    assert backend.get_service("queue_view_model").trackIds() == ids
    assert page.property("openId") == -1, "play should not also navigate into the playlist"
    assert not warnings, [w.toString() for w in warnings]
    window.close()
    del engine


def test_song_tile_keyboard_uses_shared_selection_and_opens_the_editor(qt_app, backend):
    backend.get_service("settings_view_model").setValue("songGrid", True)
    engine, window, warnings = _load(qt_app, backend)
    navigate(window, "song")
    table = item(window, "libraryTable")
    cell = current_cell(window, "song")
    uid, track_id, index = (int(cell.property(name)) for name in ("uid", "trackId", "index"))
    activated = []
    table.editTrackRequested.connect(activated.append)
    cell.forceActiveFocus(Qt.TabFocusReason)
    QTest.keyClick(window, Qt.Key_Space)
    assert window.findChild(QObject, "libraryTracks").selectedUids() == [uid]
    QTest.keyClick(window, Qt.Key_Return)
    assert activated == [track_id]
    assert window.property("viewIndex") == 7
    navigate(window, "song")
    cell.forceActiveFocus(Qt.TabFocusReason)
    QTest.keyClick(window, Qt.Key_Menu)
    assert table.property("_menuTrackId") == track_id
    assert table.property("_menuRows") == [index]
    assert not warnings, [w.toString() for w in warnings]
    window.close()
    del engine
