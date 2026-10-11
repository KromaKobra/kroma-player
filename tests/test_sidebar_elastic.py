"""Held gestures can cross both thresholds repeatedly without committing."""
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QPointF, QUrl, Qt
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

GUI = Path(__file__).resolve().parents[1] / "gui" / "nocturne"


@pytest.fixture(params=[False, True], ids=["left", "right"])
def edge(qt_app, request):
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(GUI))
    warnings = []
    engine.warnings.connect(lambda messages: warnings.extend(m.toString() for m in messages))
    engine.loadData(b'''
import QtQuick
import QtQuick.Window
import "components"
Window {
    id: window
    width: 1000; height: 720; visible: true
    property bool rightEdge: false
    property bool closed: false
    property real preferred: rightEdge ? 320 : 240
    property int commits: 0
    Rectangle {
        id: panel
        objectName: "panel"
        width: handle.displayedWidth; height: parent.height
        x: window.rightEdge ? parent.width - width : 0
    }
    SidebarResizeHandle {
        id: handle
        objectName: "handle"
        x: window.rightEdge ? Math.min(panel.x - width / 2, window.width - width)
                            : panel.width - width / 2
        height: panel.height
        rightSidebar: window.rightEdge
        expandedWidth: window.preferred
        collapsed: window.closed
        collapsedWidth: window.rightEdge ? 0 : 64
        minimumExpandedWidth: window.rightEdge ? 240 : 200
        maximumWidth: window.rightEdge ? 520 : 360
        onResizeCommitted: function (preferred, collapsed) {
            window.commits += 1
            window.preferred = preferred
            window.closed = collapsed
        }
    }
}
''', QUrl.fromLocalFile(str(GUI / "elastic-sidebar-probe.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    handle = window.findChild(QQuickItem, "handle")
    panel = window.findChild(QQuickItem, "panel")
    handle.setProperty("reducedMotion", True)
    window.setProperty("rightEdge", request.param)
    handle.setProperty("reducedMotion", False)
    qt_app.processEvents()
    yield window, panel, handle
    window.close()
    assert not warnings, warnings
    del engine


def _start(window, handle):
    point = handle.mapToScene(QPointF(handle.width() / 2, handle.height() / 2)).toPoint()
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, point)
    assert handle.property("dragging")
    return point, handle.property("pointerWidth")


def _move(window, handle, start, raw_width):
    point, start_width = start
    sign = -1 if handle.property("rightSidebar") else 1
    end = point + QPoint(round(sign * (raw_width - start_width)), 0)
    QTest.mouseMove(window, end, 0)
    assert handle.property("pressed")
    assert handle.property("pointerWidth") == pytest.approx(raw_width, abs=0.5)
    return end


def _release(window, end):
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end)
    QTest.qWait(650)


def test_resistance_increases_and_release_returns_to_minimum(edge):
    window, panel, handle = edge
    minimum = handle.property("minimumExpandedWidth")
    start = _start(window, handle)
    _move(window, handle, start, minimum)
    assert panel.width() == minimum
    _move(window, handle, start, minimum - 10)
    first = minimum - panel.width()
    _move(window, handle, start, minimum - 20)
    second = minimum - panel.width()
    end = _move(window, handle, start, minimum - 30)
    third = minimum - panel.width()
    assert 0 < third - second < second - first < first < 10
    cue = handle.findChild(QQuickItem, "sidebarCollapseFeedback")
    assert cue.isVisible()
    assert cue.opacity() > 0
    assert window.property("commits") == 0
    _release(window, end)
    assert not window.property("closed")
    assert panel.width() == minimum
    assert window.property("preferred") == minimum
    assert window.property("commits") == 1


def test_repeated_threshold_crossings_share_one_pointer_reference(edge):
    window, panel, handle = edge
    preferred = window.property("preferred")
    collapsed = handle.property("collapsedWidth")
    collapse = handle.property("effectiveCollapseThreshold")
    minimum = handle.property("minimumExpandedWidth")
    start = _start(window, handle)
    for _ in range(3):
        _move(window, handle, start, collapse - 20)
        expand = handle.property("effectiveExpansionThreshold")
        assert handle.property("previewCollapsed")
        assert not window.property("closed")
        assert window.property("preferred") == preferred
        assert window.property("commits") == 0
        QTest.qWait(350)
        assert panel.width() == collapsed
        _move(window, handle, start, expand - 10)
        assert handle.property("previewCollapsed")
        assert collapsed < panel.width() < collapsed + 24
        before = panel.width()
        _move(window, handle, start, expand + 10)
        assert not handle.property("previewCollapsed")
        assert panel.width() == before
        QTest.qWait(350)
        assert panel.width() == pytest.approx(max(minimum, expand + 10), abs=0.25)
        final_width = max(minimum, expand) + 30
        end = _move(window, handle, start, final_width)
        assert panel.width() == pytest.approx(final_width, abs=0.25)
    _release(window, end)
    assert not window.property("closed")
    assert window.property("preferred") == final_width
    assert window.property("commits") == 1


def test_hysteresis_and_release_commit_the_preview_state(edge):
    window, panel, handle = edge
    collapse = handle.property("effectiveCollapseThreshold")
    collapsed = handle.property("collapsedWidth")
    start = _start(window, handle)
    _move(window, handle, start, collapse - 1)
    expand = handle.property("effectiveExpansionThreshold")
    for raw in [collapse + 1, collapse - 1, expand - 1]:
        _move(window, handle, start, raw)
        assert handle.property("previewCollapsed")
    for raw in [expand + 1, expand - 1, expand + 1]:
        _move(window, handle, start, raw)
        assert not handle.property("previewCollapsed")
    collapse = handle.property("effectiveCollapseThreshold")
    _move(window, handle, start, collapse + 1)
    assert not handle.property("previewCollapsed")
    end = _move(window, handle, start, collapse - 1)
    _release(window, end)
    assert window.property("closed")
    assert panel.width() == collapsed
    assert window.property("commits") == 1


def test_closed_edge_resists_then_returns_home_before_threshold(edge):
    window, panel, handle = edge
    handle.setProperty("reducedMotion", True)
    window.setProperty("closed", True)
    handle.setProperty("reducedMotion", False)
    collapsed = handle.property("collapsedWidth")
    assert panel.width() == collapsed
    assert handle.isVisible()
    start = _start(window, handle)
    end = _move(window, handle, start, collapsed + 32)
    assert collapsed < panel.width() < collapsed + 24
    assert handle.property("previewCollapsed")
    assert window.property("closed")
    _release(window, end)
    assert panel.width() == collapsed
    assert window.property("closed")


def test_closed_edge_can_expand_collapse_and_expand_in_one_gesture(edge):
    window, panel, handle = edge
    handle.setProperty("reducedMotion", True)
    window.setProperty("closed", True)
    handle.setProperty("reducedMotion", False)
    start = _start(window, handle)
    for _ in range(2):
        expand = handle.property("effectiveExpansionThreshold")
        _move(window, handle, start, expand + 10)
        assert not handle.property("previewCollapsed")
        assert window.property("closed")
        assert window.property("commits") == 0
        QTest.qWait(350)
        collapse = handle.property("effectiveCollapseThreshold")
        _move(window, handle, start, collapse - 10)
        assert handle.property("previewCollapsed")
        QTest.qWait(350)
    final_width = handle.property("minimumExpandedWidth") + 40
    end = _move(window, handle, start, final_width)
    _release(window, end)
    assert not window.property("closed")
    assert window.property("preferred") == final_width
    assert panel.width() == final_width


def test_reversal_interrupts_threshold_animation_without_jumping(edge):
    window, panel, handle = edge
    start = _start(window, handle)
    _move(window, handle, start, 130)
    QTest.qWait(40)
    midway = panel.width()
    assert handle.property("collapsedWidth") < midway < start[1]
    end = _move(window, handle, start, 280)
    assert not handle.property("previewCollapsed")
    # Retargeting keeps the panel in flight instead of jumping to 280.
    assert handle.property("collapsedWidth") < panel.width() < 280
    assert window.property("commits") == 0
    _release(window, end)
    assert panel.width() == 280
    assert not window.property("closed")


@pytest.mark.parametrize("initially_closed", [False, True], ids=["collapse", "expand"])
def test_cancelling_a_preview_restores_owner_without_commit(edge, initially_closed):
    window, panel, handle = edge
    handle.setProperty("reducedMotion", True)
    window.setProperty("closed", initially_closed)
    handle.setProperty("reducedMotion", False)
    original = panel.width()
    preferred = window.property("preferred")
    start = _start(window, handle)
    raw = handle.property("effectiveExpansionThreshold") + 20 if initially_closed else 130
    _move(window, handle, start, raw)
    QTest.qWait(350)
    assert handle.property("previewCollapsed") != initially_closed
    handle.setEnabled(False)  # Qt cancels the mouse grab.
    QTest.qWait(650)
    assert not handle.property("dragging")
    assert window.property("commits") == 0
    assert panel.width() == original
    assert window.property("closed") == initially_closed
    assert window.property("preferred") == preferred


def test_fast_subthreshold_release_cannot_commit_a_flick_collapse(edge):
    window, panel, handle = edge
    start = _start(window, handle)
    end = _move(window, handle, start, handle.property("effectiveCollapseThreshold") + 5)
    _release(window, end)
    assert not window.property("closed")
    assert panel.width() == handle.property("minimumExpandedWidth")


def test_reduced_motion_keeps_reversibility_without_animations(edge):
    window, panel, handle = edge
    handle.setProperty("reducedMotion", True)
    start = _start(window, handle)
    _move(window, handle, start, 130)
    assert panel.width() == handle.property("collapsedWidth")
    assert handle.property("previewCollapsed")
    end = _move(window, handle, start, 280)
    assert panel.width() == 280
    assert not handle.property("previewCollapsed")
    assert not handle.property("animating")
    assert window.property("commits") == 0
    _release(window, end)
    assert panel.width() == 280


@pytest.mark.parametrize("reduced_motion", [False, True], ids=["animated", "reduced-motion"])
def test_short_pull_opens_to_minimum_ahead_of_pointer(edge, reduced_motion):
    window, panel, handle = edge
    handle.setProperty("reducedMotion", True)
    window.setProperty("closed", True)
    handle.setProperty("reducedMotion", reduced_motion)
    minimum = handle.property("minimumExpandedWidth")
    start = _start(window, handle)
    expand = handle.property("effectiveExpansionThreshold")
    assert expand - start[1] == 64
    assert expand < minimum - 40
    _move(window, handle, start, expand - 1)
    assert handle.property("previewCollapsed")
    assert panel.width() < start[1] + 24
    end = _move(window, handle, start, expand + 1)
    assert not handle.property("previewCollapsed")
    assert window.property("closed")
    assert window.property("commits") == 0
    QTest.qWait(350)
    assert panel.width() == pytest.approx(minimum, abs=0.25)
    assert panel.width() > handle.property("pointerWidth")
    assert handle.property("pressed")
    assert handle.property("collapseProgress") == 0
    _release(window, end)
    assert not window.property("closed")
    assert window.property("preferred") == minimum
    assert panel.width() == minimum


def test_pointer_can_catch_up_to_early_expansion_then_resize_and_collapse(edge):
    window, panel, handle = edge
    handle.setProperty("reducedMotion", True)
    window.setProperty("closed", True)
    handle.setProperty("reducedMotion", False)
    minimum = handle.property("minimumExpandedWidth")
    start = _start(window, handle)
    expand = handle.property("effectiveExpansionThreshold")
    _move(window, handle, start, expand + 1)
    QTest.qWait(350)
    _move(window, handle, start, expand + 20)
    assert panel.width() == pytest.approx(minimum, abs=0.25)
    _move(window, handle, start, expand + 8)
    assert minimum - 12 < panel.width() < minimum
    assert not handle.property("previewCollapsed")
    assert handle.property("collapseProgress") > 0
    _move(window, handle, start, expand + 28)
    assert panel.width() == pytest.approx(minimum, abs=0.25)
    assert handle.property("collapseProgress") == 0
    _move(window, handle, start, minimum + 24)
    assert panel.width() == pytest.approx(minimum + 24, abs=0.25)
    _move(window, handle, start, minimum - 12)
    assert minimum - 12 < panel.width() < minimum
    assert not handle.property("previewCollapsed")
    end = _move(window, handle, start, handle.property("effectiveCollapseThreshold") - 1)
    assert handle.property("previewCollapsed")
    assert window.property("closed")
    assert window.property("commits") == 0
    _release(window, end)
    assert window.property("closed")
    assert panel.width() == handle.property("collapsedWidth")


@pytest.mark.parametrize("reduced_motion", [False, True], ids=["animated", "reduced-motion"])
@pytest.mark.parametrize("depth", ["threshold", "edge"])
def test_held_collapse_reopens_before_pointer_reaches_minimum(edge, reduced_motion, depth):
    window, panel, handle = edge
    handle.setProperty("reducedMotion", reduced_motion)
    minimum = handle.property("minimumExpandedWidth")
    collapsed = handle.property("collapsedWidth")
    original_preferred = window.property("preferred")
    start = _start(window, handle)
    collapse = handle.property("effectiveCollapseThreshold")
    raw = collapse - 1 if depth == "threshold" else collapsed
    _move(window, handle, start, raw)
    assert handle.property("previewCollapsed")
    QTest.qWait(350)
    assert panel.width() == collapsed
    expand = handle.property("effectiveExpansionThreshold")
    assert expand <= minimum - 40
    assert expand > raw
    if depth == "threshold":
        assert expand - collapse >= 20
    _move(window, handle, start, expand - 1)
    assert handle.property("previewCollapsed")
    assert handle.property("pressed")
    _move(window, handle, start, expand)
    assert not handle.property("previewCollapsed")
    assert not window.property("closed")
    assert window.property("preferred") == original_preferred
    assert window.property("commits") == 0
    QTest.qWait(350)
    assert panel.width() == pytest.approx(minimum, abs=0.25)
    assert panel.width() - handle.property("pointerWidth") >= 40 - 0.25
    assert handle.property("pressed")
    # Reverse once more without reaching the minimum or releasing the mouse.
    collapse = handle.property("effectiveCollapseThreshold")
    _move(window, handle, start, collapse - 1)
    assert handle.property("previewCollapsed")
    expand = handle.property("effectiveExpansionThreshold")
    assert expand <= minimum - 40
    end = _move(window, handle, start, expand)
    assert not handle.property("previewCollapsed")
    assert window.property("commits") == 0
    _release(window, end)
    assert not window.property("closed")
    assert panel.width() == minimum
    assert window.property("preferred") == minimum
    assert window.property("commits") == 1
