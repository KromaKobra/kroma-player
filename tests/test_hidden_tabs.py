"""The artist and album tabs stay in the rail until Back closes them.

Both pages are hidden destinations: nothing in the rail leads to one until you
ask for it by name, from a search result, a track's artist, or a tile in the
Library. Once you have asked, the slot is the only way back to the page — so it
has to outlive stepping away from it. Tying it to "you are standing on the
page", which is what it used to be, took the slot away the moment you clicked
anything else in the rail, and the artist you were reading was gone with no
route back short of finding them again.

So the rule these tests hold: opening the page opens the slot, only Back on
that page closes it, and while the slot is open from elsewhere it points at the
page rather than its editor.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

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


def _reset(window, qt_app):
    """Close both pages and stand on the Library, whatever ran before."""
    window.setProperty("artistOpen", False)
    window.setProperty("albumOpen", False)
    _call(window, qt_app, "go", LIBRARY_VIEW_INDEX)


def test_the_artist_slot_survives_leaving_the_page(gui):
    window, qt_app = gui
    _reset(window, qt_app)
    rail = _item(window, "navRail")

    _call(window, qt_app, "openArtist", 1)
    assert window.property("viewIndex") == ARTIST_VIEW_INDEX
    assert rail.property("showArtist"), "opening an artist did not open the slot"

    _call(window, qt_app, "go", LIBRARY_VIEW_INDEX)
    assert rail.property("showArtist"), (
        "the artist slot went away on stepping over to the Library — the page "
        "is then unreachable, which is the whole reason the slot is there"
    )
    assert rail.property("artistView") == ARTIST_VIEW_INDEX, (
        "the slot points at the editor from elsewhere; it should lead back to "
        "the page you were looking at"
    )


def test_back_on_the_artist_page_closes_its_slot(gui):
    window, qt_app = gui
    _reset(window, qt_app)
    rail = _item(window, "navRail")

    _call(window, qt_app, "openArtist", 1)
    _item(window, "artistView").done.emit()
    _settle(qt_app)

    assert window.property("viewIndex") == LIBRARY_VIEW_INDEX, (
        "Back did not return to where the artist was opened from"
    )
    assert not rail.property("showArtist"), "Back left the artist slot up"


def test_the_album_slot_survives_leaving_the_page(gui):
    window, qt_app = gui
    _reset(window, qt_app)
    rail = _item(window, "navRail")

    _call(window, qt_app, "openAlbum", 1)
    assert window.property("viewIndex") == ALBUM_VIEW_INDEX
    assert rail.property("showAlbum"), "opening an album did not open the slot"

    _call(window, qt_app, "go", LIBRARY_VIEW_INDEX)
    assert rail.property("showAlbum"), (
        "the album slot went away on stepping over to the Library"
    )
    assert rail.property("albumView") == ALBUM_VIEW_INDEX


def test_back_on_the_album_page_closes_its_slot(gui):
    window, qt_app = gui
    _reset(window, qt_app)
    rail = _item(window, "navRail")

    _call(window, qt_app, "openAlbum", 1)
    _item(window, "albumView").done.emit()
    _settle(qt_app)

    assert window.property("viewIndex") == LIBRARY_VIEW_INDEX
    assert not rail.property("showAlbum"), "Back left the album slot up"
