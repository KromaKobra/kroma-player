"""The artist page's banner and its overflow menu, loaded for real.

The sibling of tests/test_album_page_actions.py, and it exists for the same
reason: the page is QML, so a typo in a view-model property is a runtime warning
nobody sees, and a menu row gated on a property that does not exist is simply a
row that never appears. Neither shows up in a Python-level test of the view
models underneath.

What is pinned here is the delta the artist page adds to the shared banner —
`DetailHero` — over what the album page already exercises: the circular portrait,
the identified mark, and the attention pip that tells the user the fix for a
split-looking name is inside the menu.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, QUrl
from PySide6.QtQml import QQmlApplicationEngine

from app.gui.gui_paths import bundled_gui_root
from app.main import CONTEXT_PROPERTIES, _register_qml_types
from app.models.track import Artist, Track
from tests.fakes.providers import FakeMetadata, FakeRegistry, use_registry


ARTIST_VIEW = 9
CHANNEL_ID = "UCabcdefghijklmnopqrstuv"


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

    identified = Artist(name="Daft Punk", external_id_rows=id_rows("artist", {CHANNEL: CHANNEL_ID}))
    # A name the split check reads as two people — the one repair the artist
    # page surfaces outside the editor.
    ambiguous = Artist(name="Sonny/Cher")
    session.add_all([identified, ambiguous])
    session.flush()

    for title, artist in (("One More Time", identified), ("Aerodynamic", identified),
                          ("I Got You Babe", ambiguous)):
        session.add(Track(title=title, duration_seconds=200, artists=[artist]))
    session.commit()
    registry.get_service("artists_view_model").refresh()
    # The identified mark speaks for whichever enabled plugin declares the id —
    # so a plugin has to be on for there to be anything to mark.
    registry.fake_providers = FakeRegistry(metadata=FakeMetadata())
    use_registry(registry.get_service("artist_edit_view_model"), registry.fake_providers)

    _register_qml_types(registry)
    registry.identified_id = int(identified.id)
    registry.ambiguous_id = int(ambiguous.id)
    return registry


def _load(qt_app, backend, artist_id):
    """Open the artist page on *artist_id*, laid out."""
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

    backend.get_service("artist_edit_view_model").load(artist_id)
    root.setProperty("viewIndex", ARTIST_VIEW)
    for _ in range(8):
        qt_app.processEvents()

    return engine, root, warnings


def test_the_banner_binds_cleanly(qt_app, backend):
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


def test_an_identified_artist_is_marked(qt_app, backend):
    """The sentence that used to say this became a glyph; it must still be drawn."""
    engine, root, warnings = _load(qt_app, backend, backend.identified_id)

    marks = root.findChildren(QObject, "identifiedMark")
    assert marks, "no identified mark on the artist page:\n" + "\n".join(
        w.toString() for w in warnings
    )
    assert any(m.property("identified") for m in marks)

    del engine


def test_an_unidentified_artist_draws_nothing(qt_app, backend):
    """It is built either way — a Row child is not a Loader — but never marked."""
    engine, root, warnings = _load(qt_app, backend, backend.ambiguous_id)

    marks = root.findChildren(QObject, "identifiedMark")
    assert marks, "the mark must exist to be able to say it is off"
    assert not any(m.property("identified") for m in marks)

    del engine


def test_an_id_no_enabled_plugin_declares_is_not_marked(qt_app, backend):
    """The id is still on the row; with its plugin off there is no one to name."""
    backend.fake_providers.set(kinds=[])
    engine, root, warnings = _load(qt_app, backend, backend.identified_id)

    marks = root.findChildren(QObject, "identifiedMark")
    assert marks
    assert not any(m.property("identified") for m in marks)

    del engine


def test_a_split_looking_name_points_at_the_menu(qt_app, backend):
    """The repair moved into the menu, so something has to say the menu holds it.

    Read off the pip rather than the menu row: the row is always there, and what
    changes with a bad name is whether the page asks you to go and look.
    """
    engine, root, warnings = _load(qt_app, backend, backend.ambiguous_id)

    pips = root.findChildren(QObject, "menuAlertPip")
    assert pips, "no attention pip in the banner:\n" + "\n".join(
        w.toString() for w in warnings
    )
    assert any(p.property("visible") for p in pips)

    del engine


def test_a_clean_name_is_not_flagged(qt_app, backend):
    engine, root, warnings = _load(qt_app, backend, backend.identified_id)

    pips = root.findChildren(QObject, "menuAlertPip")
    assert pips, "the pip must exist to be able to say it is off"
    assert not any(p.property("visible") for p in pips)

    del engine
