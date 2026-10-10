"""How many artist chips fit, at a width this test chooses.

`ContributorChips` is driven here on its own, in a two-line QML harness, rather
than through the album page — and that separation is the point of the file.

The page cannot answer this question repeatably. Its strip is anchored between
the transport controls and the Export button inside a window whose width comes
from a session value, is floored by `minimumWidth`, and is then clamped by
whatever screen the test happens to find. Running the same assertion about "four
names fit" on two machines measures two different strips, and the one that fails
looks exactly like a layout bug. tests/test_album_contributor_chips.py therefore
checks only what is true at *any* width, and the arithmetic is pinned here where
the width is an input.

What is being checked is one invariant with three faces: a strip never draws
past its own right edge, never silently drops a name it had room for, and always
accounts for every name it was given as either shown or held back.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine

from app.gui.gui_paths import bundled_gui_root


# The components folder is imported by absolute URL because the harness is
# written to a temporary directory: a relative `import "components"` would be
# resolved against that rather than against the GUI package.
HARNESS = """
import QtQuick
import Nocturne
import "%s"

Item {
    id: harness
    objectName: "harness"
    width: 800
    height: 60

    ContributorChips {
        id: chips
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
    }
}
"""


@pytest.fixture
def harness(qt_app, tmp_path):
    """The component alone, in an Item whose width a test can simply assign."""
    directory = bundled_gui_root() / "nocturne"
    source = tmp_path / "Harness.qml"
    source.write_text(HARNESS % QUrl.fromLocalFile(str(directory / "components")).toString())

    engine = QQmlApplicationEngine()
    warnings: list = []
    engine.warnings.connect(lambda w: warnings.extend(w))
    engine.addImportPath(str(directory))
    engine.load(QUrl.fromLocalFile(str(source)))
    assert engine.rootObjects(), "\n".join(w.toString() for w in warnings)

    root = engine.rootObjects()[0]
    # By objectName rather than by an alias: a `property alias` to a QML-defined
    # type has no Python converter, so reading it from here raises.
    yield root, root.findChild(object, "contributorChips"), warnings, qt_app
    del engine


def _pump(qt_app, rounds=30):
    for _ in range(rounds):
        qt_app.processEvents()


def _fill(harness, names, width, main=()):
    """Give the strip *names*, one track each, in a strip *width* pixels wide.

    *main* is the indices credited on most of the record, which draw half again
    as large. Every case below that does not name any is a strip of one size,
    which is what this file measured before there were two.
    """
    root, strip, _, qt_app = harness
    root.setProperty("width", width)
    strip.setProperty("contributors", [
        {"artistId": i + 1, "name": name, "imageHash": "",
         "trackCount": 1, "isMain": i in main}
        for i, name in enumerate(names)
    ])
    _pump(qt_app)
    return strip


def _widths(strip, main):
    """What each chip costs at rest, in model order.

    `30 * 1.5` is 45 exactly, so nothing here rounds and Python's rounding
    cannot disagree with QML's. A scale that did not land on a whole pixel
    would need `Math.round`'s half-up, which Python's `round` is not.
    """
    collapsed = strip.property("collapsedWidth")
    scale = strip.property("prominentScale")
    return [int(collapsed * (scale if i in main else 1))
            for i in range(strip.property("count"))]


def _chips(strip, main=()):
    """Every chip the Repeater built, in model order, by name.

    Walked off the Row rather than found with `findChildren`, which never
    reaches a Repeater's delegates — they are not QObject children of anything
    it searches, and a test built on it passes by finding nothing.

    Names, not positions. The harness is never put in a window, so nothing ever
    runs a polish pass and the Row never moves its children off x=0: every
    chip's *position* here is a lie. The widths are not a lie, and they are also
    no longer interesting one by one — a chip at rest is a circle whose size the
    model alone decides, which is exactly what makes the fit arithmetic below
    possible. So they are asserted against that, here and once, rather than
    summed out of the scene.
    """
    row = strip.findChild(object, "contributorRow")
    out = []
    for item in row.childItems():
        if item.objectName() == "contributorChip":
            data = item.property("modelData")
            data = data.toVariant() if hasattr(data, "toVariant") else data
            out.append((item.property("index"), data["name"], item.property("width")))
    out.sort()
    expected = _widths(strip, main)
    odd = [(name, width, expected[i])
           for i, name, width in out if width != expected[i]]
    assert not odd, "chips at rest are the wrong size (name, got, want): %r" % odd
    return [name for _, name, _ in out]


def _extent_for(strip, n, main=()):
    """How wide the strip would lay out drawing *n* chips, "+N" included.

    Computed the way the Row does it: negative spacing, so each chip after the
    first costs its own width less the overlap, and the overflow chip joins the
    stack overlapping the last face by the same amount. A sum rather than a
    multiplication, because a large chip costs more than a small one — and the
    same answer as the multiplication when nobody is large.
    """
    collapsed = strip.property("collapsedWidth")
    overlap = collapsed - strip.property("step")
    total = sum(_widths(strip, main)[:n]) - overlap * (n - 1) if n > 0 else 0
    if n < strip.property("count"):
        total += strip.property("overflowWidth") - (overlap if n > 0 else 0)
    return total


def _extent(strip, main=()):
    """How wide the strip's own answer lays out.

    The invariant this whole file is about: whatever `shown` says has to fit in
    the width the strip was given.
    """
    return _extent_for(strip, strip.property("shown"), main)


SHORT = ["Bjork", "M83", "Air", "Cher", "Enya", "Moby"]
LONG = ["Massive Attack Featuring Horace Andy",
        "Del The Funky Homosapien",
        "Godspeed You! Black Emperor",
        "The Chemical Brothers",
        "Nine Inch Nails"]


# ------------------------------------------------------------- everything fits


def test_a_wide_strip_shows_everyone(harness):
    strip = _fill(harness, SHORT, 1200)
    assert strip.property("shown") == len(SHORT)
    assert strip.property("hidden") == 0


def test_no_room_is_spent_on_an_overflow_that_is_not_needed(harness):
    """The whole list is tried first, before any is reserved for the "+N".

    A strip that always held the overflow's width back would drop its last chip
    on exactly the albums where everything fitted.
    """
    strip = _fill(harness, SHORT, 1200)
    snug = _fill(harness, SHORT, int(_extent(strip)) + 1)
    assert snug.property("shown") == len(SHORT)
    assert snug.property("hidden") == 0


# ------------------------------------------------------------------ it clips


def test_a_narrow_strip_holds_the_tail_back(harness):
    # Narrow in stack terms is genuinely narrow: five faces at a 19px step need
    # 106px, so the widths that used to truncate a strip of full pills now hold
    # the whole cast comfortably.
    strip = _fill(harness, LONG, 80)
    shown, hidden = strip.property("shown"), strip.property("hidden")
    assert hidden > 0
    assert shown + hidden == len(LONG)
    # A prefix, never a selection of whichever names happened to be short.
    assert _chips(strip)[:shown] == LONG[:shown]


def test_the_overflow_says_how_many_are_left(harness):
    strip = _fill(harness, LONG, 80)
    overflow = strip.findChild(object, "contributorOverflow")
    assert overflow.property("width") > 0
    assert strip.property("hidden") == len(LONG) - strip.property("shown")


@pytest.mark.parametrize("main", [(), (0,), (0, 1)], ids=["plain", "one", "two"])
def test_what_is_kept_always_fits(harness, main):
    """The one assertion the component exists to satisfy.

    Measured from the chips' own widths rather than from the count the strip
    arrived at, so the two can be caught disagreeing — which is the only way
    this component fails, and it fails invisibly: a chip laid out past the right
    edge still renders, because the hero clips it.

    Swept over a mixed strip as well as a plain one, because the count is no
    longer a division that is right or wrong all at once: it is a walk along the
    list adding whatever each chip costs, and a walk can be right for every
    uniform case and off by one the moment the first chip is wider.
    """
    for width in (40, 60, 80, 100, 120, 240, 500, 1200):
        strip = _fill(harness, LONG, width, main)
        assert _extent(strip, main) <= strip.property("width") + 1, (
            "at %dpx the strip kept %d chips needing %.1f"
            % (width, strip.property("shown"), _extent(strip, main))
        )


@pytest.mark.parametrize("main", [(), (0,), (0, 1)], ids=["plain", "one", "two"])
def test_nothing_that_would_have_fitted_is_dropped(harness, main):
    """The other half: not fitting is a reason to stop, not a reason to be shy.

    Without this, a component that always answered zero would pass every other
    test in the file.
    """
    for width in (40, 60, 80, 100, 120, 240, 500, 1200):
        strip = _fill(harness, LONG, width, main)
        shown, hidden = strip.property("shown"), strip.property("hidden")
        if hidden == 0:
            continue
        assert _extent_for(strip, shown + 1, main) > strip.property("width"), (
            "at %dpx the strip held back %r, which had room"
            % (width, _chips(strip, main)[shown])
        )


def test_every_name_is_accounted_for_at_every_width(harness):
    for width in (30, 60, 120, 200, 360, 640, 1000, 1600):
        strip = _fill(harness, LONG, width)
        assert strip.property("shown") + strip.property("hidden") == len(LONG)
        assert 0 <= strip.property("shown") <= len(LONG)


def test_a_main_artist_costs_the_room_of_half_another_face(harness):
    """What the size is actually spending, priced in names.

    The snug width is the one from `test_no_room_is_spent_on_an_overflow_that_is
    _not_needed` — exactly enough for the whole cast at one size. Making the
    first of them a lead has to push somebody off the end, or the large chip is
    not being counted as large anywhere the fit can see it.
    """
    strip = _fill(harness, SHORT, 1200)
    snug = int(_extent(strip)) + 1
    assert _fill(harness, SHORT, snug).property("shown") == len(SHORT)
    assert _fill(harness, SHORT, snug, main=(0,)).property("shown") < len(SHORT)


def test_the_strip_is_taller_when_somebody_leads(harness):
    """The band is the hero's, but the strip's own height is this component's."""
    plain = _fill(harness, SHORT, 1200).property("implicitHeight")
    lead = _fill(harness, SHORT, 1200, main=(0,)).property("implicitHeight")
    assert lead == plain * _fill(harness, SHORT, 1200).property("prominentScale")


def test_a_strip_with_no_room_at_all_still_counts(harness):
    """Too narrow for one face beside the count. The "+N" alone is honest.

    40px, because a face is only 30 wide now: the 90px that used to fit nobody
    fits three of them.
    """
    strip = _fill(harness, LONG, 40)
    assert strip.property("shown") == 0
    assert strip.property("hidden") == len(LONG)


# ------------------------------------------------------------- it stays right


def test_the_answer_follows_the_width_down_and_back_up(harness):
    """A resize is answered immediately, not on the next event loop turn.

    Deferring it leaves the strip showing a count decided for the old width,
    which after a narrowing is chips laid out past the right edge — a frame of
    the strip quietly losing its last face and its "+N".

    Nothing has to be arranged for that any more: `shown` is a binding over the
    width, so there is no pass to defer and no window to close. This stays as
    the guard on that staying true.
    """
    root, strip, _, qt_app = harness
    wide = _fill(harness, LONG, 1600).property("shown")
    narrow = _fill(harness, LONG, 60).property("shown")
    assert narrow < wide

    root.setProperty("width", 1600)
    # No processEvents: the binding has already answered.
    assert strip.property("shown") == wide


def test_emptying_the_list_empties_the_strip(harness):
    root, strip, _, qt_app = harness
    _fill(harness, LONG, 900)
    assert strip.property("shown") > 0

    strip.setProperty("contributors", [])
    _pump(qt_app)
    assert strip.property("count") == 0
    assert strip.property("shown") == 0
    assert strip.property("hidden") == 0


def test_it_binds_cleanly(harness):
    # With a lead in it, so the size bindings and the new anchors are in the
    # sweep rather than only the arithmetic that decides how many to draw.
    _, strip, warnings, _ = harness
    _fill(harness, LONG, 500, main=(0,))
    fatal = [
        w.toString() for w in warnings
        if "is not defined" in w.toString()
        or "Unable to assign" in w.toString()
        or "Binding loop" in w.toString()
    ]
    assert not fatal, "\n".join(fatal)
