"""Joined grid selections through real models, resizing and delegate reuse."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtTest import QTest

from test_track_selection_qml import call, table  # noqa: F401


@pytest.fixture
def grid(table):
    root, _, model, _, _, select, settle = table
    root.setProperty("tileSize", 200)
    root.setProperty("gridMode", True)
    settle()
    view = root.findChild(QObject, "songGridView")
    assert view is not None
    assert view.property("columns") == 4
    cells = {}

    def cell(index):
        view.setProperty("currentIndex", index)
        settle()
        for _ in range(50):
            current = view.property("currentItem")
            if current is not None and current.property("rowIndex") == index:
                # Keep the PySide wrapper alive while inspecting its children.
                cells[index] = current
                return current
            QTest.qWait(20)
        pytest.fail(f"grid did not create cell {index}")

    def background(index):
        return cell(index).findChild(QObject, "entrySelectionBackground")

    yield root, view, model, cell, background, select, settle


@pytest.mark.parametrize("indices,corners", [
    ([0], {0: (True, True, True, True)}),
    ([0, 1], {0: (True, False, True, False), 1: (False, True, False, True)}),
    ([0, 4], {0: (True, True, False, False), 4: (False, False, True, True)}),
    ([0, 1, 4, 5], {0: (True, False, False, False), 1: (False, True, False, False),
                     4: (False, False, True, False), 5: (False, False, False, True)}),
    # L shapes retain their exposed corner; diagonal cells stay separate.
    ([0, 1, 4], {0: (True, False, False, False), 1: (False, True, False, True),
                4: (False, False, True, True)}),
    ([0, 5], {0: (True, True, True, True), 5: (True, True, True, True)}),
    # Consecutive model positions across a row boundary are not neighbors.
    ([3, 4], {3: (True, True, True, True), 4: (True, True, True, True)}),
])
def test_grid_rounds_only_exposed_corners(grid, indices, corners):
    _, view, _, cell, background, select, _ = grid
    select(*indices)
    for index, rounded in corners.items():
        entry = cell(index)
        rect = background(index)
        for name, expected in zip(
            ("topLeftRadius", "topRightRadius", "bottomLeftRadius", "bottomRightRadius"), rounded
        ):
            assert (rect.property(name) > 0) is expected
        # Joined backgrounds reach the exact cell boundary without overlapping
        # the translucent fill of their neighbor.
        col = index % view.property("columns")
        left = col > 0 and index - 1 in indices
        right = col < view.property("columns") - 1 and index + 1 in indices
        above = index - view.property("columns") in indices
        below = index + view.property("columns") in indices
        assert rect.x() == (0 if left else 3)
        assert rect.y() == (0 if above else 3)
        assert rect.x() + rect.width() == entry.width() - (0 if right else 3)
        assert rect.y() + rect.height() == entry.height() - (0 if below else 3)


def test_grid_selection_updates_after_backend_changes_and_resize(grid):
    _, view, model, _, background, _, settle = grid
    model.setSelectedMultiple([1000, 1004])
    settle()
    assert background(0).property("bottomLeftRadius") == 0
    model.setSelectedMultiple([1000, 1001, 1004])
    settle()
    assert background(0).property("topRightRadius") == 0
    # Resize to two columns. Position 4 is no longer directly below 0.
    model.selected_rows_reads = 0
    view.setProperty("width", 450)
    settle()
    assert view.property("columns") == 2
    assert background(0).property("bottomLeftRadius") > 0
    assert model.selected_rows_reads == 0
    model.clearSelection()
    settle()
    assert background(0).property("topRightRadius") > 0
    assert background(0).property("x") == 3


def test_grid_refreshes_positions_after_insert_remove_move_and_reset(grid):
    _, _, model, _, background, select, settle = grid
    select(0, 1)
    model.insert(1, 2000)
    settle()
    assert background(0).property("topRightRadius") > 0
    model.remove(1)
    settle()
    assert background(0).property("topRightRadius") == 0
    model.move(1, 4)
    settle()
    assert background(0).property("topRightRadius") > 0
    assert background(0).property("bottomLeftRadius") == 0
    model.set_ids(list(model._ids), uids=list(range(1000, 1100)))
    settle()
    assert background(0).property("topRightRadius") == 0
    assert background(0).property("bottomLeftRadius") > 0


def test_grid_scrolling_uses_cached_selection_for_offscreen_neighbors(grid):
    _, view, model, cell, background, select, settle = grid
    select(*range(100))
    model.uid_reads = model.selected_rows_reads = 0
    view.setProperty("contentY", 12 * view.property("cellHeight"))
    settle()
    assert cell(49).property("selected") is True
    assert background(49).property("radius") == 0
    view.setProperty("contentY", 24 * view.property("cellHeight"))
    settle()
    assert background(99).property("bottomRightRadius") > 0
    assert background(99).property("bottomLeftRadius") == 0
    assert model.uid_reads == model.selected_rows_reads == 0


def test_grid_partial_last_row_and_right_to_left_neighbors(grid):
    _, view, model, _, background, _, settle = grid
    model.set_ids(list(model._ids[:6]), uids=list(range(1000, 1006)))
    model.setSelectedMultiple([1003, 1004, 1005])
    settle()
    assert background(3).property("bottomRightRadius") > 0
    assert background(4).property("topLeftRadius") > 0
    assert background(5).property("topRightRadius") > 0
    view.setProperty("layoutDirection", Qt.RightToLeft)
    settle()
    assert background(4).property("topRightRadius") > 0
    assert background(4).property("topLeftRadius") == 0
    assert background(5).property("topLeftRadius") > 0


def test_grid_coalesces_selection_signals_into_one_model_query(grid):
    root, _, model, _, background, _, settle = grid
    model.uid_reads = model.selected_rows_reads = 0
    call(root, "handleClick", 1000, True, False)
    call(root, "handleClick", 1001, True, False)
    settle()
    assert background(0).property("topRightRadius") == 0
    assert model.selected_rows_reads == 1
    model.selected_rows_reads = 0
    call(root, "clearSelection")
    settle()
    assert background(0).property("topRightRadius") > 0
    assert model.selected_rows_reads == 0


def test_single_column_grid_joins_vertically_and_empty_reset_clears_cache(grid):
    _, view, model, _, background, select, settle = grid
    view.setProperty("width", 180)
    settle()
    assert view.property("columns") == 1
    select(0, 1, 2)
    assert background(0).property("topLeftRadius") > 0
    assert background(0).property("bottomLeftRadius") == 0
    assert background(1).property("radius") == 0
    assert background(2).property("bottomLeftRadius") > 0
    model.set_ids([])
    settle()
    assert view.property("count") == 0
    model.set_ids([1, 1], uids=[1000, 1001])
    settle()
    assert background(0).property("bottomLeftRadius") > 0
