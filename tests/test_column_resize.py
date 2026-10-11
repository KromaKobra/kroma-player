"""Dragging a column divider, through the real QML.

`TrackColumnLayout.resizeTo` is the whole of what a divider drag does, and every
property of it that matters is a property of the *arithmetic*: the divider stays
under the cursor, the row total never changes, and dragging out and back lands
on the layout you started with. None of that is visible to a test that only
proves the file parses, which is all `tests/test_gui_packages.py` does.

So this drives the component itself. The harness has to be written into the
package directory for the relative `import "TrackColumns.js"` inside
TrackColumnLayout to resolve, the same trick `tests/test_lyrics_editor_qml.py`
uses -- but nothing else from that harness is needed here. The layout object
talks to no backend: give it a profile, a budget and a gap and it is complete.

`settingsKey: ""` is what keeps it out of the user's settings file. It also
means `commit()` is a no-op, so a drag can be started and released here without
writing anything anywhere.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Wide enough that the default library layout fits with room to spare, so
# nothing is dropped and no column starts out squeezed below what it asked for.
BUDGET = 1200
GAP = 14

HARNESS = """
import QtQuick

QtObject {
    property var layout: TrackColumnLayout {
        objectName: "layout"
        profile: "%s"
        settingsKey: ""
    }
}
"""


@pytest.fixture
def make_layout(qt_app):
    """Build a budgeted layout for a profile, with nothing persisted."""
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine

    from app.gui.gui_paths import bundled_gui_root

    directory = bundled_gui_root() / "nocturne" / "components" / "tracks"
    built = []

    def build(profile: str):
        harness = directory / f"__ColumnResizeHarness_{profile}.qml"
        harness.write_text(HARNESS % profile)
        built.append(harness)

        warnings: list[str] = []
        engine = QQmlApplicationEngine()
        engine.warnings.connect(
            lambda errors: warnings.extend(e.toString() for e in errors)
        )
        built.append(engine)   # the layout dies with the engine that owns it
        engine.load(QUrl.fromLocalFile(str(harness)))
        roots = engine.rootObjects()
        assert roots, f"the harness failed to load: {warnings}"
        obj = roots[0].property("layout")
        obj.setBudget(BUDGET, GAP)
        return obj

    try:
        yield build
    finally:
        for item in built:
            if isinstance(item, Path):
                item.unlink(missing_ok=True)


@pytest.fixture
def layout(make_layout):
    """The library list: TITLE | ALBUM | ADDED | TIME."""
    return make_layout("library")


@pytest.fixture
def import_layout(make_layout):
    """The import list: TITLE | ALBUM | TIME | SOURCE | STATUS."""
    return make_layout("import")


def widths(layout) -> dict[str, float]:
    return layout.property("renderedWidths").toVariant()


def title(layout) -> float:
    return layout.property("titleWidth")


def assert_fits(layout) -> None:
    """The invariant the whole file is built on: the row is exactly full."""
    w = widths(layout)
    assert title(layout) + sum(w.values()) == layout.budgetFor(len(w))


def drag(layout, key: str, delta: float) -> None:
    """Move *key*'s left divider by *delta* pixels; positive is rightwards."""
    layout.beginResize()
    layout.resizeTo(key, widths(layout)[key] - delta)


# The library default order, which every case below assumes:
#   TITLE | ALBUM | ADDED | TIME
# with ALBUM 220/90, ADDED (stashedDate) 110/76 and TIME (duration) 64/52.


def test_the_defaults_are_what_these_tests_assume(layout):
    assert [c["key"] for c in layout.property("renderedColumns").toVariant()] == [
        "album", "stashedDate", "duration"
    ]
    assert widths(layout) == {"album": 220, "stashedDate": 110, "duration": 64}
    assert_fits(layout)


def test_a_right_drag_stops_at_its_own_floor_without_pushing(layout):
    """ADDED has 34 to give, and up to there nothing else is disturbed."""
    drag(layout, "stashedDate", 34)
    assert widths(layout) == {"album": 254, "stashedDate": 76, "duration": 64}
    assert_fits(layout)


def test_a_right_drag_pushes_into_the_columns_beyond_it(layout):
    """Past its own floor ADDED stops shrinking, TIME starts, and ALBUM still
    receives the whole 40 pixels -- which is the divider staying on the cursor."""
    drag(layout, "stashedDate", 40)
    assert widths(layout) == {"album": 260, "stashedDate": 76, "duration": 58}
    assert_fits(layout)


def test_a_right_drag_stops_only_when_everything_rightwards_is_at_its_floor(layout):
    """ADDED can give 34 and TIME 12; the divider travels 46 and no further."""
    drag(layout, "stashedDate", 4000)
    assert widths(layout) == {"album": 266, "stashedDate": 76, "duration": 52}
    assert_fits(layout)


def test_the_last_column_has_only_its_own_slack_to_give(layout):
    """Nothing sits right of TIME, so its floor really is the end of the drag."""
    drag(layout, "duration", 4000)
    assert widths(layout) == {"album": 220, "stashedDate": 122, "duration": 52}
    assert_fits(layout)


def test_a_left_drag_still_walks_the_chain(layout):
    """The direction that already worked: ADDED, then ALBUM, then the title."""
    drag(layout, "duration", -34)        # comes out of ADDED alone
    assert widths(layout) == {"album": 220, "stashedDate": 76, "duration": 98}
    assert title(layout) == 764

    # A second drag, so ADDED starts spent: ALBUM pays next, and only what it
    # cannot cover reaches the title column.
    drag(layout, "duration", -200)
    assert widths(layout) == {"album": 90, "stashedDate": 76, "duration": 298}
    assert title(layout) == 764 - 70
    assert_fits(layout)


def test_a_left_drag_reaches_the_title_column(layout):
    before = title(layout)
    drag(layout, "duration", -400)
    assert title(layout) == before - (400 - 34 - 130)
    assert_fits(layout)


def test_dragging_out_and_back_restores_the_layout_exactly(layout):
    start = dict(widths(layout))
    start_title = title(layout)

    layout.beginResize()
    layout.resizeTo("stashedDate", -900)     # hard right, well past every floor
    layout.resizeTo("stashedDate", 900)      # hard left, well past what is left
    layout.resizeTo("stashedDate", start["stashedDate"])
    layout.endResize()

    assert widths(layout) == start
    assert title(layout) == start_title
    assert_fits(layout)


def test_the_push_skips_a_column_that_does_not_absorb(import_layout):
    """STATUS is `absorbs: false`, so a push goes around it.

    The import list is the only profile that has it, and it is last:
    ALBUM | TIME | SOURCE | STATUS. Dragging ALBUM's divider right takes
    ALBUM's own 130, then TIME's 12 and SOURCE's 34, and stops at 176 with
    STATUS untouched at the width it started with.
    """
    assert widths(import_layout) == {
        "album": 220, "duration": 64, "source": 110, "status": 58
    }

    drag(import_layout, "album", 4000)
    assert widths(import_layout) == {
        "album": 90, "duration": 52, "source": 76, "status": 58
    }
    assert title(import_layout) == 692 + 176
    assert_fits(import_layout)


def test_the_row_stays_full_at_every_point_of_a_drag(layout):
    layout.beginResize()
    for asked in range(-200, 400, 7):
        layout.resizeTo("stashedDate", asked)
        assert_fits(layout)
    layout.endResize()
