"""The overlapping chip stack: what it looks like at rest, and under a pointer.

`ChipStack` is the machinery behind `ContributorChips` — a packed row of faces
that opens into a full chip when you point at one, shoving its right-hand
neighbours along as it grows. Almost all of it is geometry that only exists once
the thing is laid out, so this drives the real component rather than reading its
source, and it drives it in a real window.

**Why a window, when tests/test_contributor_chips_fit.py deliberately avoids
one.** That file asks an arithmetic question — how many chips does `shown` come
to at a width it picks — and the answer is a binding that needs nothing laid
out. This file asks where the chips *are*, and a positioner only re-positions
its children from a polish pass, which nothing runs on an item that is not in a
window. Offscreen, shown, four faces: the positions below are the ones Qt
actually computed.

**Why the hover is sent to the MouseArea rather than pointed at.** `QTest`'s
pointer helpers wait on the window between synthesised events and hang, on this
platform, often enough to be useless. Every chip owns its hover — that is the
whole hit-testing story, and the `z` assertions below are what pin it — so
handing one of them an enter event is the same gesture with none of the wait.
"""
from __future__ import annotations

import math
import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, QUrl
from PySide6.QtGui import QHoverEvent, QMouseEvent
from PySide6.QtQuick import QQuickItem, QQuickView

from app.gui.gui_paths import bundled_gui_root


# The chips folder is imported by absolute URL because the harness is
# written to a temporary directory: a relative `import "components/chips"` would be
# resolved against that rather than against the GUI package.
HARNESS = """
import QtQuick
import Nocturne
import "%s"

Item {
    objectName: "harness"
    width: 600
    height: 60

    ChipStack {
        objectName: "stack"
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        width: 240
        idKey: "artistId"
    }
}
"""

NAMES = ["Ada Lovelace", "Bea Arthur", "Cyd Charisse", "Dee Dee Ramone"]


def _entities(names, key="imageHash", main=()):
    """*main* is the indices carrying `isMain`, which the harness ignores.

    Only a stack given `prominentKey` reads the flag, so passing it here and not
    the property is how "nothing is prominent unless you ask" gets tested.
    """
    return [{"artistId": i + 1, "name": name, key: "", "isMain": i in main}
            for i, name in enumerate(names)]


@pytest.fixture
def build(qt_app, tmp_path):
    """A factory for a laid-out stack: `build(entities, width=..., **props)`.

    Yields `(stack, warnings)`. The view is kept alive by the fixture and closed
    at the end of the test, which matters: the delegates are read through the
    scene graph and a closed window takes them with it.
    """
    directory = bundled_gui_root() / "nocturne"
    source = tmp_path / "Harness.qml"
    source.write_text(HARNESS % QUrl.fromLocalFile(str(directory / "components" / "chips")).toString())

    views = []

    def make(entities, width=240, **props):
        view = QQuickView()
        view.engine().addImportPath(str(directory))
        warnings: list = []
        view.engine().warnings.connect(lambda w: warnings.extend(w))
        view.setSource(QUrl.fromLocalFile(str(source)))
        assert view.status() == QQuickView.Ready, "\n".join(
            e.toString() for e in view.errors()
        )
        view.resize(600, 60)
        view.show()
        views.append(view)

        stack = view.rootObject().findChild(QQuickItem, "stack")
        for name, value in props.items():
            stack.setProperty(name, value)
        stack.setProperty("width", width)
        stack.setProperty("entities", entities)
        _settle(qt_app)
        # The offscreen cursor can start over the enlarged leading face. Establish
        # the resting state explicitly before measuring or synthesizing hovers.
        for chip in _chips(stack):
            area = chip.findChild(QQuickItem, "contributorHover")
            where = QPointF(-1, -1)
            QCoreApplication.sendEvent(area, QHoverEvent(QEvent.HoverLeave, where, where, where))
        _settle(qt_app)
        return stack, warnings

    yield make

    for view in views:
        view.close()


def _settle(qt_app, ms=340):
    """Let the window render, which is what runs the layout and the animations.

    Longer than `Theme.normal`, so a width that is still animating has landed by
    the time anything reads it, and short of the tooltip's own delay, so nothing
    here has to wait on a popup it is not testing.
    """
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()


def _chips(stack):
    """The chip delegates in model order.

    Walked off the Row rather than found with `findChildren`, which never
    reaches a Repeater's delegates — they are not QObject children of anything
    it searches, and a test built on it passes by finding nothing.
    """
    row = stack.findChild(QQuickItem, "contributorRow")
    assert row is not None, "ChipStack no longer has an object-named row"
    found = [c for c in row.childItems() if c.objectName() == "contributorChip"]
    return sorted(found, key=lambda c: c.property("index"))


def _hover(qt_app, chip, on=True):
    """Point at *chip*, or stop pointing at it."""
    area = chip.findChild(QQuickItem, "contributorHover")
    assert area is not None, "the chip has no hover area to send an event to"
    where = QPointF(5, chip.height() / 2)
    kind = QEvent.HoverEnter if on else QEvent.HoverLeave
    QCoreApplication.sendEvent(area, QHoverEvent(kind, where, QPointF(-1, -1), where))
    _settle(qt_app)


def _label(chip):
    return chip.findChild(QQuickItem, "contributorLabel")


def _left(chip, stack):
    """The chip's drawn left edge, in the stack's own coordinates.

    Through `mapToItem` rather than off `x`, because the nudge that keeps the
    last chip inside the right edge is a transform: it moves what is drawn
    without touching the position the Row assigned.
    """
    return chip.mapToItem(stack, QPointF(0, 0)).x()


# --------------------------------------------------------------------- at rest


def test_every_chip_at_rest_is_a_circle_with_no_name_on_it(build, qt_app):
    stack, _ = build(_entities(NAMES))
    chips = _chips(stack)
    assert len(chips) == len(NAMES)

    for chip in chips:
        assert chip.width() == stack.property("collapsedWidth")
        assert chip.width() == chip.height(), "a chip at rest is not a circle"
        assert _label(chip).property("opacity") == 0


def test_the_leftmost_face_is_on_top(build, qt_app):
    """Which is also the order the caller ranked them in.

    Descending `z` is not decoration: Qt hit-tests in reverse paint order, so it
    is also the rule that a press in the overlap lands on the face drawn over
    it — the one being pointed at.
    """
    stack, _ = build(_entities(NAMES))
    zs = [chip.z() for chip in _chips(stack)]
    assert zs == sorted(zs, reverse=True)
    assert len(set(zs)) == len(zs), "two chips share a layer"


@pytest.mark.parametrize("main", [(), (0,), (0, 2)], ids=["plain", "one-lead", "two"])
def test_the_faces_overlap(build, qt_app, main):
    """The point of the whole component: a step narrower than a chip.

    Written as "each chip's own width less the overlap" rather than as the one
    `step` property, because that is what it has to be once two chips can be
    different sizes — and it is the same number when they are not. A large chip
    pushes its neighbour further along; what it does not do is cover more of it.
    """
    stack, _ = build(_entities(NAMES, main=main), prominentKey="isMain")
    chips = _chips(stack)
    overlap = stack.property("collapsedWidth") - stack.property("step")

    assert overlap > 0
    for before, after in zip(chips, chips[1:]):
        assert _left(after, stack) - _left(before, stack) == before.width() - overlap


# ------------------------------------------------------------- the large ones


def _round(value):
    """`Math.round`, which is not Python's `round`.

    QML rounds a half up and Python rounds it to even, so the 4.5px the ring
    scales to comes out 5 there and 4 here. Worth the two lines: a test that
    reimplemented the arithmetic with the wrong rounding would fail against
    perfectly good code and be "fixed" in the component.
    """
    return int(math.floor(value + 0.5))


def _expected(stack, i, main):
    """What chip *i* should measure: (width, inset, avatar)."""
    scale = stack.property("prominentScale") if i in main else 1
    size = _round(stack.property("chipHeight") * scale)
    inset = _round(stack.property("avatarInset") * scale)
    return size, inset, size - 2 * inset


def test_a_prominent_chip_is_half_again_as_big(build, qt_app):
    """And still a circle, which is the part a lazier scaling gets wrong."""
    stack, _ = build(_entities(NAMES, main=(0,)), prominentKey="isMain")
    lead, guest = _chips(stack)[0], _chips(stack)[1]

    assert lead.width() == lead.height(), "the large chip at rest is not a circle"
    assert lead.width() == guest.width() * stack.property("prominentScale")
    assert lead.height() == guest.height() * stack.property("prominentScale")


def test_a_large_face_keeps_a_ring_around_it(build, qt_app):
    """The ring scales and the face takes what is left, not the other way round.

    Doing it the other way round is the obvious way to write this and puts a
    36px face in a 45px chip: a 4.5px ring, a portrait on a half pixel, and —
    once the half pixel is resolved — a face sitting a pixel high in its own
    chip. The assertion that catches that is the centre, not the size.
    """
    stack, _ = build(_entities(NAMES, main=(0,)), prominentKey="isMain")
    for i, chip in enumerate(_chips(stack)):
        size, inset, avatar = _expected(stack, i, (0,))
        face = chip.findChild(QQuickItem, "contributorAvatar")
        assert face is not None, "the chip has no object-named face to measure"
        assert (chip.width(), face.width(), face.height()) == (size, avatar, avatar)
        # Centred in the chip at both sizes, which is what the inset buys.
        centre = face.mapToItem(chip, QPointF(face.width() / 2, face.height() / 2))
        assert (centre.x(), centre.y()) == (chip.width() / 2, chip.height() / 2)
        assert inset == (chip.height() - face.height()) / 2


def test_the_strip_is_as_tall_as_its_tallest_face(build, qt_app):
    """Measured over the whole list, so the band does not move as it narrows.

    A height taken from the chips that *fit* would change the moment a resize
    pushed the last large face into the "+N", moving a band for a reason that
    had already gone off the edge.
    """
    plain, _ = build(_entities(NAMES))
    lead, _ = build(_entities(NAMES, main=(0,)), prominentKey="isMain")
    scale = lead.property("prominentScale")

    assert plain.property("implicitHeight") == plain.property("chipHeight")
    assert lead.property("implicitHeight") == round(lead.property("chipHeight") * scale)

    narrow, _ = build(_entities(NAMES, main=(0,)), width=40, prominentKey="isMain")
    assert narrow.property("shown") == 0, "this test meant to hide every face"
    assert narrow.property("implicitHeight") == lead.property("implicitHeight")


def test_the_chips_share_one_centre_line(build, qt_app):
    """Including the "+N", which is the one that stays small beside them.

    A Row positions in x alone, so mixed heights top-align unless every child
    says otherwise — and a top-aligned overflow chip is the sort of wrong that
    looks like a rendering artefact rather than a missing anchor.
    """
    stack, _ = build(_entities(NAMES, main=(0,)), width=100, prominentKey="isMain")
    overflow = stack.findChild(QQuickItem, "contributorOverflow")
    assert overflow.isVisible(), "this test meant to have an overflow chip in it"

    drawn = [c for c in _chips(stack) if c.isVisible()] + [overflow]
    centres = [c.mapToItem(stack, QPointF(0, c.height() / 2)).y() for c in drawn]
    # Within half a pixel rather than equal. 45 and 30 differ by an odd number,
    # so a small chip's own offset is 7.5 and Qt rounds it to a whole pixel,
    # leaving it half a pixel low. The thing being guarded against is not that:
    # it is the 7.5 of a child that never centred at all.
    assert max(centres) - min(centres) <= 0.5, (
        "the chips are not on one centre line: %r" % centres
    )


def test_nothing_is_prominent_without_a_key(build, qt_app):
    """Every caller that has no such distinction gets the component it had.

    The entities below all carry `isMain`. A stack that was never told to read
    it must not: that is what keeps `EntityChips`' own family, and a GUI talking
    to a backend that has never heard of the flag, exactly where they were.
    """
    stack, _ = build(_entities(NAMES, main=(0, 1)))
    for chip in _chips(stack):
        assert chip.width() == stack.property("collapsedWidth")
    assert stack.property("implicitHeight") == stack.property("chipHeight")


# ---------------------------------------------------------------- under a hover


def test_hovering_lifts_the_chip_above_every_other(build, qt_app):
    stack, _ = build(_entities(NAMES))
    chips = _chips(stack)
    _hover(qt_app, chips[2])

    assert chips[2].z() > max(chip.z() for chip in chips if chip is not chips[2])


def test_hovering_opens_the_chip_and_shows_the_name(build, qt_app):
    stack, _ = build(_entities(NAMES))
    chips = _chips(stack)
    grown = chips[2]
    _hover(qt_app, grown)

    assert grown.property("expanded")
    assert grown.width() == pytest.approx(grown.property("fullWidth"))
    assert grown.width() > stack.property("collapsedWidth")
    assert _label(grown).property("opacity") == pytest.approx(1.0)


def test_the_chips_to_the_right_are_pushed_and_the_others_are_not(build, qt_app):
    """The push is the width animation, seen from next door.

    A Row re-positions its children whenever one changes width, so nothing here
    computes a displacement — and the growing chip's own left edge does not
    move, which is the reason a chip cannot expand out from under the pointer
    that expanded it.
    """
    stack, _ = build(_entities(NAMES))
    chips = _chips(stack)
    before = [_left(chip, stack) for chip in chips]

    _hover(qt_app, chips[1])
    grown = chips[1].width() - stack.property("collapsedWidth")
    after = [_left(chip, stack) for chip in chips]

    assert after[0] == before[0]
    assert after[1] == before[1], "the chip being pointed at moved"
    for index in (2, 3):
        assert after[index] == pytest.approx(before[index] + grown)


def test_letting_go_puts_the_stack_back(build, qt_app):
    stack, _ = build(_entities(NAMES))
    chips = _chips(stack)
    rest = [(chip.width(), chip.z(), _left(chip, stack)) for chip in chips]

    _hover(qt_app, chips[1])
    assert [(chip.width(), chip.z(), _left(chip, stack)) for chip in chips] != rest

    _hover(qt_app, chips[1], on=False)
    assert [(chip.width(), chip.z(), _left(chip, stack)) for chip in chips] == rest


# ------------------------------------------------------------- the right edge


def test_the_last_chip_is_nudged_back_inside_the_edge(build, qt_app):
    """And the nudge can never move the chip out from under the cursor.

    Growing rightward would push the last chip's name under the clip, so a chip
    that would overhang is drawn shifted left — capped at the growth itself, so
    the expanded chip still strictly contains the collapsed one it grew from.
    That containment is the whole no-oscillation argument: the pointer was
    inside the collapsed chip, so it is still inside the expanded one, and the
    hover cannot be lost to the motion it caused.
    """
    # Narrow enough that the last face sits hard against the right edge.
    stack, _ = build(_entities(NAMES), width=100)
    chips = _chips(stack)
    last = chips[-1]
    assert last.isVisible(), "the stack narrowed further than this test meant"
    collapsed = (_left(last, stack), _left(last, stack) + last.width())

    _hover(qt_app, last)
    expanded = (_left(last, stack), _left(last, stack) + last.width())

    assert expanded[0] < collapsed[0], "the overhanging chip was not nudged back"
    assert expanded[0] <= collapsed[0] and expanded[1] >= collapsed[1], (
        "the expanded chip %r does not contain the collapsed one %r"
        % (expanded, collapsed)
    )
    assert expanded[1] <= stack.property("width") + 1, "the name is still cut off"


def test_a_large_chip_is_nudged_no_further_than_it_grew(build, qt_app):
    """The containment argument again, for the chip the cap can get wrong.

    Capping the nudge at `fullWidth - collapsedWidth` is right until a chip is
    not `collapsedWidth` wide: for a 45px one that allowance is 15px more than
    the chip actually grew, so it can be drawn further left than it expanded
    and the pointer ends up outside the thing it is hovering. Then the hover
    drops, the chip snaps back under the pointer, and it opens again — the one
    oscillation this component is built to be incapable of.
    """
    stack, _ = build(_entities(NAMES, main=(3,)), width=115, prominentKey="isMain")
    last = _chips(stack)[-1]
    assert last.isVisible(), "the stack narrowed further than this test meant"
    collapsed = (_left(last, stack), _left(last, stack) + last.width())

    _hover(qt_app, last)
    expanded = (_left(last, stack), _left(last, stack) + last.width())

    assert expanded[0] < collapsed[0], "the overhanging chip was not nudged back"
    assert expanded[0] <= collapsed[0] and expanded[1] >= collapsed[1], (
        "the expanded chip %r does not contain the collapsed one %r"
        % (expanded, collapsed)
    )


def test_a_chip_that_fits_is_not_nudged(build, qt_app):
    """The nudge is for the edge, not a thing every chip does."""
    stack, _ = build(_entities(NAMES), width=400)
    chips = _chips(stack)
    before = _left(chips[0], stack)

    _hover(qt_app, chips[0])
    assert _left(chips[0], stack) == before


# ------------------------------------------------------------------- the seams


def test_a_press_opens_the_entity_by_its_id_key(build, qt_app):
    stack, _ = build(_entities(NAMES))
    opened: list = []
    stack.entityOpened.connect(opened.append)

    chip = _chips(stack)[1]
    area = chip.findChild(QQuickItem, "contributorHover")
    where = QPointF(5, chip.height() / 2)
    scene = chip.mapToScene(where)
    for kind, buttons in ((QEvent.MouseButtonPress, Qt.LeftButton),
                          (QEvent.MouseButtonRelease, Qt.NoButton)):
        QCoreApplication.sendEvent(
            area, QMouseEvent(kind, where, scene, Qt.LeftButton, buttons, Qt.NoModifier)
        )
    _settle(qt_app)

    # Read through `idKey`, which the harness sets to "artistId": an entity here
    # carries no `id` at all, so a stack that ignored the key would report 0.
    assert opened == [2]


@pytest.mark.parametrize(
    "flavour", [
        {"imageKey": "imageHash", "avatarRadiusRatio": 0.5},
        {"imageKey": "coverHash", "avatarRadiusRatio": 6 / 24},
    ],
    ids=["round-faces", "square-sleeves"],
)
def test_both_flavours_build_and_bind_cleanly(build, qt_app, flavour):
    """People and records, which is the whole entity-specific surface.

    A binding error in QML is a runtime warning nobody sees, and the square
    flavour has no consumer yet to notice one.
    """
    stack, warnings = build(_entities(NAMES, key=flavour["imageKey"]), **flavour)

    assert stack.property("shown") == len(NAMES)
    fatal = [
        w.toString() for w in warnings
        if "is not defined" in w.toString()
        or "Unable to assign" in w.toString()
        or "Binding loop" in w.toString()
    ]
    assert not fatal, "\n".join(fatal)


@pytest.mark.parametrize("main", [(), (0,), (1, 2)], ids=["plain", "lead", "middle"])
def test_sweeping_across_the_stack_settles_and_says_nothing(build, qt_app, main):
    """Hover every chip in turn, which is what a pointer crossing one does.

    Expanding a chip moves its neighbours, and a neighbour moving under the
    pointer is exactly the shape of a loop: the guard is that no chip's own
    position depends on whether it is hovered. What that buys is checked here as
    the interface would show it — one chip open at a time, everything back where
    it started at the end, and a clean console throughout.

    Swept with large chips in the row as well, where the sizes either side of a
    seam differ and the arithmetic that positions them has more to get wrong.
    """
    stack, warnings = build(_entities(NAMES, main=main), prominentKey="isMain")
    chips = _chips(stack)
    rest = [(chip.width(), _left(chip, stack)) for chip in chips]

    for chip in chips:
        _hover(qt_app, chip)
        open_now = [c for c in chips if c.property("expanded")]
        assert open_now == [chip]
        _hover(qt_app, chip, on=False)

    assert [(chip.width(), _left(chip, stack)) for chip in chips] == rest
    assert not [w.toString() for w in warnings if "Binding loop" in w.toString()]
