"""A GUI declaring its own track lists in QML.

This is the claim the whole first half of the redesign rests on: that the
backend no longer decides which views exist. If a throwaway QML file can
declare a ``TrackQuery`` and render it as a GridView — a view type nothing in
the backend has ever heard of — then it holds.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

from app.events.event_bus import EventBus  # noqa: E402
from app.library.context import LibraryContext, clear_library_context, set_library_context  # noqa: E402
from app.library.row_cache import TrackRowCache  # noqa: E402
from app.main import _register_qml_types  # noqa: E402
from app.repositories.track_repository import TrackRepository  # noqa: E402

from conftest import TRACK_COUNT


@pytest.fixture
def qml_env(qt_app, db):
    """A live library context plus the registered QML types."""
    repo = TrackRepository(db.session)
    set_library_context(
        LibraryContext(
            repository=repo,
            row_cache=TrackRowCache(repo, capacity=100_000),
            event_bus=EventBus(),
        )
    )
    _register_qml_types()
    yield
    clear_library_context()


def load_qml(qt_app, tmp_path, source: str):
    """Load a QML string and return (engine, root). Fails loudly on QML errors.

    Pumps the event loop afterwards because a declared query resolves on the
    first turn — that is how it sees the properties QML assigned rather than
    the defaults it was constructed with. A running application does this
    itself, before the first frame.
    """
    path = tmp_path / "Main.qml"
    path.write_text(source, encoding="utf-8")

    engine = QQmlApplicationEngine()
    warnings: list = []
    engine.warnings.connect(warnings.append)
    engine.load(QUrl.fromLocalFile(str(path)))

    roots = engine.rootObjects()
    assert roots, f"QML failed to load: {[w.toString() for w in warnings]}"

    qt_app.processEvents()
    return engine, roots[0]


def test_a_gui_can_declare_its_own_query(qt_app, qml_env, tmp_path):
    engine, root = load_qml(
        qt_app,
        tmp_path,
        """
        import QtQuick
        import Kroma.Library 1.0

        Item {
            property alias total: q.count
            property string sortedBy: q.sortField
            TrackQuery { id: q; sortField: "title" }
        }
        """,
    )
    assert root.property("total") == TRACK_COUNT
    assert root.property("sortedBy") == "title"
    del engine


def test_a_query_renders_as_a_gridview(qt_app, qml_env, tmp_path):
    """A view type the backend has never heard of."""
    engine, root = load_qml(
        qt_app,
        tmp_path,
        """
        import QtQuick
        import Kroma.Library 1.0

        Item {
            width: 400; height: 400
            property int shown: grid.count
            property string firstTitle: ""

            TrackQuery { id: library }

            GridView {
                id: grid
                anchors.fill: parent
                cellWidth: 100; cellHeight: 100
                model: library
                delegate: Item {
                    width: 100; height: 100
                    required property string title
                    required property int index
                    Component.onCompleted: if (index === 0) parent.parent.firstTitle = title
                }
            }
        }
        """,
    )
    assert root.property("shown") == TRACK_COUNT
    del engine


def test_filters_and_sort_are_settable_from_qml(qt_app, qml_env, tmp_path):
    engine, root = load_qml(
        qt_app,
        tmp_path,
        """
        import QtQuick
        import Kroma.Library 1.0

        Item {
            property alias matched: q.count
            TrackQuery {
                id: q
                filters: [{ field: "album", operator: "Equals", value: "Album 3" }]
                sortField: "duration"
                sortAscending: false
            }
        }
        """,
    )
    assert root.property("matched") == TRACK_COUNT // 60
    del engine


def test_two_independent_queries_coexist(qt_app, qml_env, tmp_path):
    """Three browse panes, no backend change — the point of the exercise."""
    engine, root = load_qml(
        qt_app,
        tmp_path,
        """
        import QtQuick
        import Kroma.Library 1.0

        Item {
            property alias allCount: all.count
            property alias oneAlbum: filtered.count

            TrackQuery { id: all }
            TrackQuery {
                id: filtered
                filters: [{ field: "album", operator: "Equals", value: "Album 7" }]
            }
        }
        """,
    )
    assert root.property("allCount") == TRACK_COUNT
    assert root.property("oneAlbum") == TRACK_COUNT // 60
    del engine
