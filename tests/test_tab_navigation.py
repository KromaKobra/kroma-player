"""Pages opened inside a tab, and how you get back out of them.

Every rail entry is a tab. An artist, an album, a track editor or a playlist
opens *inside* the tab it belongs to — Artists, Albums, Songs, Playlists —
wherever it was asked for from, and the rail lights that tab rather than
growing an entry of its own. The back arrow on such a page goes up one level in
the same tab; the title bar's history is how you return to anything else.

So the rules these tests hold:

- the rail lists tabs and nothing else;
- a page lights its tab, and Back goes up one level within it;
- a tab keeps the page you left it on, and choosing the tab you are already on
  takes you to its root;
- a playlist is a history entry like any other page;
- leaving an editor by its back arrow asks before throwing edits away.

The module-scoped ``gui`` fixture and ``_call`` are shared with other tests.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QObject

from test_album_search_rows import _load, backend  # noqa: F401 (fixture)

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

LIBRARY_VIEW_INDEX = 4
ARTIST_VIEW_INDEX = 9
ALBUM_VIEW_INDEX = 11


@pytest.fixture(scope="module")
def gui(qt_app, tmp_path_factory):
    """The real window over a throwaway vault."""
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine

    from app.artwork.cover_provider import CoverImageProvider
    from app.artwork.local_art_provider import LocalArtImageProvider
    from app.gui.gui_paths import bundled_gui_root
    from app.main import CONTEXT_PROPERTIES, _register_qml_types
    from app.utils.library_paths import set_library_root

    vault = tmp_path_factory.mktemp("vault") / "venomvault"
    (vault / ".data").mkdir(parents=True)
    set_library_root(vault)

    from app.bootstrap import create_service_registry

    registry = create_service_registry()
    # Exercise real profile routes. Invalid IDs now stay on the current view,
    # so an empty database cannot stand in for an artist or album page.
    from app.models.track import Album, Artist
    session = registry.get_service("db_session")
    session.add_all([Artist(name="Test artist"), Album(name="Test album")])
    session.commit()
    _register_qml_types(registry)

    directory = bundled_gui_root() / "nocturne"
    engine = QQmlApplicationEngine()
    engine.addImageProvider(
        "cover", CoverImageProvider(registry.get_service("cover_store"))
    )
    engine.addImageProvider("localart", LocalArtImageProvider())
    context = engine.rootContext()
    for qml_name, service in CONTEXT_PROPERTIES.items():
        context.setContextProperty(qml_name, registry.get_service(service))
    engine.addImportPath(str(directory))
    engine.load(QUrl.fromLocalFile(str(directory / "main.qml")))

    roots = engine.rootObjects()
    assert roots, "the GUI failed to load"
    qt_app.processEvents()

    yield roots[0], qt_app

    del engine


def _settle(qt_app, seconds=0.45):
    """Pump past the cross-fade, which outlasts the property change."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.02)


def _item(window, name):
    from PySide6.QtQuick import QQuickItem

    item = window.findChild(QQuickItem, name)
    assert item is not None, f"{name} is gone or renamed"
    return item


def _call(window, qt_app, name, argument=None):
    """Call one of main.qml's navigation functions the way the views do."""
    from PySide6.QtCore import Q_ARG, QMetaObject, Qt

    if argument is None:
        ok = QMetaObject.invokeMethod(window, name, Qt.DirectConnection)
    else:
        ok = QMetaObject.invokeMethod(
            window, name, Qt.DirectConnection, Q_ARG("QVariant", argument)
        )
    assert ok, f"main.qml no longer exposes {name}()"
    _settle(qt_app)


# ------------------------------------------------------------ one window each
#
# A fresh window over a small library per test: a tab remembering its page is
# exactly the kind of state that would leak between tests on a shared window.

ARTISTS_MODE, ALBUMS_MODE, SONGS_MODE = 1, 2, 0
QUEUE_VIEW_INDEX, PLAYLISTS_VIEW_INDEX = 1, 3
TRACK_EDIT_VIEW_INDEX, ARTIST_EDIT_VIEW_INDEX, ALBUM_EDIT_VIEW_INDEX = 7, 10, 12


COVER = "c" * 32


@pytest.fixture
def app(qt_app, backend):
    from app.models.track import Album, Artist, Track

    registry = backend
    session = registry.get_service("db_session")
    # A playlist's cover is its first track's, so give one track artwork.
    session.query(Track).order_by(Track.id).first().cover_hash = COVER
    second = Artist(name="Second artist")
    session.add(second)
    session.add(Track(title="Second track", duration_seconds=60, artists=[second],
                      albums=[Album(name="Second album")]))
    session.commit()
    engine, window, warnings = _load(qt_app, registry)
    _call(window, qt_app, "go", QUEUE_VIEW_INDEX)
    yield window, qt_app, registry, warnings
    window.close()
    del engine


def _ids(registry, model):
    return [row.id for row in registry.get_service("db_session").query(model).all()]


def _lit_tab(window):
    """The label of the rail entry that is lit, or None."""
    rail = _item(window, "navRail")
    slot = rail.property("currentSlot")
    items = rail.property("items").toVariant()
    return items[slot]["label"] if slot >= 0 else None


def _choose_tab(window, qt_app, view, mode=None):
    """Choose a rail entry the way clicking it does — through the rail's signal."""
    rail = _item(window, "navRail")
    if mode is None:
        rail.navigated.emit(view)
    else:
        rail.libraryNavigated.emit(mode)
    _settle(qt_app)


def _back(window, qt_app, page):
    _item(window, page).done.emit()
    _settle(qt_app)


def test_the_rail_lists_tabs_and_nothing_else(app):
    window, qt_app, registry, warnings = app
    artist, album, track = (_ids(registry, m)[0] for m in _models())
    _call(window, qt_app, "openArtist", artist)
    _call(window, qt_app, "openAlbum", album)
    _call(window, qt_app, "editTrack", track)

    views = {entry["view"] for entry in _item(window, "navRail").property("items").toVariant()}
    assert not views & {7, 9, 10, 11, 12}, "an opened page added its own rail entry"
    assert not warnings, [w.toString() for w in warnings]


def _models():
    from app.models.track import Album, Artist, Track
    return Artist, Album, Track


@pytest.mark.parametrize("opener,view,tab,mode", [
    ("openArtist", ARTIST_VIEW_INDEX, "Artists", ARTISTS_MODE),
    ("openAlbum", ALBUM_VIEW_INDEX, "Albums", ALBUMS_MODE),
    ("editTrack", TRACK_EDIT_VIEW_INDEX, "Songs", SONGS_MODE),
])
def test_a_page_opened_from_another_tab_lands_in_its_own(app, opener, view, tab, mode):
    window, qt_app, registry, warnings = app
    model = {"openArtist": 0, "openAlbum": 1, "editTrack": 2}[opener]
    entity = _ids(registry, _models()[model])[0]

    _call(window, qt_app, opener, entity)

    assert window.property("viewIndex") == view
    assert _lit_tab(window) == tab, "the page did not light the tab it belongs to"
    assert _item(window, "navRail").property("libraryMode") == mode
    assert not warnings, [w.toString() for w in warnings]


@pytest.mark.parametrize("opener,page,mode", [
    ("openArtist", "artistView", ARTISTS_MODE),
    ("openAlbum", "albumView", ALBUMS_MODE),
    ("editTrack", "trackEditView", SONGS_MODE),
])
def test_back_goes_up_to_the_tab_not_to_where_it_was_opened_from(app, opener, page, mode):
    window, qt_app, registry, warnings = app
    model = {"openArtist": 0, "openAlbum": 1, "editTrack": 2}[opener]
    _call(window, qt_app, opener, _ids(registry, _models()[model])[0])

    _back(window, qt_app, page)

    assert window.property("viewIndex") == LIBRARY_VIEW_INDEX, (
        "Back left the tab — history is the way back to the Queue, not the arrow"
    )
    assert _item(window, "navRail").property("libraryMode") == mode


@pytest.mark.parametrize("opener,editor,page,parent", [
    ("editArtist", "artistEditView", "artistView", ARTIST_VIEW_INDEX),
    ("editAlbum", "albumEditView", "albumView", ALBUM_VIEW_INDEX),
])
def test_an_editors_back_returns_to_its_entity_page(app, opener, editor, page, parent):
    window, qt_app, registry, warnings = app
    model = 0 if opener == "editArtist" else 1
    _call(window, qt_app, opener, _ids(registry, _models()[model])[0])

    _back(window, qt_app, editor)
    assert window.property("viewIndex") == parent

    _back(window, qt_app, page)
    assert window.property("viewIndex") == LIBRARY_VIEW_INDEX


def test_a_tab_keeps_its_page_and_choosing_it_again_goes_to_its_root(app):
    window, qt_app, registry, warnings = app
    artist = _ids(registry, _models()[0])[0]
    _call(window, qt_app, "openArtist", artist)

    _choose_tab(window, qt_app, QUEUE_VIEW_INDEX)
    _choose_tab(window, qt_app, LIBRARY_VIEW_INDEX, ARTISTS_MODE)
    assert window.property("viewIndex") == ARTIST_VIEW_INDEX, (
        "coming back to Artists lost the artist that was open in it"
    )
    assert registry.get_service("artist_edit_view_model").artistId == artist

    _choose_tab(window, qt_app, LIBRARY_VIEW_INDEX, ARTISTS_MODE)
    assert window.property("viewIndex") == LIBRARY_VIEW_INDEX, (
        "choosing the tab you are on should go to its root"
    )

    # Another Library tab keeps its own page, not this one's.
    _choose_tab(window, qt_app, LIBRARY_VIEW_INDEX, ALBUMS_MODE)
    assert window.property("viewIndex") == LIBRARY_VIEW_INDEX
    assert _lit_tab(window) == "Albums"


def test_a_page_is_saved_as_its_tab(app):
    window, qt_app, registry, warnings = app
    settings = registry.get_service("settings_view_model")
    _call(window, qt_app, "openArtist", _ids(registry, _models()[0])[0])
    assert settings.sessionValue("navIndex", -1) == LIBRARY_VIEW_INDEX, (
        "a page that needs an entity was saved as a bare index to reopen"
    )
    assert settings.sessionValue("libraryMode", -1) == ARTISTS_MODE


def _playlists(registry, names):
    from app.models.track import Track

    vm = registry.get_service("playlists_view_model")
    tracks = _ids(registry, Track)
    ids = [vm.createPlaylist(name) for name in names]
    for playlist_id in ids:
        vm.addTracksToPlaylist(playlist_id, tracks[:1])
    return ids


def test_a_playlist_is_a_history_entry(app):
    window, qt_app, registry, warnings = app
    first, second = _playlists(registry, ["First", "Second"])
    page = _item(window, "playlistsView")
    history = window.findChild(QObject, "navigationHistory")

    _call(window, qt_app, "openPlaylist", first)
    _call(window, qt_app, "openPlaylist", second)
    assert page.property("openId") == second
    assert _lit_tab(window) == "Playlists"

    cursor = history.property("cursor")
    _call(window, qt_app, "visitHistory", cursor - 1)
    assert page.property("openId") == first
    tracks = registry.get_service("playlist_tracks_view_model")
    assert tracks.playlistId == first
    assert tracks.coverHash == COVER, "a playlist reopened from history lost its cover"
    labels = [entry["label"] for entry in history.property("entries").toVariant()]
    assert "Playlist · First" in labels

    _back(window, qt_app, "playlistsView")
    assert page.property("openId") == -1
    assert window.property("viewIndex") == PLAYLISTS_VIEW_INDEX
    assert not warnings, [w.toString() for w in warnings]


def test_the_playlists_tab_keeps_its_playlist_and_pops_like_the_others(app):
    window, qt_app, registry, warnings = app
    (only,) = _playlists(registry, ["Only"])
    page = _item(window, "playlistsView")
    _call(window, qt_app, "openPlaylist", only)

    _choose_tab(window, qt_app, QUEUE_VIEW_INDEX)
    _choose_tab(window, qt_app, PLAYLISTS_VIEW_INDEX)
    assert page.property("openId") == only

    _choose_tab(window, qt_app, PLAYLISTS_VIEW_INDEX)
    assert page.property("openId") == -1


def test_a_deleted_playlist_in_history_leaves_the_current_one_open(app):
    window, qt_app, registry, warnings = app
    first, second = _playlists(registry, ["First", "Second"])
    page = _item(window, "playlistsView")
    history = window.findChild(QObject, "navigationHistory")
    _call(window, qt_app, "openPlaylist", first)
    _call(window, qt_app, "openPlaylist", second)
    cursor = history.property("cursor")

    registry.get_service("playlists_view_model").deletePlaylist(first)
    _call(window, qt_app, "visitHistory", cursor - 1)

    assert history.property("cursor") == cursor
    assert page.property("openId") == second
    assert registry.get_service("playlist_tracks_view_model").playlistId == second


def test_a_playlist_made_elsewhere_does_not_change_the_playlists_tab(app):
    window, qt_app, registry, warnings = app
    _playlists(registry, ["From the queue"])
    assert window.property("viewIndex") == QUEUE_VIEW_INDEX
    assert _item(window, "playlistsView").property("openId") == -1


def test_back_on_an_editor_with_unsaved_edits_asks_first(app):
    window, qt_app, registry, warnings = app
    artist = registry.get_service("artist_edit_view_model")
    _call(window, qt_app, "editArtist", _ids(registry, _models()[0])[0])
    original = artist.name
    artist.name = "Unsaved artist"
    dialog = window.findChild(QObject, "discardEditsDialog")

    _back(window, qt_app, "artistEditView")
    assert dialog.property("visible"), "Back threw the edits away without asking"
    assert window.property("viewIndex") == ARTIST_EDIT_VIEW_INDEX
    dialog.close()
    assert artist.isDirty, "keeping the edits lost them"

    # Stepping away to another tab is not leaving them: the tab keeps its editor.
    _choose_tab(window, qt_app, QUEUE_VIEW_INDEX)
    _choose_tab(window, qt_app, LIBRARY_VIEW_INDEX, ARTISTS_MODE)
    assert window.property("viewIndex") == ARTIST_EDIT_VIEW_INDEX
    assert artist.name == "Unsaved artist"

    _back(window, qt_app, "artistEditView")
    dialog.confirmed.emit()
    dialog.close()
    _settle(qt_app)
    assert window.property("viewIndex") == ARTIST_VIEW_INDEX
    assert not artist.isDirty and artist.name == original
