"""Pulling a queue row off the right-hand edge by its grip.

The grip reorders when dragged up and down and removes when dragged far enough
sideways, and the whole of the difference between those is a curve and a
boundary. Neither is visible to ``test_gui_packages`` — a wrong constant, an
inverted test or a Behavior on the wrong property all load perfectly clean and
leave a gesture that feels broken.

So the gesture is driven here against a real engine, through the same two
functions the grip's MouseArea calls (``_beginDrag``/``_endDrag``) with the
pointer moved by writing ``_dragPointerX``. That is one step below the mouse and
every step above it that matters: the arithmetic, the boundary, the release, and
what reaches the model.

What is asserted is the specification, not the numbers:

* short of the boundary the card meets a resistance that **stops** it — the
  offset must plateau, or the row simply slides away with no gesture in it;
* past the boundary it **snaps** across and then follows the pointer **exactly**,
  because the snap is the only warning that a release now removes;
* coming back over the boundary **puts the resistance back**, and a release there
  removes nothing;
* a release past it removes what the drag was carrying, and only that.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from conftest import make_track
from PySide6.QtCore import Qt

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Where the drag starts, in the global coordinates the grip works in. The value
# is arbitrary — only differences from it are ever read.
PRESS_X = 300.0
PRESS_Y = 500.0


@pytest.fixture(scope="module")
def gui(qt_app, tmp_path_factory):
    """The real window over a throwaway vault with a queue in it."""
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine

    from app.artwork.cover_provider import CoverImageProvider
    from app.artwork.local_art_provider import LocalArtImageProvider
    from app.gui.gui_paths import bundled_gui_root
    from app.main import CONTEXT_PROPERTIES, _register_qml_types
    from app.models.track import Artist, Track
    from app.utils.library_paths import set_library_root

    vault = tmp_path_factory.mktemp("vault") / "venomvault"
    (vault / ".data").mkdir(parents=True)
    set_library_root(vault)

    from app.bootstrap import create_service_registry

    registry = create_service_registry()
    session = registry.get_service("db_session")
    artist = Artist(name="Someone")
    session.add(artist)
    session.flush()
    for i in range(12):
        session.add(
            make_track(
                session,
                title=f"Track {i}",
                album="An Album",
                duration_seconds=200,
                artists=[artist],
            )
        )
    session.commit()
    track_ids = [t.id for t in session.query(Track).all()]

    _register_qml_types(registry)

    directory = bundled_gui_root() / "nocturne"
    engine = QQmlApplicationEngine()
    engine.addImageProvider(
        "cover", CoverImageProvider(registry.get_service("cover_store"))
    )
    engine.addImageProvider("localart", LocalArtImageProvider())
    context = engine.rootContext()
    for qml_name, service in CONTEXT_PROPERTIES.items():
        context.setContextProperty(qml_name, registry.get_service(service))
    engine.addImportPath(str(directory))
    engine.load(QUrl.fromLocalFile(str(directory / "main.qml")))

    roots = engine.rootObjects()
    assert roots, "the GUI failed to load"

    queue = registry.get_service("queue_view_model")
    yield roots[0], qt_app, queue, track_ids

    del engine


@pytest.fixture
def table(gui):
    """The queue's own TrackTable, over a queue of twelve, with nothing dragged.

    Function-scoped over a module-scoped window, so each test starts from the
    same twelve rows however the one before it left them.
    """
    window, qt_app, queue, track_ids = gui

    # Show the Queue page. Not decoration: every view stays built and only the
    # current one is visible, so on any other page the queue's rows are hidden —
    # and a hidden row is one Qt's cursor walk never reaches, which would make
    # the cursor assertions below pass or fail on which page the window happened
    # to restore to.
    _go_to_queue(window, qt_app)

    queue.clearAll()
    queue.append_track_ids(track_ids)
    _settle(qt_app)

    found = [
        obj
        for obj in _descendants(window)
        if obj.property("mode") == "queue" and obj.property("reorderable") is True
    ]
    assert len(found) == 1, "the queue's track table is gone, renamed or duplicated"
    table = found[0]

    # Put the drag state back too, not just the rows.
    #
    # The window is module-scoped, so the table one test finishes with is the
    # table the next one starts with — and a test that ends mid-gesture leaves
    # `_dragRows`, `_dragMode` and possibly a running `springBack` behind it.
    # Resetting the queue's contents never touched any of that, so "however the
    # one before it left them" was only ever true of the rows.
    #
    # `_clearDrag` is the table's own teardown: it stops the spring and the drop
    # timer as well as emptying the state, which is exactly the handover wanted
    # here. Without it these tests fail intermittently and only under load,
    # which is what kept it hidden.
    from PySide6.QtCore import QMetaObject, Qt

    assert QMetaObject.invokeMethod(table, "_clearDrag", Qt.DirectConnection), (
        "TrackTable no longer exposes _clearDrag()"
    )
    _settle(qt_app)

    return table, qt_app, queue


QUEUE_VIEW_INDEX = 1


def _go_to_queue(window, qt_app):
    """Navigate the way the nav rail does, and wait out the cross-fade."""
    from PySide6.QtCore import Q_ARG, QMetaObject, Qt

    ok = QMetaObject.invokeMethod(
        window, "go", Qt.DirectConnection, Q_ARG("QVariant", QUEUE_VIEW_INDEX)
    )
    assert ok, "main.qml no longer exposes go(index)"
    _settle(qt_app, 500)
    assert window.property("viewIndex") == QUEUE_VIEW_INDEX


def _descendants(obj):
    for child in obj.children():
        yield child
        yield from _descendants(child)


def _settle(qt_app, ms=350):
    """Pump the loop for a fixed stretch, for waiting out a Timer."""
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        qt_app.processEvents()


def _settle_snap(table, qt_app, timeout_ms=2000):
    """Pump until the snap has finished easing.

    ``_snap`` is under a Behavior, so reading the offset straight after crossing
    the boundary catches it mid-flight — somewhere between the resisted curve and
    the pointer, which is neither of the two things a test wants to ask about.

    Waits for the value the Behavior is heading for rather than for it to stop
    changing. "It has not moved since the last poll" is not an answer here: this
    loop polls far faster than the animation ticks, so it agrees with itself
    between frames and returns while the ease is still running.
    """
    target = 1.0 if table.property("_dismissArmed") else 0.0
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if table.property("_snap") == target:
            return
    raise AssertionError(
        f"the snap never reached {target} (stuck at {table.property('_snap')})"
    )


def _grab(table, row, mode="dismiss"):
    """Start a drag on *row* in *mode*, the way the grip's MouseArea does.

    The mode is the grip's decision, not the table's — only the MouseArea can
    see which way the hand went first — so it is passed in here for the same
    reason it is passed in there.
    """
    from PySide6.QtCore import Q_ARG, Qt

    ok = table.metaObject().invokeMethod(
        table,
        "_beginDrag",
        Qt.DirectConnection,
        Q_ARG("QVariant", row),
        Q_ARG("QVariant", PRESS_Y),
        Q_ARG("QVariant", PRESS_X),
        Q_ARG("QVariant", mode),
    )
    assert ok, "_beginDrag is gone or has changed arity"
    assert table.property("_dragging"), "the drag did not start"
    assert table.property("_dragMode") == mode


def _pull(table, qt_app, dx):
    """Move the pointer *dx* to the right of where it was pressed."""
    table.setProperty("_dragPointerX", PRESS_X + dx)
    qt_app.processEvents()


def _push_down(table, qt_app, dy):
    """Move the pointer *dy* below where it was pressed."""
    table.setProperty("_dragPointerY", PRESS_Y + dy)
    qt_app.processEvents()


def _end_drag(table, commit):
    from PySide6.QtCore import Q_ARG, Qt

    table.metaObject().invokeMethod(
        table, "_endDrag", Qt.DirectConnection, Q_ARG("QVariant", commit)
    )


def _release(table, qt_app, commit=True):
    _end_drag(table, commit)
    _settle(qt_app, 600)


def _uids(queue):
    return [queue.uidAt(row) for row in range(queue.rowCount())]


def _card_shift(table):
    """How far right the block currently sits."""
    return table.property("_dragXOffset")


# --------------------------------------------------------------- the resistance


def test_a_small_pull_moves_nothing(table):
    """A vertical drag is never perfectly vertical.

    Without a dead zone the row creeps sideways under a hand that only meant to
    reorder, which reads as the list being loose rather than as a gesture.
    """
    tbl, qt_app, _ = table
    _grab(tbl, 4)
    _pull(tbl, qt_app, 3)

    assert tbl.property("_dragXOffset") == 0
    _release(tbl, qt_app, commit=False)


def test_the_resistance_grows_until_the_card_stops(table):
    """Equal pulls have to buy less and less travel, and eventually none.

    A linear offset would be the same gesture with no resistance in it, and a
    card that never stopped would give the boundary nothing to be a boundary
    between.
    """
    tbl, qt_app, _ = table
    _grab(tbl, 4)

    offsets = []
    for dx in (20, 40, 60, 80, 100):
        _pull(tbl, qt_app, dx)
        offsets.append(tbl.property("_dragXOffset"))

    gains = [b - a for a, b in zip(offsets, offsets[1:])]
    assert all(0 <= later < earlier for earlier, later in zip(gains, gains[1:])), (
        f"the resistance does not grow: gains were {gains}"
    )
    assert offsets == sorted(offsets), "the card went backwards"
    assert not tbl.property("_dismissArmed"), "still short of the boundary"

    # And it stops. Past the boundary the resisted term buys nothing at all —
    # which is what "the resistance reaches its maximum" has to mean if the snap
    # is going to read as a snap.
    _pull(tbl, qt_app, 200)
    plateau = tbl.property("_dragXResisted")
    assert plateau == pytest.approx(tbl.property("_dismissTravel"))
    for dx in (400, 800, 4000):
        _pull(tbl, qt_app, dx)
        assert tbl.property("_dragXResisted") == pytest.approx(plateau)

    _release(tbl, qt_app, commit=False)


def test_the_boundary_is_where_the_threshold_says_it_is(table):
    tbl, qt_app, _ = table
    slack = tbl.property("_dismissSlack")
    threshold = tbl.property("_dismissThreshold")
    _grab(tbl, 4)

    _pull(tbl, qt_app, slack + threshold - 1)
    assert not tbl.property("_dismissArmed")

    _pull(tbl, qt_app, slack + threshold)
    assert tbl.property("_dismissArmed")

    _release(tbl, qt_app, commit=False)


def test_past_the_boundary_the_card_follows_the_pointer_exactly(table):
    """The requirement the snap exists to deliver.

    A Behavior on the card's own offset would smooth this, and the row would lag
    the hand for as long as the pull lasted — which is why the easing lives on
    ``_snap`` and the offset is an unbuffered function of it.
    """
    tbl, qt_app, _ = table
    _grab(tbl, 4)
    _pull(tbl, qt_app, 300)
    _settle_snap(tbl, qt_app)

    _pull(tbl, qt_app, 300)
    before = tbl.property("_dragXOffset")
    _pull(tbl, qt_app, 340)
    after = tbl.property("_dragXOffset")

    assert after - before == pytest.approx(40), "the card is not tracking 1:1"
    _release(tbl, qt_app, commit=False)


def test_coming_back_over_the_boundary_puts_the_resistance_back(table):
    """The boundary is symmetric, so an armed pull can be talked out of."""
    tbl, qt_app, _ = table
    _grab(tbl, 4)

    _pull(tbl, qt_app, 60)
    resisted = tbl.property("_dragXOffset")

    _pull(tbl, qt_app, 300)
    _settle_snap(tbl, qt_app)
    assert tbl.property("_dismissArmed")

    _pull(tbl, qt_app, 60)
    _settle_snap(tbl, qt_app)
    assert not tbl.property("_dismissArmed")
    assert tbl.property("_dragXOffset") == pytest.approx(resisted), (
        "the resistance is applied again from where the pointer now is"
    )

    _release(tbl, qt_app, commit=False)


# ------------------------------------------------------------------ the release


def test_letting_go_past_the_boundary_removes_the_row(table):
    tbl, qt_app, queue = table
    before = _uids(queue)
    _grab(tbl, 4)
    _pull(tbl, qt_app, 300)
    _settle_snap(tbl, qt_app)

    _release(tbl, qt_app)

    assert _uids(queue) == before[:4] + before[5:]
    assert not tbl.property("_dragging"), "the gesture cleaned up after itself"
    assert tbl.property("_dragMode") == ""


def test_letting_go_short_of_the_boundary_removes_nothing(table):
    """The other half of the boundary being worth having."""
    tbl, qt_app, queue = table
    before = queue.rowCount()
    _grab(tbl, 4)
    _pull(tbl, qt_app, 60)

    _release(tbl, qt_app)

    assert queue.rowCount() == before


def test_a_pull_that_is_taken_back_removes_nothing(table):
    """Armed, then talked out of it, then released."""
    tbl, qt_app, queue = table
    before = _uids(queue)
    _grab(tbl, 4)
    _pull(tbl, qt_app, 300)
    _settle_snap(tbl, qt_app)
    _pull(tbl, qt_app, 40)
    _settle_snap(tbl, qt_app)

    _release(tbl, qt_app)

    assert _uids(queue) == before


def test_a_multi_row_pull_carries_the_whole_selection(table):
    """The same rule the reorder follows: a grip on a selected row carries the
    selection, and a scattered one lands as one departure rather than three.
    """
    tbl, qt_app, queue = table
    picked = [queue.uidAt(row) for row in (2, 5, 6)]
    queue.setSelectedMultiple(picked)
    _settle(qt_app, 120)

    _grab(tbl, 5)
    assert sorted(tbl.property("_dragRows").toVariant()) == [2, 5, 6]
    _pull(tbl, qt_app, 300)
    _settle_snap(tbl, qt_app)
    _release(tbl, qt_app)

    left = _uids(queue)
    assert queue.rowCount() == 9
    assert not any(uid in left for uid in picked)


def test_a_pull_on_an_unselected_row_carries_only_that_row(table):
    """A grip is not a click — it neither selects nor deselects — so a pull on a
    row outside the selection must not take the selection with it.
    """
    tbl, qt_app, queue = table
    picked = [queue.uidAt(row) for row in (1, 2)]
    queue.setSelectedMultiple(picked)
    _settle(qt_app, 120)
    doomed = queue.uidAt(7)

    _grab(tbl, 7)
    assert tbl.property("_dragRows").toVariant() == [7]
    _pull(tbl, qt_app, 300)
    _settle_snap(tbl, qt_app)
    _release(tbl, qt_app)

    left = _uids(queue)
    assert doomed not in left
    assert all(uid in left for uid in picked), "the selection is untouched"


# ------------------------------------------------------------------- one or other
#
# The grip does two jobs and a drag may only ever be doing one of them. Both
# directions have to be asked, because the failure is silent either way: a drag
# that quietly does both looks right until the release, and then does the wrong
# thing.


def test_a_reorder_cannot_be_turned_into_a_removal(table):
    """Pull a reordering drag as far right as you like. Nothing comes out."""
    tbl, qt_app, queue = table
    before = queue.rowCount()
    _grab(tbl, 4, mode="reorder")

    _pull(tbl, qt_app, 400)
    _settle(qt_app, 200)

    assert _card_shift(tbl) == 0, "a reorder moved a card sideways"
    assert not tbl.property("_dismissArmed"), "a reorder armed the removal"

    _release(tbl, qt_app)
    assert queue.rowCount() == before


def test_a_removal_cannot_be_turned_into_a_reorder(table):
    """Push a removing drag as far down as you like. Nothing is reordered."""
    tbl, qt_app, queue = table
    before = _uids(queue)
    _grab(tbl, 4, mode="dismiss")

    _push_down(tbl, qt_app, 4 * tbl.property("rowHeight"))
    _settle(qt_app, 200)

    _pull(tbl, qt_app, 40)
    _release(tbl, qt_app)

    assert _uids(queue) == before, "a removal reordered the queue"


def test_the_grip_reads_the_direction_the_hand_went_first():
    """The decision itself, taken straight out of the source.

    It lives in the MouseArea rather than the table — only the grip can see the
    press point — so there is no property to interrogate at runtime. The rule is
    small enough to read, and wrong enough to matter if it flips.
    """
    import re

    from app.gui.gui_paths import bundled_gui_root

    source = (
        bundled_gui_root() / "nocturne" / "components" / "tracks" / "TrackTable.qml"
    ).read_text()

    match = re.search(
        r'\(dx > 0 && dx > Math\.abs\(dy\)\)\s*\?\s*"dismiss"\s*:\s*"reorder"', source
    )
    assert match, (
        "the mode decision is gone or changed shape: rightward and more "
        "sideways than vertical removes, everything else reorders"
    )


# ---------------------------------------------------------------- the two glides
#
# "It arrives" is true of a teleport too, so these sample partway through rather
# than at the ends. Both endings used to fail here: the spring back held its
# offset for the whole glide and then snapped home, and the throw raced a
# Behavior's `enabled` against its own target and could jump the whole way.


def test_the_card_glides_home_rather_than_snapping(table):
    tbl, qt_app, queue = table
    _grab(tbl, 4)
    _pull(tbl, qt_app, 80)
    released_at = _card_shift(tbl)
    assert released_at > 1, "precondition: the card is out of its row"

    _end_drag(tbl, True)
    assert tbl.property("_springing"), "precondition: the spring is running"

    # Partway: somewhere between where the hand let go and home, and neither end.
    _settle(qt_app, 60)
    midway = tbl.property("_springX")
    assert 0 < midway < released_at, (
        f"the card jumped rather than glided: {released_at} -> {midway}"
    )

    _settle(qt_app, 400)
    assert tbl.property("_springX") == 0
    assert not tbl.property("_springing")
    assert not tbl.property("_dragging"), "the drag ends when the card lands"
    assert queue.rowCount() == 12, "a spring back is not a removal"


def test_the_throw_hands_straight_to_the_exit_transition(table):
    """No timer in between, which is what makes it the context menu's departure.

    The row is removed on the release itself and leaves through `remove`, from
    wherever the hand left it — the offset is held by uid so the exit can carry
    on rather than yanking the row back to the left edge to start again.
    """
    tbl, qt_app, queue = table
    doomed = queue.uidAt(4)
    _grab(tbl, 4)
    _pull(tbl, qt_app, 300)
    _settle_snap(tbl, qt_app)
    released_at = _card_shift(tbl)

    _end_drag(tbl, True)

    # Synchronously, with no event loop turn in between.
    assert queue.rowCount() == 11, "the removal waited for a timer"
    assert doomed not in _uids(queue)

    held = tbl.property("_dismissedShift").toVariant()
    assert held[str(doomed)] == pytest.approx(released_at), (
        "the exit was not told where the hand let go"
    )

    _settle(qt_app, 600)
    assert tbl.property("_dismissedShift").toVariant() == {}, (
        "the held offset is forgotten"
    )


# ---------------------------------------------------------------------- the grip
#
# The cursor is the only thing that says the number column is a handle before you
# press it, and the QML property alone does not prove Qt was told: what matters is
# the cursor actually on the item, which is what `QQuickItem::setCursor` writes and
# what the window's cursor walk reads back.
#
# That walk skips items that are hidden or disabled and does not descend into a
# subtree with no cursor in it — so `visible: enabled`, which this used to carry,
# took the handle out of the search every time it was momentarily disabled. Hence
# the `visible` assertion below, which is the regression, not a formality.


def _grips(window):
    """Every grip MouseArea on a row that is actually on screen.

    Delegates hang off the ListView's `contentItem`, which a plain QObject walk
    from the window does not reach — so this goes through the list itself.

    Pooled rows are skipped. `reuseItems` keeps spare delegates parented and
    hidden, and their grips are hidden with them: that is the view holding a row
    in reserve, not a handle refusing to show a cursor, and asserting on them
    would be asking the wrong question of the right property.
    """
    from PySide6.QtCore import QObject

    out = []
    for lst in window.findChildren(QObject, "trackList"):
        content = lst.property("contentItem")
        if content is None:
            continue
        for delegate in content.childItems():
            if not delegate.property("visible"):
                continue
            for obj in _descendants(delegate):
                if not obj.metaObject().className().startswith("QQuickMouseArea"):
                    continue
                if obj.property("cursorShape") in (
                    Qt.CursorShape.OpenHandCursor,
                    Qt.CursorShape.ClosedHandCursor,
                ):
                    out.append(obj)
    return out


def test_the_grip_asks_for_a_hand(table, gui):
    window = gui[0]

    grips = _grips(window)
    assert grips, "no grip is asking for a hand cursor"

    for grip in grips:
        assert grip.property("visible"), (
            "the cursor walk skips hidden items, so a handle on a row you can "
            "see must not hide itself — which is what `visible: enabled` did"
        )
        assert grip.property("width") > 0 and grip.property("height") > 0
        # The property is the request; this is what Qt was actually handed.
        assert grip.cursor().shape() == Qt.CursorShape.OpenHandCursor


def test_the_hand_closes_while_the_row_is_being_dragged(table, gui):
    window = gui[0]
    tbl, qt_app, _ = table

    _grab(tbl, 4)
    _pull(tbl, qt_app, 40)
    qt_app.processEvents()

    closed = [
        g for g in _grips(window)
        if g.cursor().shape() == Qt.CursorShape.ClosedHandCursor
    ]
    assert closed, "nothing showed a closed hand while a row was in the block"

    _release(tbl, qt_app, commit=False)
    _settle(qt_app, 400)


def test_the_playing_row_offers_no_hand(table, gui):
    """Queue row 0 is what is playing and is staying there, so it has no grip —
    and must not claim a cursor that says otherwise.
    """
    window = gui[0]
    tbl, qt_app, queue = table

    for grip in _grips(window):
        # `canDrag` is false on the playing row, and the shape follows it.
        row_item = grip.parent().parent().parent()
        if row_item.property("isPlaying"):
            assert grip.cursor().shape() == Qt.CursorShape.ArrowCursor


# ------------------------------------------------------------- hovering is not
#
# A drag begins with a press. That sounds like it needs no saying until
# `positionChanged` is reached without one — which is what `hoverEnabled` does —
# and the handler measures the pointer against a `pressX`/`pressY` pair that no
# press ever set. The distance is nonsense, so it clears the 5px arm instantly,
# and the queue reorders itself under a hand that was only passing over the grip.
#
# These drive real mouse events through the window rather than calling the
# handler, because the bug was in *when* Qt calls it. And they force `hoverEnabled`
# back on first, so what is under test is the guard rather than the absence of the
# property that exposed the need for it.


def _visible_grips_with_rows(window):
    """(delegate, grip) for every grip on a row that is on screen."""
    from PySide6.QtCore import QObject

    out = []
    for lst in window.findChildren(QObject, "trackList"):
        content = lst.property("contentItem")
        if content is None:
            continue
        for delegate in content.childItems():
            if not delegate.property("visible"):
                continue
            for obj in _descendants(delegate):
                if not obj.metaObject().className().startswith("QQuickMouseArea"):
                    continue
                if obj.property("cursorShape") in (
                    Qt.CursorShape.OpenHandCursor,
                    Qt.CursorShape.ClosedHandCursor,
                ):
                    out.append((delegate, obj))
    return sorted(out, key=lambda pair: pair[0].property("y"))


def _grip_centre(grip):
    from PySide6.QtCore import QPoint

    scene = grip.mapToScene(
        QPoint(int(grip.property("width") / 2), int(grip.property("height") / 2))
    )
    return QPoint(int(scene.x()), int(scene.y()))


def test_hovering_the_handle_does_not_touch_the_queue(table, gui):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    window = gui[0]
    tbl, qt_app, queue = table

    grips = _visible_grips_with_rows(window)
    assert len(grips) > 4, "no rows on screen to hover"
    # The guard has to hold on its own, not because hover happens to be off.
    for _, grip in grips:
        grip.setProperty("hoverEnabled", True)
    _settle(qt_app, 120)

    before = _uids(queue)
    centre = _grip_centre(grips[4][1])

    # Across the handle and off it, diagonally, with no button down.
    for step in range(0, 60, 3):
        QTest.mouseMove(window, QPoint(centre.x() + step, centre.y() + step))
        qt_app.processEvents()
    _settle(qt_app, 300)

    assert not tbl.property("_dragging"), "hovering started a drag"
    assert tbl.property("_dragMode") == ""
    assert _uids(queue) == before, "hovering reordered the queue"


def test_scrolling_under_a_hovering_pointer_does_not_reorder(table, gui):
    """The same bug's other face.

    ``_dragY`` folds `contentY` in, so once a phantom drag is armed the list
    scrolling under the pointer moves the drop destination — and the order
    changes with nothing touched but the wheel.
    """
    from PySide6.QtCore import QObject
    from PySide6.QtTest import QTest

    window = gui[0]
    tbl, qt_app, queue = table

    grips = _visible_grips_with_rows(window)
    for _, grip in grips:
        grip.setProperty("hoverEnabled", True)
    _settle(qt_app, 120)

    before = _uids(queue)
    QTest.mouseMove(window, _grip_centre(grips[4][1]))
    qt_app.processEvents()

    lst = window.findChildren(QObject, "trackList")[0]
    for offset in (40, 90, 150, 90, 0):
        lst.setProperty("contentY", lst.property("originY") + offset)
        qt_app.processEvents()
    _settle(qt_app, 300)

    assert not tbl.property("_dragging")
    assert _uids(queue) == before, "scrolling under a hover reordered the queue"


# ------------------------------------------------------------------- playlists
#
# The other list with an order worth changing. It shares every line of the
# gesture with the queue — the arithmetic above is the same arithmetic — so what
# is asked here is only the two things that differ: that the handles are there at
# all, and the one rule that is deliberately *not* the queue's.
#
# On the queue, the playing track is row 0 and it is staying there, so it keeps
# its meter and offers no grip. A playlist row has no such invariant: playing a
# playlist copies its ids into the queue and the two go their own way, so every
# row of one has a handle, including whichever is currently playing.

def test_a_playlist_offers_the_same_handles(gui):
    """`reorderable` is what turns the number column into a grab handle."""
    from PySide6.QtCore import QObject

    window = gui[0]

    tables = [
        obj
        for obj in _descendants(window)
        if obj.property("mode") == "playlist"
    ]
    assert len(tables) == 1, "the playlist track table is gone or duplicated"
    assert tables[0].property("reorderable") is True, (
        "the playlist's rows have no grip"
    )
    assert tables[0].property("showIndex") is True, (
        "the grip lives in the number slot, which has to be there"
    )


def test_the_queue_keeps_its_protected_row(table):
    """The rule that had to become queue-only, checked from both ends.

    Making `_minDropRow` mode-aware is the one change here that could silently
    cost the queue its "row 0 is playing and is staying" protection. Nothing is
    playing in this fixture — starting real playback needs real audio files, and
    these tracks are rows in a database — so the runtime half can only assert the
    idle case. The other half is asked of the source, because the conjunct is
    exactly what a careless simplification would drop.
    """
    import re

    from app.gui.gui_paths import bundled_gui_root

    tbl, _qt_app, _queue = table
    assert tbl.property("mode") == "queue"
    assert tbl.property("_minDropRow") == 0, "nothing is playing, so nothing is reserved"

    source = (
        bundled_gui_root() / "nocturne" / "components" / "tracks" / "TrackTable.qml"
    ).read_text()
    match = re.search(
        r'_minDropRow:\s*\n?\s*\(mode === "queue" && playerVM\.has_current_track\)'
        r'\s*\?\s*1\s*:\s*0',
        source,
    )
    assert match, (
        "the queue reserves row 0 while something is playing, and only the "
        "queue does — one of those two halves is gone"
    )

    # And the grip is refused on exactly the row nothing may be dropped on.
    assert re.search(
        r"canDrag:\s*\n?\s*root\.reorderable && rowItem\.rowIndex >= root\._minDropRow",
        source,
    ), "canDrag no longer asks _minDropRow, so the two rules can drift apart"


def test_a_playlist_never_reserves_a_row(gui):
    """`_minDropRow` is a queue rule; on a playlist it is always 0."""
    window = gui[0]

    playlist_tables = [
        obj for obj in _descendants(window) if obj.property("mode") == "playlist"
    ]
    assert playlist_tables, "no playlist table"
    assert playlist_tables[0].property("_minDropRow") == 0
