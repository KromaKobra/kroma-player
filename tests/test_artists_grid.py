"""The Library page's artists face, loaded for real.

`artistsVM` was shipped with roles nothing rendered — documented, exposed, and
read only by the track editor's chip input. A grid bound to it is the first thing
that asks every role to actually answer, and a delegate naming a role that does
not exist is a runtime warning rather than a load failure, so it would otherwise
reach a user as blank tiles.

This loads Nocturne against the real backend, switches the page to its artists
face, and checks the grid comes up with as many cells as the library has artists.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine

from app.gui.gui_paths import bundled_gui_root
from app.main import CONTEXT_PROPERTIES, _register_qml_types
from app.models.track import Artist, Track
from app.viewmodels.artists_view_model import ArtistRoles


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

    from tests.fakes.providers import CHANNEL, id_rows

    lions = Artist(
        name="Seven Lions",
        external_id_rows=id_rows("artist", {CHANNEL: "UCfakeChannel0000000001A"}),
    )
    compound = Artist(name="person1/person2")
    session.add_all([lions, compound])
    session.flush()
    session.add_all([
        Track(title="Foolish Of Me", duration_seconds=187, artists=[lions]),
        Track(title="Rush Over", duration_seconds=200, artists=[lions]),
        Track(title="Promises", duration_seconds=240, artists=[compound]),
    ])
    session.commit()

    # The model was built against an empty database and these rows went in
    # through the session rather than through a service, so nothing on the event
    # bus told it they exist.
    registry.get_service("artists_view_model").refresh()

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


def test_the_artists_grid_renders_one_cell_per_artist(qt_app, backend):
    engine, root, warnings = _load(qt_app, backend)

    grid = root.findChild(object, "artistCollection")
    assert grid is not None, "LibraryView has no artistCollection"

    # Switch the page to its artists face, the way the Pill does.
    grid.setProperty("visible", True)
    qt_app.processEvents()

    assert grid.property("count") == 2, (
        "the grid did not bind to artistsVM:\n"
        + "\n".join(w.toString() for w in warnings)
    )

    del engine


def test_changing_the_sort_reorders_the_grid(qt_app, backend):
    """The seam the sort control crosses: a preference write has to reach the
    grid, and it only does because the model resets rather than mutating rows in
    place."""
    engine, root, warnings = _load(qt_app, backend)

    grid = root.findChild(object, "artistCollection")
    grid.setProperty("visible", True)
    qt_app.processEvents()

    vm = backend.get_service("artists_view_model")

    def names():
        return [
            vm.data(vm.index(row, 0), ArtistRoles.Name) for row in range(vm.rowCount())
        ]

    rebuilds: list = []
    vm.modelReset.connect(lambda: rebuilds.append(True))

    # The default the wall opens on: two tracks before one.
    assert names() == ["Seven Lions", "person1/person2"]

    vm.sortField = "name"
    vm.sortAscending = True
    qt_app.processEvents()

    assert names() == ["person1/person2", "Seven Lions"]
    assert rebuilds, "the grid was never told to rebuild"
    assert grid.property("count") == 2

    del engine


def test_the_delegate_reads_no_role_artistsvm_does_not_serve(qt_app, backend):
    """A delegate naming a missing role warns at runtime rather than failing to
    load, so it would otherwise ship as blank tiles nobody traced back here."""
    engine, root, warnings = _load(qt_app, backend)

    grid = root.findChild(object, "artistCollection")
    grid.setProperty("visible", True)
    qt_app.processEvents()

    fatal = [
        w.toString()
        for w in warnings
        if "ArtistDelegate" in w.toString()
        or "LibraryCard" in w.toString()
        or "LibraryEntityView" in w.toString()
        or "is not defined" in w.toString()
        or "Unable to assign" in w.toString()
    ]
    assert not fatal, "Artist collection binding errors:\n" + "\n".join(fatal)

    del engine
