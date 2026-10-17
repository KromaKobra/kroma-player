import QtQuick
import Nocturne

// The owner stores only released gestures. Preview state and raw pointer width
// live here, in one fixed coordinate system for both mirrored edges.
MouseArea {
    id: root

    property real expandedWidth: 240
    property bool collapsed: false
    property real collapsedWidth: 64
    property real minimumExpandedWidth: 200
    property real maximumWidth: 360
    property real collapseThreshold: 156
    // Pointer travel from the closed preview, independent of panel size.
    property real expansionThreshold: 64
    property bool rightSidebar: false
    property bool reducedMotion: false

    readonly property real effectiveMaximum: Math.max(minimumExpandedWidth, maximumWidth)
    readonly property real targetWidth: collapsed ? collapsedWidth
        : Math.max(minimumExpandedWidth, Math.min(expandedWidth, effectiveMaximum))
    // Reopening always happens ahead of the minimum, even if the pointer
    // stopped immediately after collapse rather than reaching the closed edge.
    // Reserve 20px between that opening limit and the normal collapse boundary.
    readonly property real expansionLimit: minimumExpandedWidth - 40
    readonly property real collapseBoundary: Math.min(collapseThreshold, expansionLimit - 20)
    readonly property real effectiveExpansionThreshold: Math.min(expansionLimit,
        _collapsedOrigin + Math.max(1, Math.min(expansionThreshold, effectiveMaximum - collapsedWidth)))
    readonly property real effectiveCollapseThreshold: _anticipatingExpansion
        ? Math.min(collapseBoundary, _minimumPointerWidth - 40) : collapseBoundary
    readonly property bool animating: spring.running
    readonly property bool dragging: _dragging
    readonly property bool previewCollapsed: _dragging ? _previewCollapsed : collapsed
    readonly property real pointerWidth: _pointerWidth
    readonly property real collapseProgress: _dragging && !_previewCollapsed
        ? Math.max(0, Math.min(1, ((_anticipatingExpansion ? _minimumPointerWidth
            : minimumExpandedWidth) - _pointerWidth) / Math.max(1,
            (_anticipatingExpansion ? _minimumPointerWidth : minimumExpandedWidth)
                - effectiveCollapseThreshold))) : 0
    property real displayedWidth: 0
    property real velocity: 0

    signal resizeCommitted(real preferredWidth, bool collapsed)

    width: 12
    hoverEnabled: true
    preventStealing: true
    acceptedButtons: Qt.LeftButton
    cursorShape: Qt.SizeHorCursor
    containmentMask: QtObject {
        function contains(point: point): bool { return ScrollBarPriority.resizeContains(root, point) }
    }
    Accessible.role: Accessible.Grip
    Accessible.name: rightSidebar ? "Resize details sidebar" : "Resize navigation sidebar"
    Accessible.description: "Drag to resize, collapse, or expand the sidebar"

    property bool _ready: false
    property bool _finishing: false
    property bool _dragging: false
    property bool _previewCollapsed: false
    property bool _anticipatingExpansion: false
    property real _minimumPointerWidth: 0
    property real _pressX: 0
    property real _startWidth: 0
    property real _pointerWidth: 0
    property real _collapsedOrigin: 0
    property real _dragTarget: 0
    property real _springOffset: 0
    property double _lastTime: 0
    property real _releaseVelocity: 0
    function motionTarget() { return _dragging ? _dragTarget : targetWidth }

    Component.onCompleted: { displayedWidth = targetWidth; _ready = true }
    onTargetWidthChanged: if (_ready && !_dragging && !_finishing) settle()
    onReducedMotionChanged: if (_ready) settle()

    // Saturating travel: the pointer can keep going while the edge moves less
    // and less. Thresholds always read the original, unresisted pointer width.
    function resistedTravel(distance) {
        return 24 * (1 - Math.exp(-0.65 * Math.max(0, distance) / 24))
    }

    function bounded(width) {
        return Math.max(collapsedWidth, Math.min(effectiveMaximum, width))
    }

    function settle() {
        _springOffset = displayedWidth - motionTarget()
        if (reducedMotion || Math.abs(_springOffset) < 0.25) {
            spring.running = false
            displayedWidth = motionTarget()
            _springOffset = 0
            velocity = 0
        } else {
            spring.running = true
        }
    }

    // Animate only the difference from the pointer-driven target. The target
    // still tracks every move directly, so a threshold snap never turns normal
    // resizing into a lagging animation. Retarget from the presentation width
    // and retain spring velocity, including when reversing mid-animation.
    FrameAnimation {
        id: spring
        running: false
        onTriggered: {
            var dt = Math.min(frameTime, 0.05)
            var response = root._dragging ? 40 : 24
            var c = root.velocity + response * root._springOffset
            var decay = Math.exp(-response * dt)
            root._springOffset = (root._springOffset + c * dt) * decay
            root.velocity = (root.velocity - response * c * dt) * decay
            root.displayedWidth = root.bounded(root.motionTarget() + root._springOffset)
            if (Math.abs(root._springOffset) < 0.25 && Math.abs(root.velocity) < 2) {
                running = false
                root._springOffset = 0
                root.displayedWidth = root.motionTarget()
                root.velocity = 0
            }
        }
    }

    onPressed: function (event) {
        spring.running = false
        velocity = 0
        _springOffset = 0
        // Global coordinates remain stable even when an ancestor moves or the
        // handle disappears into a collapsed edge. Never rebase during a drag.
        _pressX = mapToGlobal(event.x, event.y).x
        _startWidth = displayedWidth
        _pointerWidth = _startWidth
        // Grabbing an in-flight close takes hold of the visible panel.
        _previewCollapsed = displayedWidth <= collapsedWidth + 0.25
        _collapsedOrigin = _previewCollapsed ? _pointerWidth : collapsedWidth
        _anticipatingExpansion = false
        _minimumPointerWidth = minimumExpandedWidth
        _dragTarget = displayedWidth
        _lastTime = Date.now()
        _releaseVelocity = 0
        _dragging = true
    }

    function updateDrag(globalX) {
        var oldTarget = _dragTarget
        var now = Date.now()
        var direction = rightSidebar ? -1 : 1
        _pointerWidth = _startWidth + direction * (globalX - _pressX)
        var crossed = false
        if (_anticipatingExpansion && _pointerWidth >= minimumExpandedWidth)
            _anticipatingExpansion = false
        if (!_previewCollapsed && _pointerWidth <= effectiveCollapseThreshold) {
            _previewCollapsed = true
            _collapsedOrigin = _pointerWidth
            crossed = true
        } else if (_previewCollapsed && _pointerWidth >= effectiveExpansionThreshold) {
            _previewCollapsed = false
            _anticipatingExpansion = _pointerWidth < minimumExpandedWidth
            _minimumPointerWidth = Math.min(minimumExpandedWidth, _pointerWidth)
            crossed = true
        }
        if (_previewCollapsed) {
            // A deeper pull stays closed; reversing reveals a small resisted
            // preview. Reopening travel is measured from this closed pull;
            // the original press coordinates stay fixed throughout.
            _collapsedOrigin = Math.min(_collapsedOrigin, _pointerWidth)
            _dragTarget = collapsedWidth + resistedTravel(_pointerWidth - _collapsedOrigin)
        } else if (_pointerWidth < minimumExpandedWidth) {
            // Open ahead of the pointer and hold the minimum until it catches
            // up. A reversal meets resistance from the furthest pointer reach;
            // this changes neither the press reference nor the raw pointer.
            if (_anticipatingExpansion)
                _minimumPointerWidth = Math.max(_minimumPointerWidth, _pointerWidth)
            var minimumPointer = _anticipatingExpansion ? _minimumPointerWidth : minimumExpandedWidth
            _dragTarget = minimumExpandedWidth - resistedTravel(minimumPointer - _pointerWidth)
        } else {
            _dragTarget = Math.min(effectiveMaximum, _pointerWidth)
        }
        _dragTarget = bounded(_dragTarget)
        _releaseVelocity = !crossed && now > _lastTime
            ? (_dragTarget - oldTarget) * 1000 / (now - _lastTime) : 0
        _lastTime = now
        if (crossed) {
            settle()
        } else {
            displayedWidth = bounded(_dragTarget + _springOffset)
        }
    }

    onPositionChanged: function (event) {
        if (_dragging) updateDrag(mapToGlobal(event.x, event.y).x)
    }
    function finish(cancelled) {
        if (!_dragging) return
        _finishing = true
        if (!cancelled) {
            var v = Date.now() - _lastTime < 100 ? _releaseVelocity : 0
            velocity += v
            var preferred = _previewCollapsed ? expandedWidth
                : Math.max(minimumExpandedWidth, Math.min(_pointerWidth, effectiveMaximum))
            resizeCommitted(preferred, _previewCollapsed)
        } else {
            velocity = 0
        }
        _dragging = false
        _finishing = false
        settle()
    }
    onReleased: function (event) {
        // Releases can carry the last position without an intervening move.
        var globalX = mapToGlobal(event.x, event.y).x
        if (_startWidth + (rightSidebar ? -1 : 1) * (globalX - _pressX) !== _pointerWidth)
            updateDrag(globalX)
        finish(false)
    }
    onCanceled: finish(true)

    // Like the track-list removal bed, reveal a tint as resistance builds.
    // Collapse uses the accent since it preserves content.
    Rectangle {
        objectName: "sidebarCollapseFeedback"
        x: root.rightSidebar ? root.width / 2 : root.width / 2 - width
        width: 16 * root.collapseProgress
        height: parent.height
        color: Theme.accentSoft
        opacity: root.collapseProgress
        visible: opacity > 0
    }
    Rectangle {
        // One divider column, mirrored into each panel.
        x: root.width / 2 - (root.rightSidebar ? 0 : width)
        width: 1
        height: parent.height
        color: Theme.accent
        opacity: root.pressed ? 0.65 : root.containsMouse ? 0.3 : 0
    }
}
