"""The album page's artwork toggle and its reorder mode, loaded for real.

Modelled on tests/test_album_page_actions.py, and it exists for the same reason:
both of these are QML-only features — no backend property, no signal, nothing a
Python-level test can reach — so a binding written against a property that does
not exist would show up as "the feature quietly does nothing" rather than as an
error anyone sees.

Three rules are worth pinning, and they are the three that would be easy to
regress without noticing:

*The artwork is off unless asked for.* The row thumbnail used to be the only way
to play a track from this page, so hiding it by default is only safe because the
number slot grows a play button in its place. A change that put the artwork back
on by default would not fail anything else in the suite.

*Reordering is a mode.* `reorderable` was hardcoded true here, which meant every
row's number was a live drag handle and a stray gesture rewrote the running order
silently. It is now the page's own `reorderMode`, and the thing most likely to
break is not the arming but the disarming — a mode that survives leaving the page
is the accident it was introduced to prevent.

*The two reorders have different names.* "Reorder from <source>" replaces what was
just "Reorder" precisely because the new row needed that word. If the rename is
reverted the menu has two identical entries doing unrelated things.
"""
from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine

from app.gui.gui_paths import bundled_gui_root
from app.main import CONTEXT_PROPERTIES, _register_qml_types
from app.models.track import Album, Artist, Track


ALBUM_VIEW = 11
LIBRARY_VIEW = 4


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

    artist = Artist(name="Radiohead")
    session.add(artist)
    album = Album(name="Kid A")
    session.add(album)
    session.flush()

    # More than one, because the reorder row is gated on there being an order
    # worth changing at all.
    for title in ("Everything In Its Right Place", "Kid A", "The National Anthem"):
        track = Track(title=title, duration_seconds=240, artists=[artist])
        track.albums = [album]
        session.add(track)
    session.commit()
    registry.get_service("albums_view_model").refresh()

    _register_qml_types(registry)
    registry.album_id = int(album.id)
    return registry


def _load(qt_app, backend):
    """Open the album page, with its table laid out."""
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

    backend.get_service("album_edit_view_model").load(backend.album_id)
    backend.get_service("album_tracks_view_model").load(backend.album_id)
    # The page has to be the visible one or its table never lays out.
    root.setProperty("viewIndex", ALBUM_VIEW)
    _settle(qt_app)

    return engine, root, warnings


def _settle(qt_app, rounds=8):
    for _ in range(rounds):
        qt_app.processEvents()


def _table(root):
    table = root.findChild(object, "albumTable")
    assert table is not None, "the album page's track table is gone or renamed"
    return table


def _row(root, name):
    return root.findChild(object, name)


def _number_slot(table, row=0):
    """The lead slot of *row*, found by the one property only it has.

    `playAction` is unique to it in the whole tree, which beats adding an
    objectName to something instantiated once per visible row — and beats
    computing the slot's position from the row's insets, which would make this
    test fail on a margin change that broke nothing.

    Walked with `childItems()` rather than `children()`: a ListView parents its
    delegates to `contentItem` as *items*, and they do not appear in the QObject
    child list at all. And addressed by `rowIndex` rather than by position in
    that list, because a ListView pools and reuses delegates — the order it
    hands them back is the recycler's, not the list's.
    """
    listview = table.findChild(object, "trackList")
    assert listview is not None, "the track list is gone or renamed"

    def walk(item):
        for child in item.childItems():
            yield child
            yield from walk(child)

    rows = {
        d.property("rowIndex"): d
        for d in listview.property("contentItem").childItems()
        if d.property("rowIndex") is not None
    }
    assert row in rows, f"row {row} is not realised; have {sorted(rows)}"

    slots = [c for c in walk(rows[row]) if c.property("playAction") is not None]
    assert slots, "no number slot in the row — the lead slot lost `playAction`"
    return slots[0]


def _open_menu(qt_app, root):
    """Put the album page's overflow menu up.

    A menu row's ``visible`` is *effective* visibility — while the popup is
    closed every row in it reads false, including the ones whose own gate is
    true. So the menu has to be open before anything can be read off it.
    """
    menu = root.findChild(object, "albumMenu")
    assert menu is not None, "the album page has no overflow menu"
    menu.setProperty("visible", True)
    _settle(qt_app)
    return menu


def test_the_artwork_is_hidden_and_the_order_locked_on_a_fresh_install(qt_app, backend):
    """Both defaults at once: they are the two halves of the same decision.

    Hiding the artwork is only safe because the number slot takes over the play
    button, and locking the order is only tolerable because the artwork being
    gone is what freed that slot up. A regression in either one is a regression
    in the pair.
    """
    engine, root, warnings = _load(qt_app, backend)
    table = _table(root)

    assert table.property("showArt") is False, (
        "the album page is repeating the sleeve down every row:\n"
        + "\n".join(w.toString() for w in warnings)
    )
    assert table.property("reorderable") is False, (
        "every row's number is a live drag handle again"
    )
    # The number column stays — it is what the play button appears in.
    assert table.property("showIndex") is True

    del engine


def test_showing_the_artwork_survives_the_app(qt_app, backend, tmp_path):
    """The toggle flips the table and reaches settings.json, not just memory."""
    engine, root, _ = _load(qt_app, backend)
    _open_menu(qt_app, root)

    toggle = _row(root, "albumArtworkToggle")
    assert toggle is not None, "no artwork row in the album menu"
    assert toggle.property("text") == "Show artwork"

    toggle.triggered.emit()
    _settle(qt_app)

    assert _table(root).property("showArt") is True
    # The row now offers the way back, which is the whole of its state.
    assert toggle.property("text") == "Hide artwork"

    backend.get_service("settings_view_model").sync()

    from app.settings.file import settings_path

    stored = json.loads(settings_path().read_text())
    assert stored["gui"]["nocturne"]["albumArtwork"] is True, (
        f"the choice never reached settings.json: {stored.get('gui')}"
    )

    del engine


def test_reordering_is_a_mode_that_does_not_outlive_the_page(qt_app, backend):
    engine, root, _ = _load(qt_app, backend)
    _open_menu(qt_app, root)

    reorder = _row(root, "albumReorderMode")
    assert reorder is not None, "no reorder row in the album menu"
    assert reorder.property("text") == "Reorder"
    assert reorder.property("enabled") is True, "three tracks is an order worth changing"

    reorder.triggered.emit()
    _settle(qt_app)

    assert _table(root).property("reorderable") is True
    assert reorder.property("text") == "Done reordering"

    # Leaving the page is as good as saying you are done — and every drop has
    # already been written by then, so there is nothing here to strand.
    root.setProperty("viewIndex", LIBRARY_VIEW)
    _settle(qt_app)

    assert _table(root).property("reorderable") is False, (
        "the album page came back still armed for dragging"
    )

    del engine


def test_the_number_slot_carries_the_play_button_the_artwork_gave_up(qt_app, backend):
    """The half of the feature that makes hiding the artwork survivable.

    The row thumbnail is what a track is played from; hiding it without moving
    that action would leave the page with no way to play a row but a
    double-click, which nothing on screen advertises. So the slot showing the
    number takes the artwork's action over verbatim — and hands it back the
    moment the artwork returns, rather than offering two buttons for one job.
    """
    engine, root, _ = _load(qt_app, backend)
    table = _table(root)

    assert _number_slot(table).property("playAction") == "play", (
        "the artwork is hidden and nothing took over its play button"
    )

    # Artwork back on: it draws its own button again, so this one stands down.
    _open_menu(qt_app, root)
    _row(root, "albumArtworkToggle").triggered.emit()
    _settle(qt_app)

    assert _number_slot(table).property("playAction") == "", (
        "both the artwork and the number slot are offering to play the row"
    )

    del engine


def test_pressing_the_number_slot_plays_that_track(qt_app, backend):
    """The button is only worth having if the click behind it is wired up."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    engine, root, _ = _load(qt_app, backend)
    table = _table(root)
    slot = _number_slot(table, row=0)

    queue = backend.get_service("queue_manager")
    assert queue.current() is None, "something was already playing"

    centre = slot.mapToScene(QPoint(int(slot.property("width") / 2),
                                    int(slot.property("height") / 2)))
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, centre.toPoint())
    _settle(qt_app, 30)

    # The audio never starts — these tracks have no files behind them — but the
    # queue is what the click is responsible for, and it is what the artwork
    # button would have moved too.
    current = queue.current()
    assert current is not None, (
        "clicking the number slot queued nothing; the play button is not wired up"
    )
    first = backend.get_service("album_tracks_view_model").trackIds()[0]
    assert int(current["id"]) == int(first), (
        f"the wrong row played: queued {current['id']}, row 0 is {first}"
    )

    del engine


def test_the_release_reorder_says_where_the_order_comes_from(qt_app, backend):
    """Two rows in one menu both called "Reorder" would be a coin toss.

    The name is the plugin's — whatever it calls the catalogue the order is
    read from — so the album needs an id and a plugin that can list releases.
    """
    from tests.fakes.providers import RELEASE, FakeMetadata, FakeRegistry, set_id, use_registry

    session = backend.get_service("db_session")
    album = session.get(Album, backend.album_id)
    set_id(album, RELEASE, "MPREb_kidakidakida")
    session.commit()
    editor = backend.get_service("album_edit_view_model")
    use_registry(editor, FakeRegistry(metadata=FakeMetadata()))

    engine, root, _ = _load(qt_app, backend)
    _open_menu(qt_app, root)

    reorder = _row(root, "albumReorder")
    assert reorder is not None
    assert editor.refreshLabel != ""
    assert reorder.property("text") == "Reorder from " + editor.refreshLabel

    del engine
