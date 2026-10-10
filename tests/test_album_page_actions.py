"""The album page's two release actions, loaded for real.

Modelled on tests/test_album_search_rows.py, and it exists for the same reason:
a binding error in QML is a runtime warning nobody sees, and an action gated on a
property that does not exist is simply an action that never appears — neither of
which any Python-level test can notice.

What is worth pinning here is the gate. Both actions are meaningless without an
id *and* a plugin that can list a release's tracks, and an album that has both
is the *unusual* case, so a wrong condition shows up as "the feature does not
exist" rather than as an error. The plugin is a fake registry put in the album
editor's hands; the real host would need a process.

The two used to be ghost buttons in a hero that rode inside the track list's
lazily-instantiated `listHeader`. They are now rows in the page's overflow menu,
and the hero is pinned — so both the rows and the completeness mark below are
built as soon as the page is, and the laziness this file was written around is
gone. What it checks is unchanged: the condition, not the pixels.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine

from app.gui.gui_paths import bundled_gui_root
from app.main import CONTEXT_PROPERTIES, _register_qml_types
from app.models.track import Album, Artist, Track
from tests.fakes.providers import FakeMetadata, FakeRegistry, use_registry


ALBUM_VIEW = 11
BROWSE_ID = "MPREb_abcdefghijklm"


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

    artist = Artist(name="Daft Punk")
    session.add(artist)
    from tests.fakes.providers import RELEASE, id_rows

    identified = Album(name="Discovery", external_id_rows=id_rows("album", {RELEASE: BROWSE_ID}))
    anonymous = Album(name="Homemade Rips")
    session.add_all([identified, anonymous])
    session.flush()

    for title, album in (("One More Time", identified), ("Aerodynamic", identified),
                         ("Untitled", anonymous)):
        track = Track(title=title, duration_seconds=200, artists=[artist])
        track.albums = [album]
        session.add(track)
    session.commit()
    registry.get_service("albums_view_model").refresh()
    registry.fake_providers = FakeRegistry(metadata=FakeMetadata())
    use_registry(registry.get_service("album_edit_view_model"), registry.fake_providers)

    _register_qml_types(registry)
    registry.identified_id = int(identified.id)
    registry.anonymous_id = int(anonymous.id)
    return registry


def _load(qt_app, backend, album_id):
    """Open the album page on *album_id*, with its scrolling header laid out."""
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
    root = engine.rootObjects()[0]

    backend.get_service("album_edit_view_model").load(album_id)
    backend.get_service("album_tracks_view_model").load(album_id)
    # The page has to be the visible one or its table never lays out.
    root.setProperty("viewIndex", ALBUM_VIEW)
    for _ in range(8):
        qt_app.processEvents()

    return engine, root, warnings


def _row(root, name):
    return root.findChild(object, name)


def _open_menu(qt_app, root):
    """Put the album page's overflow menu up.

    Both actions live in it, and a menu row's ``visible`` is *effective*
    visibility — while the popup is closed every row in it reads false,
    including the ones whose own gate is true. So the menu has to be open before
    the gate can be read off it at all.
    """
    menu = root.findChild(object, "albumMenu")
    assert menu is not None, "the album page has no overflow menu"
    menu.setProperty("visible", True)
    for _ in range(8):
        qt_app.processEvents()
    return menu


def test_both_actions_are_there_for_an_identified_album(qt_app, backend):
    engine, root, warnings = _load(qt_app, backend, backend.identified_id)
    _open_menu(qt_app, root)

    find = _row(root, "albumFindTracks")
    reorder = _row(root, "albumReorder")
    assert find is not None, "no Find tracks row:\n" + "\n".join(
        w.toString() for w in warnings
    )
    assert reorder is not None
    assert find.property("visible") is True
    assert reorder.property("visible") is True

    del engine


def test_neither_is_offered_without_an_id(qt_app, backend):
    """There is nothing to ask, and a row that errors is worse than none."""
    engine, root, warnings = _load(qt_app, backend, backend.anonymous_id)
    _open_menu(qt_app, root)

    find = _row(root, "albumFindTracks")
    reorder = _row(root, "albumReorder")
    # Either the row was never instantiated, or it was and is hidden. Both are
    # correct; what must not happen is a live entry with no id behind it.
    assert find is None or find.property("visible") is False
    assert reorder is None or reorder.property("visible") is False

    del engine


def test_neither_is_offered_without_a_plugin_that_lists_releases(qt_app, backend):
    """The id is still there; what is missing is anything that can read it."""
    backend.fake_providers.set(metadata_provider=None)
    engine, root, warnings = _load(qt_app, backend, backend.identified_id)
    _open_menu(qt_app, root)

    find = _row(root, "albumFindTracks")
    reorder = _row(root, "albumReorder")
    assert find is None or find.property("visible") is False
    assert reorder is None or reorder.property("visible") is False

    del engine


def test_the_hero_row_binds_cleanly(qt_app, backend):
    """A typo in a view-model property is a warning, not a failure, without this."""
    engine, root, warnings = _load(qt_app, backend, backend.identified_id)

    fatal = [
        w.toString() for w in warnings
        if "is not defined" in w.toString()
        or "Unable to assign" in w.toString()
        or "Cannot read property" in w.toString()
    ]
    assert not fatal, "binding errors:\n" + "\n".join(fatal)

    del engine


# ---------------------------------------------------------------------------
# The completeness mark
# ---------------------------------------------------------------------------
#
# It lives in the same lazily-instantiated header as the two buttons above, and
# it fails the same silent way: a mark that stops being drawn leaves a page that
# looks entirely correct. Found through `findChild` on the objectName, because
# what matters first is that one got built at all.
#
# Read off `complete` rather than `visible`. `CompleteMark` sets one from the
# other and that is a tautology; what is worth pinning is the binding back to
# `albumVM`. `visible` would also drag in the page's cross-fade, which offscreen
# does not reliably finish — see the buttons above, which are flaky for exactly
# that reason.


def _marks(root):
    from PySide6.QtCore import QObject
    return root.findChildren(QObject, "completeMark")


def test_a_complete_album_shows_the_mark_beside_its_count(qt_app, backend):
    session = backend.get_service("db_session")
    session.get(Album, backend.identified_id).is_complete = True
    session.commit()

    engine, root, warnings = _load(qt_app, backend, backend.identified_id)

    marks = _marks(root)
    assert marks, "no completeness mark on the album page:\n" + "\n".join(
        w.toString() for w in warnings
    )
    assert any(m.property("complete") for m in marks)

    del engine


def test_an_incomplete_album_draws_nothing(qt_app, backend):
    """It is built either way — a Row child is not a Loader — but never marked."""
    engine, root, warnings = _load(qt_app, backend, backend.identified_id)

    assert _marks(root), "the mark must exist to be able to say it is off"
    assert not any(m.property("complete") for m in _marks(root))

    del engine
