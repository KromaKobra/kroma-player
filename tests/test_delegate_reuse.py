"""The contract a pooled track row has to keep.

`TrackTable`'s ListView sets `reuseItems`, so a row leaving the viewport is
returned to a pool and handed to a different track later instead of being
destroyed. That is what makes a fast scroll affordable — the delegate is on the
order of fifty QObjects, including a MultiEffect and a cell per visible column,
and building that per row is what a scrollbar drag could not keep up with.

The cost is a rule that is easy to break by accident: **anything assigned to a
delegate imperatively, rather than bound, survives into the next row that gets
that object.** The `remove` transition assigns three such properties on the way
out — opacity, x, and z — and before pooling none of them mattered, because the
row was destroyed immediately afterwards. Pooled, each one is a permanent
visual bug on some unrelated row later: invisible, parked off the right-hand
edge, or drawn on top of its neighbours.

`z` is the subtle one. A `PropertyAction` does not just write a value, it
*destroys the binding underneath it*, so restoring it means handing back a
`Qt.binding` — assigning 0 would fix the symptom and leave the next drag unable
to raise the row it is carrying.

These are mostly source checks, like `test_cover_masks`. Catching the real thing
needs a GPU, a populated library and a minute of dragging; what is cheap to
protect is the wiring, and in particular the property list staying in step with
the transition it mirrors. The one runtime check at the bottom covers the gap
those cannot: that the property is set on the object QML actually builds, and
not merely present in the file.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from test_cover_masks import _block_at  # noqa: E402
from test_gui_packages import CONTEXT_PROPERTIES, backend  # noqa: E402,F401

TRACK_TABLE = REPO_ROOT / "gui" / "nocturne" / "components" / "TrackTable.qml"


def _source() -> str:
    return TRACK_TABLE.read_text(encoding="utf-8")


def _block_after(source: str, pattern: str) -> str:
    """The braced block introduced by the first match of *pattern*."""
    match = re.search(pattern, source)
    assert match, f"{TRACK_TABLE.name}: no match for {pattern!r} — did it move?"
    return source[slice(*_block_at(source, match.end() - 1))]


def _properties_written_by_exit(source: str) -> set[str]:
    """Every property the `remove` transition assigns, however it assigns it.

    Both spellings count: `PropertyAction { property: "z" }` and
    `NumberAnimation { property: "opacity" }` leave the value behind in exactly
    the same way once the row is pooled rather than destroyed.
    """
    block = _block_after(source, r"remove:\s*Transition\s*\{")
    return set(re.findall(r"propert(?:y|ies):\s*\"([^\"]+)\"", block))


def test_rows_are_pooled():
    assert re.search(r"reuseItems:\s*true", _source()), (
        "TrackTable no longer pools its rows. If that was deliberate the rest of "
        "this module is obsolete; if it was not, a fast scroll is rebuilding the "
        "whole delegate per row again."
    )


def test_every_property_the_exit_writes_is_reset_on_reuse():
    """The list of properties to restore is derived, not hardcoded.

    So adding a fourth animated property to the exit transition fails here
    rather than shipping as an intermittent visual bug on unrelated rows.
    """
    source = _source()
    written = _properties_written_by_exit(source)
    assert written, "the remove transition animates nothing — did it move?"

    reused = _block_after(source, r"ListView\.onReused:\s*\{")
    for prop in sorted(written):
        assert re.search(rf"\b{re.escape(prop)}\s*=", reused), (
            f"the exit transition writes `{prop}`, but ListView.onReused does not "
            f"put it back. A pooled row keeps whatever it was left holding, so the "
            f"next track handed this delegate inherits that row's {prop}."
        )


def test_z_is_restored_as_a_binding():
    """PropertyAction destroys the binding; only a binding restores it."""
    source = _source()
    assert 'PropertyAction { property: "z"' in source, (
        "the exit no longer clobbers z with a PropertyAction — if it now animates "
        "z instead, this test should check that spelling rather than be deleted"
    )

    reused = _block_after(source, r"ListView\.onReused:\s*\{")
    match = re.search(r"\bz\s*=\s*(.+)", reused)
    assert match, "ListView.onReused does not restore z"
    assert "Qt.binding" in match.group(1), (
        "z is restored by assignment, which leaves it a constant. The binding "
        "PropertyAction destroyed is what raises a row while it is being dragged, "
        "so this row would stay flat under the drag block forever."
    )


def test_a_recycled_row_releases_the_drag_it_was_holding():
    """The safety net has to cover both ways a delegate can go away.

    A row that is pooled is never destroyed, so `Component.onDestruction` alone
    would mean this check silently stopped running in the one case it names —
    the view recycling the delegate that holds the mouse grab, which otherwise
    leaves the list frozen with `interactive: false` and nothing able to release
    it.
    """
    source = _source()
    pooled = re.search(r"ListView\.onPooled:\s*(\w+)\(\)", source)
    destroyed = re.search(r"Component\.onDestruction:\s*(\w+)\(\)", source)

    assert pooled, "no ListView.onPooled handler on the row delegate"
    assert destroyed, "no Component.onDestruction handler on the row delegate"
    assert pooled.group(1) == destroyed.group(1), (
        "pooling and destruction run different teardown. They are the same event "
        "as far as a live drag is concerned, and two copies of that rule is one "
        "copy too many."
    )

    body = _block_after(source, rf"function\s+{destroyed.group(1)}\s*\(\)\s*\{{")
    assert "_endDrag" in body, "the shared teardown no longer ends the drag"


def test_reuse_suppresses_the_row_s_own_animations():
    """A row changing identity must not animate its way into the new one.

    Every binding reading the row's identity re-evaluates at once when a pooled
    delegate is reused, and a Behavior turns each of those into an animation
    *from the previous track's value* — a selected row's accent draining out of
    an unselected one, the playing row's title colour bleeding off a track that
    is not playing. It happens once per recycled row, so during the fast scroll
    pooling exists to enable it happens to every row on screen.
    """
    source = _source()
    reused = _block_after(source, r"ListView\.onReused:\s*\{")

    assert re.search(r"_settling\s*=\s*true", reused), (
        "ListView.onReused does not raise the settle flag, so a reused row "
        "animates its way from the previous track's appearance into its own"
    )
    assert "Qt.callLater" in reused, (
        "the settle flag is raised but never lowered on a later turn, which "
        "would leave every recycled row's animations off for good"
    )

    # Matched on the negated read rather than on `enabled:`, because one of the
    # guards is a term in a multi-line condition rather than the whole of one.
    gates = re.findall(r"!\s*rowItem\._settling", source)
    assert len(gates) >= 4, (
        f"only {len(gates)} Behavior(s) consult the settle flag. Each one that "
        "does not is an animation played on a row that did not change — check "
        "any Behavior whose property is bound to rowItem.selected, "
        "rowItem.isPlaying, rowItem.stateOpacity or root._shiftFor."
    )


def test_a_reused_row_takes_on_its_new_identity():
    """`rowIndex` and `rowUid` are captured, not bound, so reuse must re-capture.

    They exist because a departing row's `index` goes to -1 and its roles stop
    answering while it is still being drawn. That makes them plain properties
    rather than bindings — and a plain property on a pooled object is exactly
    what does not update itself.
    """
    source = _source()
    completed = re.search(r"Component\.onCompleted:\s*(\w+)\(\)", source)
    assert completed, "the row no longer adopts its identity on completion"

    reused = _block_after(source, r"ListView\.onReused:\s*\{")
    assert f"{completed.group(1)}()" in reused, (
        f"Component.onCompleted calls {completed.group(1)}() but ListView.onReused "
        "does not. A pooled row is completed once and reused many times, so the "
        "second path is the one that runs for most rows the user sees."
    )


# ======================================================================
# The same thing, on the object QML actually builds
# ======================================================================


def test_the_live_track_list_pools_its_rows(qt_app, backend):
    """The source says `reuseItems: true`; this checks the view agrees.

    Worth doing separately because the two can disagree. A property set inside
    a block that never gets instantiated, or shadowed by an assignment further
    down, reads exactly the same in the file and does nothing at runtime.

    Delegates themselves are deliberately not inspected here. A ListView only
    builds them once it is inside a window and has been through a render pass,
    which needs a GL context this suite does not assume — so what a recycled row
    looks like stays a manual check, and what is pinned here is that recycling
    is switched on at all.
    """
    from PySide6.QtCore import QObject, QUrl
    from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent

    from app.artwork.cover_provider import CoverImageProvider
    from app.gui.gui_paths import bundled_gui_root
    from app.library.track_query import TrackQuery

    nocturne = bundled_gui_root() / "nocturne"
    engine = QQmlApplicationEngine()
    engine.addImageProvider(
        "cover", CoverImageProvider(backend.get_service("cover_store"))
    )

    tracks = TrackQuery()
    tracks.refresh()

    context = engine.rootContext()
    context.setContextProperty("probeTracks", tracks)
    for qml_name, service in CONTEXT_PROPERTIES.items():
        context.setContextProperty(qml_name, backend.get_service(service))
    engine.addImportPath(str(nocturne))

    warnings: list = []
    engine.warnings.connect(lambda w: warnings.extend(w))

    harness = """
    import QtQuick
    import Nocturne
    import "components"

    Item {
        width: 900; height: 600
        TrackTable {
            anchors.fill: parent
            mode: "library"
            listVM: probeTracks
            active: true
        }
    }
    """
    component = QQmlComponent(engine)
    component.setData(harness.encode(), QUrl.fromLocalFile(str(nocturne) + "/probe.qml"))
    assert component.status() == QQmlComponent.Status.Ready, component.errorString()

    root = component.create()
    assert root is not None, component.errorString()
    qt_app.processEvents()

    lists = [c for c in root.findChildren(QObject) if c.objectName() == "trackList"]
    assert len(lists) == 1, "the row list is no longer findable by objectName"
    view = lists[0]

    assert view.property("count") == tracks.rowCount(), "the model did not reach the view"
    assert view.property("reuseItems") is True, (
        "the live ListView is not pooling its rows, whatever the source says"
    )
    assert not warnings, [w.toString() for w in warnings]

    tracks.detach()
