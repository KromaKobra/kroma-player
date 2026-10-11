import QtQuick
import Nocturne

// Shared sizing and motion for both sidebar edges. The owner keeps preferred
// width and open state; this surface reports a completed gesture without
// assigning to the owner's bindings. Pointer coordinates use a stable parent.
MouseArea {
    id: root

    property real expandedWidth: 240
    property bool collapsed: false
    property real collapsedWidth: 64
    property real minimumExpandedWidth: 200
    property real maximumWidth: 360
    property real collapseThreshold: 156
    property bool rightSidebar: false
    property bool reducedMotion: false

    readonly property real effectiveMaximum: Math.max(minimumExpandedWidth, maximumWidth)
    readonly property real targetWidth: collapsed ? collapsedWidth
        : Math.max(minimumExpandedWidth, Math.min(expandedWidth, effectiveMaximum))
    readonly property bool animating: spring.running
    property real displayedWidth: 0
    property real velocity: 0

    signal resizeCommitted(real preferredWidth, bool collapsed)

    width: 8
    hoverEnabled: true
    preventStealing: true
    acceptedButtons: Qt.LeftButton
    cursorShape: Qt.SizeHorCursor

    property bool _ready: false
    property bool _finishing: false
    property real _pressX: 0
    property real _startWidth: 0
    property real _lastX: 0
    property double _lastTime: 0
    property real _releaseVelocity: 0

    Component.onCompleted: { displayedWidth = targetWidth; _ready = true }
    onTargetWidthChanged: if (_ready && !pressed && !_finishing) settle()
    onReducedMotionChanged: if (reducedMotion && !pressed) settle()

    function settle() {
        if (reducedMotion) {
            spring.running = false
            displayedWidth = targetWidth
            velocity = 0
        } else {
            spring.running = true
        }
    }

    // Retargeting preserves velocity and starts at the presentation width.
    // Analytic integration is stable across variable refresh rates, and the
    // single frame callback stops when settled rather than doing idle work.
    FrameAnimation {
        id: spring
        running: false
        onTriggered: {
            var dt = Math.min(frameTime, 0.05)
            var offset = root.displayedWidth - root.targetWidth
            var c = root.velocity + 24 * offset
            var decay = Math.exp(-24 * dt)
            var nextWidth = root.targetWidth + (offset + c * dt) * decay
            var boundedWidth = Math.max(root.collapsedWidth,
                Math.min(Math.max(root.displayedWidth, root.effectiveMaximum), nextWidth))
            root.velocity = boundedWidth === nextWidth
                ? (root.velocity - 24 * c * dt) * decay : 0
            root.displayedWidth = boundedWidth
            if (Math.abs(root.displayedWidth - root.targetWidth) < 0.25
                    && Math.abs(root.velocity) < 2) {
                running = false
                root.displayedWidth = root.targetWidth
                root.velocity = 0
            }
        }
    }

    onPressed: function (event) {
        spring.running = false
        _pressX = mapToItem(parent, event.x, 0).x
        _lastX = _pressX
        _lastTime = Date.now()
        _startWidth = displayedWidth
        _releaseVelocity = 0
    }
    onPositionChanged: function (event) {
        if (!pressed) return
        var nowX = mapToItem(parent, event.x, 0).x
        var now = Date.now()
        var direction = rightSidebar ? -1 : 1
        if (now > _lastTime)
            _releaseVelocity = direction * (nowX - _lastX) * 1000 / (now - _lastTime)
        _lastX = nowX
        _lastTime = now
        displayedWidth = Math.max(collapsedWidth,
            Math.min(effectiveMaximum, _startWidth + direction * (nowX - _pressX)))
    }
    function finish(cancelled) {
        _finishing = true
        if (!cancelled) {
            var v = Date.now() - _lastTime < 100 ? _releaseVelocity : 0
            var shouldCollapse = displayedWidth < collapseThreshold
                || displayedWidth + Math.min(0, v) * 0.06 < collapseThreshold
            var preferred = shouldCollapse ? expandedWidth
                : Math.max(minimumExpandedWidth, Math.min(displayedWidth, effectiveMaximum))
            velocity = v
            resizeCommitted(preferred, shouldCollapse)
        } else {
            velocity = 0
        }
        _finishing = false
        settle()
    }
    onReleased: finish(false)
    onCanceled: finish(true)

    Rectangle {
        // Paint over the divider inside either panel. Keeping the highlight
        // to one column avoids the wider double edge on the right.
        x: root.width / 2 - (root.rightSidebar ? 0 : width)
        width: 1
        height: parent.height
        color: Theme.accent
        opacity: root.pressed ? 0.65 : root.containsMouse ? 0.3 : 0
    }
}
