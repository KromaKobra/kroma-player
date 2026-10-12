"""Sorting a track list from its column headers.

Three layers, each driven for real:

* `TrackColumns.nextSort` — the click cycle as pure arithmetic, evaluated in a
  bare QJSEngine so every step of every header's cycle is checked without a
  window.
* `TrackColumnHeader` — mouse clicks on a header over a real layout, asserting
  what it asks for and what it draws back: the chevron, its direction, and the
  stacked title header renaming itself.
* The Library — the same clicks reaching `libraryTracks` and reordering it.

The harness is written into the package directory for the header's relative
`import "TrackColumns.js"` to resolve, the trick `test_column_resize.py` uses.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from test_tile_size import _load, backend  # noqa: F401

TRACKS_DIR = (Path(__file__).resolve().parents[1]
              / "gui" / "nocturne" / "components" / "tracks")

# What `sortDirections(libraryVM.fields)` gives the library: words first click
# A to Z, measured values biggest or newest first.
DIRECTIONS = {
    "title": True, "artist": True, "album": True, "key": True, "isrc": True,
    "duration": False, "bpm": False, "date": False, "stashedDate": False,
}


# ------------------------------------------------------------------ the cycle


@pytest.fixture(scope="module")
def catalog():
    """TrackColumns.js, loaded as a plain script."""
    from PySide6.QtQml import QJSEngine

    engine = QJSEngine()
    source = (TRACKS_DIR / "TrackColumns.js").read_text()
    # `.pragma library` is a QML import directive, not JavaScript.
    result = engine.evaluate(source.replace(".pragma library", "", 1))
    assert not result.isError(), result.toString()

    def call(name, *args):
        fn = engine.globalObject().property(name)
        out = fn.call([engine.toScriptValue(a) for a in args])
        assert not out.isError(), out.toString()
        return out.toVariant()

    yield call
    del engine


def walk(catalog, keys, clicks, field="", ascending=False):
    """The (field, ascending) after each of `clicks` presses on one header."""
    steps = []
    for _ in range(clicks):
        nxt = catalog("nextSort", keys, field, ascending, DIRECTIONS)
        field, ascending = nxt["field"], nxt["ascending"]
        steps.append((field, ascending))
    return steps


def test_single_header_sorts_flips_then_turns_off(catalog):
    assert walk(catalog, ["album"], 4) == [
        ("album", True), ("album", False), ("", False), ("album", True),
    ]


def test_measured_values_sort_largest_first(catalog):
    assert walk(catalog, ["duration"], 3) == [
        ("duration", False), ("duration", True), ("", False),
    ]


def test_stacked_title_header_takes_five_clicks_to_come_back(catalog):
    assert walk(catalog, ["title", "artist"], 5) == [
        ("title", True), ("title", False),
        ("artist", True), ("artist", False),
        ("", False),
    ]


def test_a_new_header_starts_its_own_cycle(catalog):
    # Sorted by album, descending: the TIME header starts fresh, not mid-cycle.
    assert walk(catalog, ["duration"], 1, "album", False) == [("duration", False)]


def test_a_restored_flipped_sort_counts_as_the_second_step(catalog):
    # Title Z to A from settings or the grid menu: next is artist, not a repeat.
    assert walk(catalog, ["title", "artist"], 1, "title", False) == [("artist", True)]


def test_unsortable_keys_are_skipped_and_empty_headers_are_inert(catalog):
    assert catalog("sortableKeys", ["title", "genres", "artist"], DIRECTIONS) == [
        "title", "artist",
    ]
    assert walk(catalog, ["genres"], 1, "album", True) == [("album", True)]


def test_directions_come_from_the_catalog(catalog):
    fields = [
        {"key": "title", "type": "string", "sortable": True},
        {"key": "bpm", "type": "int", "sortable": True},
        {"key": "genres", "type": "string", "sortable": False},
    ]
    assert catalog("sortDirections", fields) == {"title": True, "bpm": False}


# ----------------------------------------------------------------- the header


HARNESS = """
import QtQuick
import Nocturne

Window {
    id: window
    width: 1300; height: 80; visible: true
    color: Theme.bg

    property var requests: []
    property string field: ""
    property bool ascending: false
    property var directions: %s

    TrackColumnLayout {
        id: layout
        profile: "library"
        settingsKey: ""
    }

    TrackColumnHeader {
        id: header
        objectName: "header"
        width: parent.width
        height: 24
        layout: layout
        sortDirections: window.directions
        sortField: window.field
        sortAscending: window.ascending
        onSortRequested: function (field, ascending) {
            window.requests = window.requests.concat([[field, ascending]])
            window.field = field
            window.ascending = ascending
        }
    }

    Component.onCompleted: layout.setBudget(width - 2 * header.sideMargin, Theme.gap)
}
"""


def descendant(root, name):
    """A named item anywhere in the visual tree. Repeater delegates are not
    QObject children of the header, so findChild cannot see them."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node.objectName() == name:
            return node
        stack.extend(node.childItems())
    raise AssertionError(f"no item named {name}")


@pytest.fixture
def make_header(qt_app):
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine

    built = []

    def build(directions=DIRECTIONS):
        import json

        harness = TRACKS_DIR / f"__ColumnSortHarness_{len(built)}.qml"
        harness.write_text(HARNESS % json.dumps(directions))
        built.append(harness)

        warnings: list[str] = []
        engine = QQmlApplicationEngine()
        engine.addImportPath(str(TRACKS_DIR.parents[1]))
        engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
        built.append(engine)
        engine.load(QUrl.fromLocalFile(str(harness)))
        assert engine.rootObjects(), warnings
        window = engine.rootObjects()[0]
        QTest.qWaitForWindowExposed(window)
        qt_app.processEvents()
        header = window.findChild(QQuickItem, "header")
        return window, header, warnings

    yield build

    for thing in reversed(built):
        if isinstance(thing, Path):
            thing.unlink(missing_ok=True)
        else:
            for root in thing.rootObjects():
                root.close()
    built.clear()


def centre(item):
    return item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()


def click(window, item):
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, centre(item))
    # Past the double-click interval, and past every header transition.
    QTest.qWait(260)


def requests(window):
    return [tuple(r) for r in window.property("requests").toVariant()]


def arrow_state(cell):
    arrow = descendant(cell, "sortArrow")
    return arrow.isVisible() and arrow.property("opacity") == 1, arrow.property("rotation")


def test_clicking_a_header_cycles_its_sort_and_draws_the_chevron(make_header):
    window, header, warnings = make_header()
    album = descendant(header, "columnHeader_album")
    assert arrow_state(album)[0] is False

    click(window, album)
    assert requests(window) == [("album", True)]
    assert arrow_state(album) == (True, 180)      # ascending points up

    click(window, album)
    assert requests(window)[-1] == ("album", False)
    assert arrow_state(album) == (True, 0)        # descending points down

    click(window, album)
    assert requests(window)[-1] == ("", False)
    assert arrow_state(album)[0] is False
    assert not warnings, warnings


def test_title_header_names_the_field_it_sorts_by(make_header):
    window, header, warnings = make_header()
    title = descendant(header, "columnHeader_title")
    label = descendant(title, "sortLabelText")
    assert label.property("text") == "TITLE"

    for _ in range(5):
        click(window, title)
    assert requests(window) == [
        ("title", True), ("title", False), ("artist", True), ("artist", False), ("", False),
    ]
    assert label.property("text") == "TITLE"

    window.setProperty("field", "artist")
    window.setProperty("ascending", True)
    QTest.qWait(260)
    assert label.property("text") == "ARTIST"
    assert arrow_state(title) == (True, 180)
    assert not warnings, warnings


def test_only_the_sorted_header_shows_a_chevron(make_header):
    window, header, _ = make_header()
    click(window, descendant(header, "columnHeader_duration"))
    assert requests(window) == [("duration", False)]
    assert arrow_state(descendant(header, "columnHeader_duration")) == (True, 0)
    for other in ("title", "album", "stashedDate"):
        assert arrow_state(descendant(header, "columnHeader_" + other))[0] is False


def test_a_drag_reorders_without_sorting(make_header):
    window, header, _ = make_header()
    layout = header.property("layout")
    before = layout.property("order").toVariant()
    album = descendant(header, "columnHeader_album")
    start = centre(album)
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start)
    # QTest.mouseMove sends no buttons, and a reorder only follows a move made
    # with the button held — so the moves are built by hand.
    for dx in (10, 60, 140, 200):
        pos = QPointF(start.x() + dx, start.y())
        QGuiApplication.sendEvent(window, QMouseEvent(
            QEvent.MouseMove, pos, window.mapToGlobal(pos),
            Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
        QTest.qWait(20)
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier,
                       QPointF(start.x() + 200, start.y()).toPoint())
    QTest.qWait(260)
    assert layout.property("order").toVariant() != before, "the drag did not reorder"
    assert requests(window) == []


def test_hovering_a_header_never_picks_it_up(make_header):
    """Sortable headers track hover to light their label, so they see moves
    with no button held — before any press, and after a click lets go. None
    of those may start a reorder."""
    window, header, _ = make_header()
    layout = header.property("layout")
    before = layout.property("order").toVariant()
    album = descendant(header, "columnHeader_album")
    start = centre(album)

    def sweep():
        for dx in (10, 60, 140, 200, 0):
            pos = QPointF(start.x() + dx, start.y())
            QGuiApplication.sendEvent(window, QMouseEvent(
                QEvent.MouseMove, pos, window.mapToGlobal(pos),
                Qt.NoButton, Qt.NoButton, Qt.NoModifier))
            QTest.qWait(20)
            assert header.property("isDragging") is False

    sweep()
    click(window, album)
    sweep()
    assert layout.property("order").toVariant() == before
    assert requests(window) == [("album", True)]


def test_hiding_the_sorted_column_drops_its_sort(make_header):
    window, header, _ = make_header()
    click(window, descendant(header, "columnHeader_album"))
    assert requests(window) == [("album", True)]
    # The right-click menu's ALBUM row, triggered as a click on it would.
    rows = [r for r in header.findChildren(QObject)
            if r.metaObject().className().startswith("MenuRow")
            and r.property("text") == "ALBUM"]
    assert rows, "column menu has no ALBUM row"
    rows[0].triggered.emit()
    QTest.qWait(50)
    assert requests(window)[-1] == ("", False)
    assert "album" not in header.property("layout").property("order").toVariant()


def test_a_list_without_sort_directions_does_not_sort(make_header):
    window, header, _ = make_header(directions={})
    click(window, descendant(header, "columnHeader_title"))
    click(window, descendant(header, "columnHeader_album"))
    assert requests(window) == []


# ---------------------------------------------------------------- the library


def library_header(window):
    table = window.findChild(QQuickItem, "libraryTable")
    return table, descendant(table, "columnHeader_stashedDate"), descendant(table, "columnHeader_title")


def test_library_headers_reorder_the_songs(qt_app, backend):  # noqa: F811
    engine, window, warnings = _load(qt_app, backend)
    window.findChild(QQuickItem, "navRail").libraryNavigated.emit(0)
    QTest.qWait(250)
    table, added, title = library_header(window)
    query = window.findChild(QObject, "libraryTracks")

    def ids():
        return [query.trackIdAt(i) for i in range(query.property("count"))]

    # Nothing chosen: newest first, and no arrow on ADDED to say so.
    assert table.property("sortField") == ""
    assert (query.property("sortField"), query.property("sortAscending")) == ("stashedDate", False)
    newest_first = ids()
    assert arrow_state(added)[0] is False

    click(window, added)
    assert (table.property("sortField"), table.property("sortAscending")) == ("stashedDate", False)
    assert arrow_state(added) == (True, 0)

    click(window, added)
    QTest.qWait(50)
    assert query.property("sortAscending") is True
    assert ids() == list(reversed(newest_first))

    click(window, added)
    assert table.property("sortField") == ""
    assert (query.property("sortField"), query.property("sortAscending")) == ("stashedDate", False)
    assert arrow_state(added)[0] is False

    click(window, title)
    QTest.qWait(50)
    titles = [query.trackIdAt(i) for i in range(query.property("count"))]
    assert query.property("sortField") == "title"
    assert titles == sorted(titles)       # "Track 0".."Track 7", inserted in order

    assert backend.get_service("settings_view_model").value("librarySort", None) == "title"
    table.sortRequested.emit("", False)
    assert not warnings, [w.toString() for w in warnings]
    window.close()
    del engine
