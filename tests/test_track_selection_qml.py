"""Joined selections through the real table, including reuse and row changes."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Q_ARG, QMetaObject, QObject, Qt, QUrl, Slot
from PySide6.QtQml import QQmlApplicationEngine, QQmlProperty
from PySide6.QtTest import QTest

from app.library.list_model import TrackListModel


class CountingModel(TrackListModel):
    uid_reads = 0

    @Slot(int, result=int)
    def uidAt(self, row):  # noqa: N802
        self.uid_reads += 1
        return super().uidAt(row)

    def insert(self, row, uid):
        self.beginInsertRows(self.index(-1), row, row)
        self._ids.insert(row, self._ids[0])
        self._uids.insert(row, uid)
        self.endInsertRows()

    def remove(self, row):
        self.beginRemoveRows(self.index(-1), row, row)
        self._ids.pop(row)
        self._uids.pop(row)
        self.endRemoveRows()

    def move(self, source, destination):
        self.beginMoveRows(self.index(-1), source, source, self.index(-1),
                           destination + (destination > source))
        self._ids.insert(destination, self._ids.pop(source))
        self._uids.insert(destination, self._uids.pop(source))
        self.endMoveRows()


def call(obj, name, *args):
    QMetaObject.invokeMethod(
        obj, name, Qt.ConnectionType.DirectConnection,
        *[Q_ARG("QVariant", value) for value in args],
    )


@pytest.fixture
def table(qt_app, tmp_path):
    from app.bootstrap import create_service_registry
    from app.gui.gui_paths import bundled_gui_root
    from app.main import CONTEXT_PROPERTIES, _register_qml_types

    registry = create_service_registry()
    _register_qml_types(registry)
    track_id = registry.get_service("track_service").create_track_with_relationships(
        {"title": "Same recording", "artists": "An artist"}
    )
    model = CountingModel(row_cache=registry.get_service("row_cache"))
    # Repeated track IDs deliberately exercise per-instance selection identity.
    model.set_ids([track_id] * 100, uids=list(range(1000, 1100)))
    engine = QQmlApplicationEngine()
    directory = bundled_gui_root() / "nocturne"
    engine.addImportPath(str(directory))
    context = engine.rootContext()
    for qml_name, service in CONTEXT_PROPERTIES.items():
        context.setContextProperty(qml_name, registry.get_service(service))
    context.setContextProperty("testModel", model)
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
    harness = tmp_path / "SelectionHarness.qml"
    harness.write_text(f'''
import QtQuick
import Nocturne
import "{(directory / 'components' / 'tracks').as_uri()}"
Window {{
    width: 900; height: 300; visible: true
    TrackTable {{
        objectName: "table"
        anchors.fill: parent
        listVM: testModel
        showArt: false
    }}
}}
''')
    engine.load(QUrl.fromLocalFile(str(harness)))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    root = window.findChild(QObject, "table")
    view = root.findChild(QObject, "trackList")

    def settle():
        for _ in range(10):
            qt_app.processEvents()

    def row(index):
        settle()
        # Qt constructs buffered delegates over rendering frames; pumping the
        # event queue alone can finish before the first buffer frame arrives.
        for _ in range(50):
            if any(item.property("rowIndex") == index
                   for item in view.property("contentItem").childItems()):
                break
            QTest.qWait(20)
        return next(item for item in view.property("contentItem").childItems()
                    if item.property("rowIndex") == index)

    def backdrop(index):
        card = next(item for item in row(index).childItems()
                    if 0 < item.property("width") < view.property("width")
                    and any(child.property("topLeftRadius") is not None
                            for child in item.childItems()))
        return next(child for child in card.childItems()
                    if child.property("topLeftRadius") is not None)

    def select(*indices):
        call(root, "clearSelection")
        for index in indices:
            call(root, "handleClick", model.uidAt(index), True, False)
        settle()

    settle()
    yield root, view, model, row, backdrop, select, settle
    assert not warnings, warnings
    window.close()
    del engine


@pytest.mark.parametrize("indices,rounded", [
    ([0], {0: (True, True)}),
    ([0, 1], {0: (True, False), 1: (False, True)}),
    ([0, 1, 2], {0: (True, False), 1: (False, False), 2: (False, True)}),
    ([0, 1, 3, 4, 6], {0: (True, False), 1: (False, True),
                       3: (True, False), 4: (False, True), 6: (True, True)}),
])
def test_only_outer_corners_are_rounded(table, indices, rounded):
    root, view, model, row, backdrop, select, settle = table
    select(*indices)
    for index, (top, bottom) in rounded.items():
        rect = backdrop(index)
        for corner in ("topLeftRadius", "topRightRadius"):
            assert (rect.property(corner) > 0) is top
        for corner in ("bottomLeftRadius", "bottomRightRadius"):
            assert (rect.property(corner) > 0) is bottom
        assert QQmlProperty(rect, "border.width").read() == 0
        assert rect.property("y") == 0
        assert rect.property("height") == row(index).property("height")
        if not top and not bottom:
            assert rect.property("antialiasing") is False
    for index in range(7):
        assert row(index).property("selected") is (index in indices)
    assert row(1).property("y") == row(0).property("y") + row(0).property("height")


def test_backend_selection_and_clear_update_the_corners(table):
    root, view, model, row, backdrop, select, settle = table
    model.setSelectedMultiple([1000, 1001, 1002])
    settle()
    assert backdrop(1).property("radius") == 0
    model.setSelectedMultiple([1000, 1002])
    settle()
    assert backdrop(0).property("bottomLeftRadius") > 0
    assert backdrop(2).property("topLeftRadius") > 0
    call(root, "clearSelection")
    settle()
    assert all(not row(i).property("selected") for i in range(3))


def test_reorder_reset_insert_and_remove_refresh_adjacency(table):
    root, view, model, row, backdrop, select, settle = table
    select(0, 1)
    model.insert(1, 2000)
    settle()
    assert backdrop(0).property("bottomLeftRadius") > 0
    assert backdrop(2).property("topLeftRadius") > 0
    model.remove(1)
    settle()
    assert backdrop(0).property("bottomLeftRadius") == 0
    model.move(1, 3)
    settle()
    assert backdrop(0).property("bottomLeftRadius") > 0
    assert backdrop(3).property("topLeftRadius") > 0
    uids = [1000, 1001] + list(range(1002, 1100))
    model.set_ids(list(model._ids), uids=uids)
    settle()
    assert backdrop(0).property("bottomLeftRadius") == 0
    assert backdrop(1).property("topLeftRadius") == 0


def test_scrolling_reuses_cached_shapes_without_uid_queries(table):
    root, view, model, row, backdrop, select, settle = table
    select(*range(100))
    model.uid_reads = 0
    view.setProperty("contentY", 50 * root.property("rowHeight"))
    settle()
    assert row(50).property("selected") is True
    assert backdrop(50).property("topLeftRadius") == 0
    assert backdrop(50).property("bottomLeftRadius") == 0
    view.setProperty("contentY", 99 * root.property("rowHeight"))
    settle()
    assert backdrop(99).property("bottomLeftRadius") > 0
    assert model.uid_reads == 0
