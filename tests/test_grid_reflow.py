"""Presentation stays continuous while the virtualized grid commits new geometry."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, QPointF, QUrl, QMetaObject, Qt
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest


class LargeModel(QAbstractListModel):
    def rowCount(self, parent=None):  # noqa: N802
        return 0 if parent is not None and parent.isValid() else 10000

    def data(self, index, role):
        return None

    def move(self, source, destination):
        self.beginMoveRows(QModelIndex(), source, source, QModelIndex(),
                           destination + (destination > source))
        self.endMoveRows()


@pytest.fixture
def wall(qt_app, tmp_path):
    from app.gui.gui_paths import bundled_gui_root

    directory = bundled_gui_root() / "nocturne"
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(directory))
    model = LargeModel()
    engine.rootContext().setContextProperty("entries", model)
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
    harness = tmp_path / "ReflowHarness.qml"
    harness.write_text(f'''
import QtQuick
import Nocturne
import "{(directory / 'components' / 'library').as_uri()}"
Window {{
    width: 800; height: 600; visible: true
    property bool reducedMotion: false
    property Item mask: grid.artMask
    Binding {{ target: Theme.prefs; property: "reducedMotion"; value: reducedMotion }}
    LibraryGrid {{
        id: grid
        objectName: "grid"
        width: parent.width; height: parent.height
        model: entries
        delegate: LibraryCard {{
            required property int index
            rowIndex: index
            displayTitle: "Tile " + index
            placeholderGlyph: false
        }}
    }}
}}
''')
    engine.load(QUrl.fromLocalFile(str(harness)))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    grid = window.findChild(QObject, "grid")
    QTest.qWait(100)

    def tile(index):
        return next(item for item in grid.property("contentItem").childItems()
                    if item.property("rowIndex") == index)

    def layout():
        assert QMetaObject.invokeMethod(grid, "forceLayout", Qt.DirectConnection)

    yield window, grid, tile, layout
    window.close()
    assert not warnings, warnings
    del engine


def rect(tile):
    start = tile.mapToScene(QPointF(0, 0))
    end = tile.mapToScene(QPointF(tile.width(), tile.height()))
    return start.x(), start.y(), end.x() - start.x(), end.y() - start.y()


def destination(tile):
    return tile.x(), tile.y(), tile.width(), tile.height()


def test_wrapping_and_resizing_start_at_the_current_presentation(wall):
    window, grid, tile, layout = wall
    wrapping, neighbor = tile(3), tile(1)
    before = rect(wrapping)
    neighbor_before = rect(neighbor)
    grid.setProperty("tileSize", 280)
    layout()
    assert grid.property("columns") == 2
    assert wrapping.x() == 400 and wrapping.y() == 448
    assert rect(wrapping) == pytest.approx(before, abs=0.01)
    assert rect(neighbor) == pytest.approx(neighbor_before, abs=0.01)
    mask = window.property("mask")
    final_mask_size = (mask.width(), mask.height())
    QTest.qWait(80)
    during = rect(wrapping)
    assert 400 < during[0] < before[0]
    assert before[1] < during[1] < 448
    assert before[2] < during[2] < 400
    assert wrapping.width() == 400
    assert (mask.width(), mask.height()) == final_mask_size
    QTest.qWait(650)
    assert rect(wrapping) == pytest.approx(destination(wrapping), abs=0.01)


def test_reversal_keeps_the_current_presentation_and_settles_at_the_latest_target(wall):
    _, grid, tile, layout = wall
    entry = tile(3)
    grid.setProperty("tileSize", 280)
    layout()
    QTest.qWait(80)
    before = rect(entry)
    grid.setProperty("tileSize", 200)
    layout()
    assert rect(entry) == pytest.approx(before, abs=0.01)
    # The next frame continues smoothly; it must not restart at either layout.
    QTest.qWait(20)
    after = rect(entry)
    assert abs(after[0] - before[0]) < 50
    assert abs(after[1] - before[1]) < 100
    QTest.qWait(650)
    assert rect(entry) == pytest.approx(destination(entry), abs=0.01)


def test_reduced_motion_finishes_in_flight_motion_and_skips_later_reflows(wall):
    window, grid, tile, layout = wall
    entry = tile(3)
    grid.setProperty("tileSize", 280)
    layout()
    QTest.qWait(60)
    assert rect(entry) != pytest.approx(destination(entry), abs=1)
    window.setProperty("reducedMotion", True)
    assert rect(entry) == pytest.approx(destination(entry), abs=0.01)
    grid.setProperty("tileSize", 200)
    layout()
    assert rect(entry) == pytest.approx(destination(entry), abs=0.01)


def test_viewport_resize_uses_the_same_continuity(wall):
    _, grid, tile, layout = wall
    entry = tile(3)
    before = rect(entry)
    grid.setWidth(600)
    layout()
    assert grid.property("columns") == 3
    assert rect(entry) == pytest.approx(before, abs=0.01)
    QTest.qWait(650)
    assert rect(entry) == pytest.approx(destination(entry), abs=0.01)


def test_new_and_hidden_tiles_do_not_animate_and_large_models_stay_virtualized(wall):
    window, grid, tile, layout = wall
    assert rect(tile(3)) == pytest.approx(destination(tile(3)), abs=0.01)
    assert len(grid.property("contentItem").childItems()) < 100
    window.hide()
    grid.setProperty("tileSize", 280)
    layout()
    assert rect(tile(3)) == pytest.approx(destination(tile(3)), abs=0.01)


def test_model_moves_glide_from_the_existing_position(wall):
    _, grid, tile, layout = wall
    entry = tile(3)
    before = rect(entry)
    grid.property("model").move(0, 4)
    layout()
    assert entry.property("rowIndex") == 2
    assert rect(entry) == pytest.approx(before, abs=0.01)
    QTest.qWait(650)
    assert rect(entry) == pytest.approx(destination(entry), abs=0.01)


def test_scrolling_recycles_tiles_without_motion_from_their_old_identity(wall):
    _, grid, tile, layout = wall
    first = tile(3)
    grid.setProperty("tileSize", 280)
    layout()
    QTest.qWait(40)
    grid.setProperty("contentY", 200 * grid.property("cellHeight"))
    layout()
    QTest.qWait(40)
    # A long jump must not allocate a delegate for every intervening row.
    children = grid.property("contentItem").childItems()
    assert len(children) < 100
    visible = [entry for entry in children if entry.property("rowIndex") is not None
               and entry.y() < grid.property("contentY") + grid.height()
               and entry.y() + entry.height() > grid.property("contentY")]
    assert visible
    for entry in visible:
        assert entry.mapToItem(entry.parentItem(), QPointF(0, 0)) == QPointF(entry.x(), entry.y())
        assert rect(entry)[2:] == pytest.approx((entry.width(), entry.height()), abs=0.01)
    # Retaining the wrapper also catches accidental reuse of the old transform.
    if first in visible:
        assert first.property("rowIndex") >= 400
