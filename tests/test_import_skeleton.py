"""The placeholder rows a list draws while it is waiting on work.

An empty panel does not read as "working" — it reads as "finished, and there was
nothing". A scan and a match run over a large folder leave the import list empty
for a minute or more, and for that whole minute the only sign of life is a
progress bar off in the rail. So the panel is filled with the shape of the
answer: one bar per row, standing where the row cards will land.

What is worth pinning here is arithmetic, not appearance. Three things:

**The bars line up with real rows.** They are drawn from the table's own
`rowHeight` and `sideMargin` rather than from numbers of their own, so a row card
arriving lands exactly on the bar that was holding its place. Get that wrong and
the handover is a jump.

**They fill the panel.** The count comes from the height, so a taller window gets
more of them and a resize is followed without anything being told.

**They are the right size while nobody is looking.** This is the one that has
already gone wrong once, in `ImportLegend`: an item that sizes itself by
*measuring* its own laid-out content latches at zero the first time it is hidden,
because a positioner that is not effectively visible never lays out, so the
height it is waiting on can never arrive. The skeleton is built while the page it
is on is hidden, every single time, so it must not measure anything. A skeleton
that reports the right row count while invisible is a skeleton that counted.

The pulse itself — that the bars brighten and dim together — is checked by
driving `pulse` by hand and reading the colour off two bars, which pins the thing
that matters about it: there is one number and every bar reads it, rather than an
animation each and a crowd drifting out of phase.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# -- harness ---------------------------------------------------------------


class _Probe:
    """Engine, root and item held together.

    All three have to stay referenced for the length of a test: the engine owns
    the QML objects, and letting any of it go collects the item out from under
    the assertion — as `libshiboken: Internal C++ object already deleted`.
    """

    def __init__(self, engine, component, root, item):
        self.engine = engine
        self.component = component
        self.root = root
        self.item = item

    def prop(self, name):
        return self.item.property(name)


def _build(qt_app, *, height=600, width=800, active=False, row_height=58,
           side_margin=34):
    """One SkeletonRows on its own, with no window and no GL context.

    Everything asserted below is a property binding, so the component can be
    built in isolation the way `tests/test_delegate_reuse.py` builds a table.
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent

    from app.gui.gui_paths import bundled_gui_root

    nocturne = bundled_gui_root() / "nocturne"
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(nocturne))

    warnings: list = []
    engine.warnings.connect(lambda w: warnings.extend(w))

    harness = f"""
    import QtQuick
    import Nocturne
    import "components"

    Item {{
        width: {width}; height: {height}
        SkeletonRows {{
            objectName: "skeleton"
            anchors.fill: parent
            rowHeight: {row_height}
            sideMargin: {side_margin}
            active: {"true" if active else "false"}
        }}
    }}
    """
    component = QQmlComponent(engine)
    component.setData(
        harness.encode(), QUrl.fromLocalFile(str(nocturne) + "/probe.qml")
    )
    assert component.status() == QQmlComponent.Status.Ready, component.errorString()

    root = component.create()
    assert root is not None, component.errorString()
    qt_app.processEvents()
    assert not warnings, [w.toString() for w in warnings]

    from PySide6.QtCore import QObject

    item = [
        child for child in root.findChildren(QObject)
        if child.objectName() == "skeleton"
    ][0]
    return _Probe(engine, component, root, item)


def _bars(probe):
    """The placeholder rectangles, in the order they were built."""
    return [
        child for child in probe.item.childItems()
        if child.metaObject().className().startswith("QQuickRectangle")
    ]


# ======================================================================
# Filling the panel
# ======================================================================


def test_the_bars_fill_the_height_they_are_given(qt_app):
    skeleton = _build(qt_app, height=600, row_height=58, active=True)

    assert skeleton.prop("rows") == 600 // 58
    assert len(_bars(skeleton)) == 600 // 58


def test_a_taller_panel_gets_more_of_them(qt_app):
    short = _build(qt_app, height=300)
    tall = _build(qt_app, height=900)

    assert tall.prop("rows") > short.prop("rows")


def test_no_height_means_no_bars(qt_app):
    """And never a negative count, which a Repeater would refuse."""
    skeleton = _build(qt_app, height=0, active=True)

    assert skeleton.prop("rows") == 0
    assert _bars(skeleton) == []


# ======================================================================
# Standing where the rows will
# ======================================================================


def test_a_bar_sits_exactly_where_a_row_card_will(qt_app):
    """The figures are TrackTable's, not this component's.

    A row's drawn card is inset from the list edge by `sideMargin - 8`, and its
    backdrop takes a pixel off the top and bottom at `Theme.radius`. A bar that
    used its own numbers would be a placeholder the rows do not land on, and the
    arrival would read as a jump rather than a fill.
    """
    skeleton = _build(qt_app, width=800, height=600, row_height=58,
                      side_margin=34, active=True)
    first = _bars(skeleton)[0]

    assert first.property("x") == 34 - 8
    assert first.property("width") == 800 - 2 * (34 - 8)
    assert first.property("height") == 58 - 2
    assert first.property("y") == 1


def test_the_bars_tile_at_the_row_pitch(qt_app):
    """No gaps and no overlap: the list tiles its rows and so does this."""
    skeleton = _build(qt_app, height=600, row_height=58, active=True)
    bars = _bars(skeleton)

    ys = [bar.property("y") for bar in bars]
    assert ys == [i * 58 + 1 for i in range(len(bars))]


def test_the_geometry_follows_the_table_it_is_given(qt_app):
    """Handed different figures, it draws to those instead of to its own."""
    skeleton = _build(qt_app, width=500, height=400, row_height=40,
                      side_margin=22, active=True)
    bars = _bars(skeleton)

    assert skeleton.prop("rows") == 400 // 40
    assert bars[0].property("x") == 22 - 8
    assert bars[0].property("width") == 500 - 2 * (22 - 8)
    assert bars[1].property("y") == 40 + 1


# ======================================================================
# Measuring nothing
# ======================================================================


def test_it_is_the_right_size_while_it_is_invisible(qt_app):
    """The regression guard, and the reason there is no positioner in it.

    `active` is false here, so the item is not visible — which is the state it is
    built in every time, since the page it lives on is hidden until a run has
    something to show.

    What is being enforced is that `rows` follows the height it was handed,
    whether or not anything has laid out. An implementation that read its own
    content instead answers with whatever that content happens to be, which is
    not the same number and need not be a number at all: `ImportLegend` sized
    itself from a positioner it also hid on the strength of, and latched at zero
    for good, because hiding is exactly what stops the layout that would have
    fixed it. Counting cannot latch.
    """
    skeleton = _build(qt_app, height=600, row_height=58, active=False)

    assert skeleton.prop("visible") is False, "the premise: nobody is looking"
    assert skeleton.prop("rows") == 600 // 58, "it measured instead of counting"


def test_an_inactive_skeleton_builds_nothing(qt_app):
    """The count is free; the rectangles are not.

    Every track list in the window owns one of these, so bars built at load and
    never shown would be a hundred items the queue and the library carry for a
    waiting state only the import page has. `rows` still answers, because that
    is the number the component must be able to give without being drawn.
    """
    skeleton = _build(qt_app, height=600, active=False)

    assert skeleton.prop("rows") > 0
    assert _bars(skeleton) == []


def test_inactive_is_drawn_at_nothing(qt_app):
    skeleton = _build(qt_app, active=False)

    assert skeleton.prop("opacity") == 0
    assert skeleton.prop("visible") is False


def test_active_is_drawn_in_full(qt_app):
    """Built active, so this is the binding rather than the fade into it."""
    skeleton = _build(qt_app, active=True)

    assert skeleton.prop("opacity") == 1
    assert skeleton.prop("visible") is True


# ======================================================================
# One pulse, every bar
# ======================================================================


def test_every_bar_is_the_same_shade_at_any_instant(qt_app):
    """Which is the whole of "they breathe together".

    Read rather than driven. The animation is running here — it has to be, since
    the bars only exist while the skeleton is active — so `pulse` is not
    something a test can hold still. It does not need to be: whatever phase the
    sample catches, every bar must be showing that same phase. An animation per
    bar is exactly what would make these disagree.
    """
    skeleton = _build(qt_app, height=600, active=True)
    bars = _bars(skeleton)
    assert len(bars) > 1, "one bar cannot disagree with anything"

    shades = {round(bar.property("color").alphaF(), 6) for bar in bars}

    assert len(shades) == 1, f"the bars drifted apart: {sorted(shades)}"


def test_the_shade_is_read_off_the_shared_pulse(qt_app):
    """And is that one number, not a coincidence that happens to match.

    Sampled together, so the relation holds at whatever phase the animation is
    in. This is what pins that there is a single value every bar derives from,
    rather than each bar animating a colour of its own.
    """
    skeleton = _build(qt_app, height=600, active=True)
    bar = _bars(skeleton)[0]

    for _ in range(5):
        qt_app.processEvents()
        pulse = skeleton.prop("pulse")
        assert bar.property("color").alphaF() == pytest.approx(
            0.04 + 0.05 * pulse, abs=0.002
        )


def test_the_bars_stay_quieter_than_the_progress_bar(qt_app):
    """A placeholder is furniture.

    The one bar on the page reporting real numbers sits at 0.10, and has to stay
    the loudest thing in the panel however far the pulse swings.
    """
    skeleton = _build(qt_app, height=600, active=True)
    bar = _bars(skeleton)[0]

    for _ in range(10):
        qt_app.processEvents()
        assert 0 < bar.property("color").alphaF() < 0.10


def test_the_bars_are_a_tint_and_not_a_colour(qt_app):
    """Alpha over the theme's own text colour, never a literal grey.

    Theme.qml's standing rule. A hex literal here would be one colour in both
    themes, and invisible in one of them — so the bar has to be transparent, and
    the same hue the text is.
    """
    from PySide6.QtCore import QObject

    skeleton = _build(qt_app, height=600, active=True)
    bar = _bars(skeleton)[0]
    colour = bar.property("color")

    assert 0 < colour.alphaF() < 1, "an opaque bar is a hardcoded grey"
    assert (colour.redF(), colour.greenF(), colour.blueF()) != (0.0, 0.0, 0.0)
