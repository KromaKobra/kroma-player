"""Dragging an album chip, and what the head of the list means.

The sibling of tests/test_artist_chips.py, against the same `EntityChips`
machinery through the other wrapper. Most of it is the same gesture and would be
covered twice; what is only true here is the last three tests.

An artist field's order is a credit order — a preference about how the names
read. An album field's head is the record the track *belongs to*, which decides
the artwork it carries and the name its `album` shows. So the first chip is
marked, the marker has to follow a drag rather than stay at slot zero, and the
staged set has to come back out as the `{id, name}` refs `editVM.setAlbums`
takes: an album that does not exist yet is created by name **and** lead artist,
so the name is not merely the display of the id.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtQml import QQmlComponent, QQmlEngine  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402

from app.main import CONTEXT_PROPERTIES  # noqa: E402
from app.models.track import Album, Artist, Track  # noqa: E402

NOCTURNE = REPO_ROOT / "gui" / "nocturne"


@pytest.fixture
def backend(qt_app, tmp_path):
    """A registry over a throwaway vault holding one track on three albums."""
    from app.utils.library_paths import set_library_root

    vault = tmp_path / "venomvault"
    (vault / ".data").mkdir(parents=True)
    set_library_root(vault)

    from app.bootstrap import create_service_registry

    registry = create_service_registry()
    session = registry.get_service("db_session")

    artist = Artist(name="Someone")
    albums = [Album(name=name) for name in ("Adore", "Bloom", "Coda")]
    session.add(artist)
    session.add_all(albums)
    session.flush()

    track = Track(title="One", duration_seconds=200, artists=[artist])
    track.albums = albums
    session.add(track)
    session.commit()
    registry.get_service("albums_view_model").refresh()
    return registry


@pytest.fixture
def chips(qt_app, backend):
    """A laid-out AlbumChips, its `changed` payloads, and the album ids."""
    engine = QQmlEngine()
    engine.addImportPath(str(NOCTURNE))
    context = engine.rootContext()
    for qml_name, service in CONTEXT_PROPERTIES.items():
        context.setContextProperty(qml_name, backend.get_service(service))

    warnings: list = []
    engine.warnings.connect(lambda w: warnings.extend(w))

    component = QQmlComponent(
        engine, QUrl.fromLocalFile(str(NOCTURNE / "components" / "AlbumChips.qml"))
    )
    item = component.create()
    assert item is not None, component.errorString()

    # Wide enough that three chips share one line, so a horizontal drag is the
    # whole gesture — the same bargain the artist test makes.
    item.setWidth(800)
    item.setHeight(120)

    emitted: list = []
    item.changed.connect(
        lambda ids, names: emitted.append((_js(ids), _js(names)))
    )

    repo = backend.get_service("album_repository")
    ids = [
        int(repo.get_by_name_insensitive(n).id) for n in ("Adore", "Bloom", "Coda")
    ]
    item.setFromIds(ids, "")
    _settle(qt_app, item)

    yield item, emitted, ids, warnings
    del engine


def _settle(qt_app, item):
    """Let the Flow lay its children out before anything asks where they are."""
    for _ in range(3):
        qt_app.processEvents()
        item.polish()
        qt_app.processEvents()


def _flow(item) -> QQuickItem:
    return item.findChild(QQuickItem, "chipFlow")


def _js(value):
    return value.toVariant() if hasattr(value, "toVariant") else value


def _model_names(item) -> list:
    return list(_js(item.staged())["names"])


def _chips_in_order(item) -> list:
    flow = _flow(item)
    assert flow is not None, "AlbumChips no longer reaches an object-named chipFlow"
    kids = [c for c in flow.childItems() if c.property("entityId") is not None]
    kids.sort(key=lambda c: (round(c.y()), c.x()))
    return kids


def _drag(qt_app, item, source, target):
    """Press the chip at *source* and let go over the one at *target*."""
    item._beginDrag(source)
    landing = _chips_in_order(item)[target]
    x = landing.x() + (landing.width() * 0.9 if target > source else landing.width() * 0.1)
    item._dragTo(x, landing.y() + landing.height() / 2)
    _settle(qt_app, item)
    item._endDrag(True)
    _settle(qt_app, item)


def test_the_chips_load_in_the_tracks_own_album_order(chips):
    item, emitted, ids, warnings = chips
    assert _model_names(item) == ["Adore", "Bloom", "Coda"], (
        "AlbumChips did not fill:\n" + "\n".join(w.toString() for w in warnings)
    )
    assert emitted == [], "filling the field is not an edit and must not report one"


def test_dragging_an_album_to_the_front_changes_the_primary(qt_app, chips):
    """The gesture that says "this is the record it belongs to"."""
    item, emitted, ids, _ = chips

    _drag(qt_app, item, 2, 0)

    assert _model_names(item) == ["Coda", "Adore", "Bloom"]
    assert len(emitted) == 1, "the order is committed once, on release"
    assert emitted[0][0] == [ids[2], ids[0], ids[1]]


def test_the_primary_marker_follows_the_drag(qt_app, chips):
    """Marked by live index, not by a snapshot taken when the chips were built.

    Bound to a stale index the badge would stay on the chip that used to be
    first, and the field would say the opposite of what saving is about to do.
    """
    item, _, _, _ = chips
    assert [c.property("isPrimary") for c in _chips_in_order(item)] == [
        True, False, False
    ]

    _drag(qt_app, item, 2, 0)

    ordered = _chips_in_order(item)
    assert [c.property("name") for c in ordered][0] == "Coda"
    assert [c.property("isPrimary") for c in ordered] == [True, False, False]


def test_staged_refs_carry_the_names_beside_the_ids(qt_app, chips):
    """What `editVM.setAlbums` takes, and why it is not the bare ids.

    Two unrelated records really are both called "Greatest Hits", so an album is
    resolved by name *and* lead artist. A chip for an album that does not exist
    yet has an id of 0 and nothing but its name to be created from.
    """
    item, _, ids, _ = chips

    refs = _js(item.stagedRefs())
    assert [r["id"] for r in refs] == ids
    assert [r["name"] for r in refs] == ["Adore", "Bloom", "Coda"]


def test_the_refs_survive_the_crossing_into_the_view_model(qt_app, backend, tmp_path):
    """`setAlbums` takes a JS array of objects, which is not a Python list of dicts.

    The one seam in this feature that fails silently rather than loudly: a slot
    typed `QVariantList` that received something it did not recognise would drop
    every entry and stage an empty album list, and every unit test on either side
    of the crossing would still pass. So it is driven from QML, as the editor
    drives it.
    """
    session = backend.get_service("db_session")
    track = session.query(Track).first()
    ids = [int(a.id) for a in track.albums]

    engine = QQmlEngine()
    engine.addImportPath(str(NOCTURNE))
    context = engine.rootContext()
    for qml_name, service in CONTEXT_PROPERTIES.items():
        context.setContextProperty(qml_name, backend.get_service(service))

    edit_vm = backend.get_service("track_edit_view_model")
    edit_vm.load(int(track.id))

    driver = tmp_path / "Driver.qml"
    driver.write_text(
        "import QtQuick\n"
        "Item {\n"
        "    function go() {\n"
        f'        editVM.setAlbums([{{ id: {ids[2]}, name: "Coda" }},\n'
        '                          { id: 0, name: "Typed In" }])\n'
        "    }\n"
        "}\n"
    )
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(driver)))
    item = component.create()
    assert item is not None, component.errorString()

    item.go()

    assert list(edit_vm.albumIds) == [ids[2], 0]
    assert edit_vm.primaryAlbumName == "Coda"
    assert edit_vm.isDirty
    # No cover on either album, so there is nothing for the prompt to warn about.
    assert edit_vm.artworkWillChange is False
    del engine
