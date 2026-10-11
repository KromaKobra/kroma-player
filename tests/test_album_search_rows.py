"""Search's albums face, loaded for real.

Modelled on tests/test_artists_grid.py and for the same reason it exists: a
delegate naming a role the model does not serve is a *runtime warning*, not a
load failure, so it reaches a user as blank rows and nothing points back here.
Album rows are especially exposed to that — every value they show comes from a
row dict rather than from a `tracks` row, through two mechanisms that both have
to agree about what `rowKind == "album"` means.

There is a second seam this covers that a Python-level test cannot. `AlbumRow`
reads its four roles off `model` rather than through required properties, which
is deliberate (see the comment on TrackTable's delegate) and means a typo in one
of the names is invisible until something actually paints one.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine

from app.gui.gui_paths import bundled_gui_root
from app.library.roles import FIELD_TO_ROLE
from app.main import CONTEXT_PROPERTIES, _register_qml_types
from app.models.track import Album, Artist, Track


@pytest.fixture
def backend(tmp_path, monkeypatch):
    from app.utils.library_paths import set_library_root

    monkeypatch.setenv("KROMA_DATA_DIR", str(tmp_path))
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    set_library_root(vault)

    from app.bootstrap import create_service_registry

    registry = create_service_registry()
    session = registry.get_service("db_session")

    lions = Artist(name="Seven Lions")
    session.add(lions)
    horizon = Album(name="Horizon", cover_hash="a" * 32)
    other = Album(name="Beyond The Horizon")
    session.add_all([horizon, other])
    session.flush()

    for title, album in (("Foolish Of Me", horizon), ("Rush Over", horizon),
                         ("Days To Come", other)):
        track = Track(title=title, duration_seconds=200, artists=[lions])
        track.albums = [album]
        session.add(track)
    session.commit()
    registry.get_service("albums_view_model").refresh()

    _register_qml_types(registry)
    return registry


def _load(qt_app, backend):
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


def test_an_album_search_fills_the_result_list(qt_app, backend):
    engine, root, warnings = _load(qt_app, backend)
    search_vm = backend.get_service("search_view_model")

    search_vm.searchMode = "albums"
    search_vm.text_search("horizon")
    qt_app.processEvents()

    assert search_vm.rowCount() == 2, (
        "the albums mode returned nothing:\n"
        + "\n".join(w.toString() for w in warnings)
    )
    assert [search_vm.row_value(i, "rowKind") for i in range(2)] == ["album", "album"]

    del engine


def test_the_album_row_reads_no_role_the_model_does_not_serve(qt_app, backend):
    """The roles AlbumRow reads off `model`, asked of the real model.

    Read here rather than trusted from the QML, because the delegate's bindings
    are lazy: a row scrolled off screen never asks, and a name that is wrong is
    simply an empty string in a Text nobody is looking at.
    """
    engine, root, warnings = _load(qt_app, backend)
    search_vm = backend.get_service("search_view_model")

    search_vm.searchMode = "albums"
    search_vm.text_search("horizon")
    qt_app.processEvents()

    index = search_vm.index(0, 0)
    for key in ("albumId", "albumName", "albumCoverHash", "albumTrackCount"):
        assert key in FIELD_TO_ROLE, f"{key} is not in the field catalog"
        assert search_vm.data(index, FIELD_TO_ROLE[key]) is not None, key

    # Exact name beats the one that merely contains it, and the sleeve and count
    # are the album's own rather than any track's.
    assert search_vm.data(index, FIELD_TO_ROLE["albumName"]) == "Horizon"
    assert search_vm.data(index, FIELD_TO_ROLE["albumCoverHash"]) == "a" * 32
    assert search_vm.data(index, FIELD_TO_ROLE["albumTrackCount"]) == 2

    fatal = [
        w.toString() for w in warnings
        if "is not defined" in w.toString() or "Unable to assign" in w.toString()
    ]
    assert not fatal, "binding errors:\n" + "\n".join(fatal)

    del engine


# ---------------------------------------------------------------------------
# The completeness mark on a search row
# ---------------------------------------------------------------------------


def test_the_completeness_role_is_served_to_the_album_row(qt_app, backend):
    """`albumIsComplete` is a ROW-tier field like the four beside it.

    Read off `model` by name, so a typo is an empty binding rather than an
    error — which for a boolean means a checkmark that simply never appears.
    """
    session = backend.get_service("db_session")
    horizon = session.query(Album).filter(Album.name == "Horizon").one()
    horizon.is_complete = True
    session.commit()

    engine, root, warnings = _load(qt_app, backend)
    search_vm = backend.get_service("search_view_model")

    search_vm.searchMode = "albums"
    search_vm.text_search("horizon")
    qt_app.processEvents()

    assert "albumIsComplete" in FIELD_TO_ROLE, "not in the field catalog"
    values = [
        search_vm.data(search_vm.index(row, 0), FIELD_TO_ROLE["albumIsComplete"])
        for row in range(search_vm.rowCount())
    ]
    names = [
        search_vm.data(search_vm.index(row, 0), FIELD_TO_ROLE["albumName"])
        for row in range(search_vm.rowCount())
    ]
    assert dict(zip(names, values)) == {
        "Horizon": True, "Beyond The Horizon": False,
    }

    del engine


def _complete(backend, name):
    session = backend.get_service("db_session")
    session.query(Album).filter(Album.name == name).one().is_complete = True
    session.commit()
    backend.get_service("albums_view_model").refresh()


def test_the_album_wall_serves_the_role_its_delegate_requires(qt_app, backend):
    """`AlbumDelegate` declares `isComplete` as a **required** property.

    A required property the model cannot answer is a delegate that is never
    built, so on a wall of sleeves the failure looks like a library that has
    lost half its records rather than like an error. Asserted through the model
    and the grid's `count`, the way tests/test_artists_grid.py asserts its own:
    the page cross-fades in, and offscreen that animation never advances, so no
    cell is ever laid out and anything read off a delegate would pass for the
    wrong reason.
    """
    from app.viewmodels.albums_view_model import AlbumRoles

    _complete(backend, "Horizon")
    engine, root, warnings = _load(qt_app, backend)

    grid = root.findChild(object, "albumCollection")
    assert grid is not None, "LibraryView has no albumCollection"
    grid.setProperty("visible", True)
    qt_app.processEvents()

    assert grid.property("count") == 2, (
        "the grid did not bind to albumsVM:\n"
        + "\n".join(w.toString() for w in warnings)
    )

    vm = backend.get_service("albums_view_model")
    served = {
        vm.data(vm.index(row, 0), AlbumRoles.Name):
            vm.data(vm.index(row, 0), AlbumRoles.IsComplete)
        for row in range(vm.rowCount())
    }
    assert served == {"Horizon": True, "Beyond The Horizon": False}

    del engine
