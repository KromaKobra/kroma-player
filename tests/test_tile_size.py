"""How big the tiles on the artist and album walls are.

Both walls fill their row — ``cellWidth = floor(width / columns)`` — so the size
a user picks is not the size that gets drawn. The preference is a *target*
width, the slider in each heading steps through *column counts*, and the number
written back is the width those columns work out to. Three representations of
one idea, and the seams between them are what this covers:

- the stored width has to reach the wall and change its layout,
- a stop the user drags to has to come back as a width the next launch can use,
- and neither end may be trusted: a hand-edited settings.json is an integer like
  any other, and a wall of two 9999px tiles has no way back to a usable one.

Plus the guarantee that makes this change invisible to anyone who does not want
it: with nothing stored, both walls open at exactly the numbers they were
hard-coded to before there was a slider.

Loaded against the real backend for the same reason ``test_artists_grid`` is: a
QML binding error on a wall of covers ships as blank tiles rather than as a
failure, so only a real engine can say the wall came up.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine

from app.gui.gui_paths import bundled_gui_root
from app.main import CONTEXT_PROPERTIES, _register_qml_types
from app.models.track import Album, Artist, Track

# The clamp both walls apply to whatever comes out of settings.json. Duplicated
# from LibraryEntityView rather than read out of them: a test that reads its
# expectation from the thing under test cannot fail when that thing changes.
MIN_TILE = 120
MAX_TILE = 300

# What each wall was hard-coded to before the slider existed.
ARTIST_DEFAULT = 170
ALBUM_DEFAULT = 200


@pytest.fixture
def backend(tmp_path, monkeypatch):
    """A library with enough artists and albums for a wall to have rows."""
    from app.utils.library_paths import set_library_root

    monkeypatch.setenv("KROMA_DATA_DIR", str(tmp_path))
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    set_library_root(vault)

    from app.bootstrap import create_service_registry

    registry = create_service_registry()
    session = registry.get_service("db_session")

    artists = [Artist(name=f"Artist {i}") for i in range(8)]
    albums = [Album(name=f"Album {i}") for i in range(8)]
    session.add_all(artists + albums)
    session.flush()

    for i, (artist, album) in enumerate(zip(artists, albums)):
        track = Track(title=f"Track {i}", duration_seconds=200, artists=[artist])
        track.albums = [album]
        session.add(track)
    session.commit()

    # Both models were built against an empty database and these rows went in
    # through the session rather than through a service, so nothing on the
    # event bus told them they exist.
    registry.get_service("artists_view_model").refresh()
    registry.get_service("albums_view_model").refresh()

    _register_qml_types(registry)
    return registry


def _load(qt_app, backend):
    """Bring up Nocturne. Seed any preference *before* calling this — `Theme`
    reads the store once, when its engine builds it."""
    directory = bundled_gui_root() / "nocturne"
    engine = QQmlApplicationEngine()

    from app.artwork.cover_provider import CoverImageProvider
    from app.artwork.local_art_provider import LocalArtImageProvider

    engine.addImageProvider("cover", CoverImageProvider(backend.get_service("cover_store")))
    engine.addImageProvider("localart", LocalArtImageProvider())

    context = engine.rootContext()
    for qml_name, service in CONTEXT_PROPERTIES.items():
        context.setContextProperty(qml_name, backend.get_service(service))

    warnings: list = []
    engine.warnings.connect(lambda w: warnings.extend(w))

    engine.addImportPath(str(directory))
    engine.load(QUrl.fromLocalFile(str(directory / "main.qml")))

    assert engine.rootObjects(), "nocturne failed to load:\n" + "\n".join(
        w.toString() for w in warnings
    )
    qt_app.processEvents()
    return engine, engine.rootObjects()[0], warnings


def _wall(qt_app, root, name):
    """The named grid, switched to the face it lives on the way the Pill does."""
    wall = root.findChild(object, name)
    assert wall is not None, f"LibraryView has no {name}"
    wall.setProperty("visible", True)
    qt_app.processEvents()
    return wall


def _call(obj, name, *args):
    """Invoke a QML-declared function. They are not Python attributes."""
    from PySide6.QtCore import Q_ARG, QMetaObject, Qt

    return QMetaObject.invokeMethod(
        obj, name, Qt.ConnectionType.DirectConnection,
        *[Q_ARG("QVariant", value) for value in args],
    )


def test_the_wall_opens_at_the_stored_size(qt_app, backend):
    """The seam the preference crosses. A stored width is not a column count,
    so the only thing worth asserting is the direction: the smaller target has
    to lay out more columns than the larger one, in the same window."""
    settings = backend.get_service("settings_view_model")

    settings.setValue("artistTileSize", 130)
    engine, root, _ = _load(qt_app, backend)
    grid = _wall(qt_app, root, "artistCollectionView")
    width = grid.property("width")
    dense = grid.property("columns")
    del engine

    settings.setValue("artistTileSize", 280)
    engine, root, _ = _load(qt_app, backend)
    grid = _wall(qt_app, root, "artistCollectionView")
    sparse = grid.property("columns")

    assert width > 0, "the wall was never laid out, so its columns mean nothing"
    assert dense > sparse, (
        f"a 130px target laid out {dense} columns and a 280px target {sparse}; "
        "the stored size is not reaching the grid"
    )
    # Not just *a* difference — the count the arithmetic actually calls for.
    assert dense == max(2, int(width // 130))
    assert sparse == max(2, int(width // 280))

    del engine


def test_an_untouched_install_opens_where_it_always_did(qt_app, backend):
    """The whole compatibility claim, in one assertion per wall: with nothing
    stored, the targets are the numbers the grids used to hard-code."""
    engine, root, _ = _load(qt_app, backend)

    artists = _wall(qt_app, root, "artistCollection")
    albums = _wall(qt_app, root, "albumCollection")

    assert artists.property("tileSize") == ARTIST_DEFAULT
    assert albums.property("tileSize") == ALBUM_DEFAULT

    # And nothing was written for a preference nobody touched — `Theme`'s
    # `_ready` guard is what keeps a first launch from materialising defaults.
    settings = backend.get_service("settings_view_model")
    assert settings.value("artistTileSize", None) is None
    assert settings.value("albumTileSize", None) is None

    del engine


def test_a_stop_moves_the_wall_and_is_remembered(qt_app, backend):
    """The seam the control crosses, in the direction a drag goes: a column
    count picked on the slider has to land in the grid *and* come back out of
    the store as a width, because a column count would not survive a resize."""
    engine, root, _ = _load(qt_app, backend)

    grid = _wall(qt_app, root, "artistCollectionView")
    slider = root.findChild(object, "artistTileSlider")
    assert slider is not None, "the artist heading has no tile slider"
    assert slider.property("available"), (
        "the test window is too narrow to offer a second layout"
    )

    width = grid.property("width")
    before = grid.property("columns")
    assert slider.property("columns") == before, (
        "the control and the wall disagree about how many tiles are across"
    )

    # One notch towards smaller tiles, which is one more column.
    _call(slider, "applyColumns", before + 1)
    qt_app.processEvents()

    assert grid.property("columns") == before + 1, "the stop never reached the wall"

    settings = backend.get_service("settings_view_model")
    stored = settings.value("artistTileSize", None)
    assert stored == round(width / (before + 1)), (
        f"stored {stored!r}, but the stop was {before + 1} columns of a {width}px wall"
    )
    # The album wall has its own preference and must not have moved with it.
    assert settings.value("albumTileSize", None) is None

    del engine


def test_a_garbage_stored_size_is_clamped(qt_app, backend):
    """A preference file is a text file. Nothing stops a 9999 landing in it, and
    a wall of two tiles the size of the window is not something a user could
    drag their way back out of — the slider would have one stop."""
    settings = backend.get_service("settings_view_model")
    settings.setValue("artistTileSize", 9999)
    settings.setValue("albumTileSize", 1)

    engine, root, _ = _load(qt_app, backend)

    artists = _wall(qt_app, root, "artistCollection")
    albums = _wall(qt_app, root, "albumCollection")

    assert artists.property("tileSize") == MAX_TILE
    assert albums.property("tileSize") == MIN_TILE

    for name, target in (("artistCollectionView", MAX_TILE), ("albumCollectionView", MIN_TILE)):
        grid = _wall(qt_app, root, name)
        assert grid.property("columns") == max(2, int(grid.property("width") // target))
        assert grid.property("cellWidth") > 0

    del engine


def test_the_walls_load_with_no_binding_errors(qt_app, backend):
    """A binding error here reaches a user as blank tiles rather than as a
    crash, so the load has to be checked rather than assumed."""
    settings = backend.get_service("settings_view_model")
    settings.setValue("artistTileSize", 240)
    settings.setValue("albumTileSize", 140)

    engine, root, warnings = _load(qt_app, backend)

    _wall(qt_app, root, "artistCollection")
    _wall(qt_app, root, "albumCollection")
    slider = root.findChild(object, "artistTileSlider")
    _call(slider, "applyColumns", slider.property("columns") + 1)
    qt_app.processEvents()

    fatal = [
        w.toString()
        for w in warnings
        if "TileSizeSlider" in w.toString()
        or "Library" in w.toString()
        or "ArtistDelegate" in w.toString()
        or "AlbumDelegate" in w.toString()
        or "is not defined" in w.toString()
        or "Unable to assign" in w.toString()
    ]
    assert not fatal, "tile-size binding errors:\n" + "\n".join(fatal)

    del engine
