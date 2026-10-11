"""Dragging an artist chip into a new place in a track's credits.

The gesture is the only way to arrange a credit order, and almost all of it is
geometry — which slot the pointer is over in a row that wraps — so a source check
would not say much. This builds the real component against the real `artistsVM`,
lays it out at a known width, and drives the same three calls the MouseArea makes.

`EntityChips` — the machinery behind `ArtistChips` and `AlbumChips` — holds a
`ListModel` rather than the JS array it used to, and that is load-bearing rather
than tidiness: reassigning an array makes a `Repeater` destroy every delegate and
build them again, which mid-drag destroys the chip the pointer is holding and
strands the gesture. `_dragTo` asks `itemAt(i)` where the chips are, so a
delegate that did not survive the last move answers nothing and the chip stops
following. That is what the reorder assertions here would catch.

It builds `ArtistChips` rather than `EntityChips` for a second reason: the drag
functions live on the inner component and the page calls them on the outer one,
so a wrapper that forgot to forward one would pass a test written against the
machinery and fail in the interface. Driving the wrapper is what pins the seam.
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
from app.models.track import Artist, Track  # noqa: E402

NOCTURNE = REPO_ROOT / "gui" / "nocturne"


@pytest.fixture
def backend(qt_app, tmp_path):
    """A registry over a throwaway vault holding one three-artist track."""
    from app.utils.library_paths import set_library_root

    vault = tmp_path / "venomvault"
    (vault / ".data").mkdir(parents=True)
    set_library_root(vault)

    from app.bootstrap import create_service_registry

    registry = create_service_registry()
    session = registry.get_service("db_session")

    artists = [Artist(name=name) for name in ("Ada", "Bea", "Cyd")]
    session.add_all(artists)
    session.flush()
    session.add(Track(title="One", duration_seconds=200, artists=artists))
    session.commit()
    registry.get_service("artists_view_model").refresh()
    return registry


@pytest.fixture
def chips(qt_app, backend):
    """A laid-out ArtistChips, its `changed` payloads, and the artist ids."""
    engine = QQmlEngine()
    engine.addImportPath(str(NOCTURNE))
    context = engine.rootContext()
    for qml_name, service in CONTEXT_PROPERTIES.items():
        context.setContextProperty(qml_name, backend.get_service(service))

    warnings: list = []
    engine.warnings.connect(lambda w: warnings.extend(w))

    component = QQmlComponent(
        engine, QUrl.fromLocalFile(str(NOCTURNE / "components" / "chips" / "ArtistChips.qml"))
    )
    item = component.create()
    assert item is not None, component.errorString()

    # Wide enough that three chips share one line, so a horizontal drag is the
    # whole gesture. The wrapped case is geometry this cannot reach without a
    # real window, and is why `_dragTo` tests the line before the side.
    item.setWidth(800)
    item.setHeight(120)

    emitted: list = []
    item.changed.connect(
        lambda ids, names: emitted.append((_js(ids), _js(names)))
    )

    repo = backend.get_service("artist_repository")
    ids = [int(repo.get_by_name(n).id) for n in ("Ada", "Bea", "Cyd")]
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
    """A QML value as a Python one.

    Anything crossing from QML as `var` arrives wrapped; QML itself unwraps it
    on the way to a `changed` handler, so this is the test's problem alone.
    """
    return value.toVariant() if hasattr(value, "toVariant") else value


def _model_names(item) -> list:
    """The chip names in model order — what `changed` would carry right now."""
    return list(_js(item.staged())["names"])


def _chip_at(item, index) -> QQuickItem:
    flow = _flow(item)
    assert flow is not None, "EntityChips no longer has an object-named chipFlow"
    kids = [c for c in flow.childItems() if c.property("entityId") is not None]
    kids.sort(key=lambda c: (round(c.y()), c.x()))
    return kids[index]


def _drag(qt_app, item, source, target):
    """Press the chip at *source* and let go over the one at *target*."""
    item._beginDrag(source)
    landing = _chip_at(item, target)
    # Just past the far edge of the target's midpoint, which is the side the
    # dragged chip has to cross for the swap to mean anything.
    x = landing.x() + (landing.width() * 0.9 if target > source else landing.width() * 0.1)
    item._dragTo(x, landing.y() + landing.height() / 2)
    _settle(qt_app, item)
    item._endDrag(True)
    _settle(qt_app, item)


def test_the_chips_load_in_credit_order(chips):
    item, emitted, ids, warnings = chips
    assert _model_names(item) == ["Ada", "Bea", "Cyd"], (
        "ArtistChips did not fill:\n" + "\n".join(w.toString() for w in warnings)
    )
    assert emitted == [], "filling the field is not an edit and must not report one"


def test_dragging_a_chip_to_the_end_reorders_and_reports_once(qt_app, chips):
    item, emitted, ids, _ = chips

    _drag(qt_app, item, 0, 2)

    assert _model_names(item) == ["Bea", "Cyd", "Ada"]
    assert len(emitted) == 1, "the order is committed once, on release — not per move"
    assert emitted[0][0] == [ids[1], ids[2], ids[0]]
    assert emitted[0][1] == ["Bea", "Cyd", "Ada"]


def test_dragging_backwards_works_the_same(qt_app, chips):
    item, emitted, ids, _ = chips

    _drag(qt_app, item, 2, 0)

    assert _model_names(item) == ["Cyd", "Ada", "Bea"]
    assert emitted[-1][0] == [ids[2], ids[0], ids[1]]


def test_a_cancelled_drag_puts_the_chip_back_and_reports_nothing(qt_app, chips):
    item, emitted, _, _ = chips

    item._beginDrag(0)
    landing = _chip_at(item, 2)
    item._dragTo(landing.x() + landing.width() * 0.9,
                 landing.y() + landing.height() / 2)
    _settle(qt_app, item)
    assert _model_names(item) == ["Bea", "Cyd", "Ada"], "the preview did not follow"

    item._endDrag(False)
    _settle(qt_app, item)

    assert _model_names(item) == ["Ada", "Bea", "Cyd"]
    assert emitted == [], "a cancelled gesture is not an edit"


def test_dropping_a_chip_where_it_started_reports_nothing(qt_app, chips):
    """Pressing, moving a little, and letting go is not an edit."""
    item, emitted, _, _ = chips

    item._beginDrag(1)
    item._endDrag(True)
    _settle(qt_app, item)

    assert _model_names(item) == ["Ada", "Bea", "Cyd"]
    assert emitted == []


def test_removing_a_chip_still_works_after_the_model_change(qt_app, chips):
    """`_removeAt` moved from splicing an array to a ListModel with it."""
    item, emitted, ids, _ = chips

    item._removeAt(1)
    _settle(qt_app, item)

    assert _model_names(item) == ["Ada", "Cyd"]
    assert emitted[-1][0] == [ids[0], ids[2]]
