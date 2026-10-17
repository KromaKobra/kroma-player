import QtQuick

// FLIP-style presentation: the view owns final geometry and virtualization;
// only translation and scale move between layouts. No per-frame relayout,
// artwork decode, or mask texture allocation is needed to animate a tile.
Item {
    id: root
    required property Item target
    required property var view
    property bool active: false

    readonly property Translate translation: Translate {
        x: root._offset.x
        y: root._offset.y
    }
    readonly property Scale resizing: Scale {
        xScale: root.target.width > 0 ? 1 + root._offset.z / root.target.width : 1
        yScale: root.target.height > 0 ? 1 + root._offset.w / root.target.height : 1
    }

    property bool _ready: false
    // x, y, width, height, all in pixels. Independent axes retain velocity
    // when another layout arrives, including a reversal mid-flight.
    property vector4d _geometry: Qt.vector4d(0, 0, 0, 0)
    property vector4d _offset: Qt.vector4d(0, 0, 0, 0)
    property vector4d _velocity: Qt.vector4d(0, 0, 0, 0)

    function geometry() {
        return Qt.vector4d(target.x, target.y, target.width, target.height)
    }
    function reset() {
        spring.running = false
        _geometry = geometry()
        _offset = Qt.vector4d(0, 0, 0, 0)
        _velocity = Qt.vector4d(0, 0, 0, 0)
    }
    function initialize() {
        reset()
        _ready = true
    }
    function prepare() {
        _ready = false
        reset()
        // GridView finishes positioning a reused delegate after onReused.
        // An owned timer also cancels initialization if the view is destroyed
        // in this turn (for example, switching between grid and list).
        readiness.restart()
    }
    function retarget() {
        var next = geometry()
        // Animate only entries visible before or after the layout change.
        // Buffered/pooled delegates must never fly in from a previous identity.
        var top = view ? view.contentY : 0
        var bottom = top + (view ? view.height : 0)
        var oldY = _geometry.y + _offset.y
        var oldHeight = _geometry.w + _offset.w
        var inViewport = (next.y < bottom && next.y + next.w > top)
                      || (oldY < bottom && oldY + oldHeight > top)
        if (!_ready || !active || !inViewport) {
            reset()
            return
        }
        _offset = Qt.vector4d(_offset.x + _geometry.x - next.x,
                             _offset.y + _geometry.y - next.y,
                             _offset.z + _geometry.z - next.z,
                             _offset.w + _geometry.w - next.w)
        _geometry = next
        spring.running = true
    }

    Component.onCompleted: prepare()
    onActiveChanged: reset()
    Timer {
        id: readiness
        interval: 0
        onTriggered: root.initialize()
    }
    Connections {
        target: root.target
        function onXChanged() { root.retarget() }
        function onYChanged() { root.retarget() }
        function onWidthChanged() { root.retarget() }
        function onHeightChanged() { root.retarget() }
    }

    FrameAnimation {
        id: spring
        running: false
        onTriggered: {
            // Exact critically damped solution, rather than an Euler step:
            // stable at any refresh rate and after a delayed frame. At 24/s
            // routine reflows settle in roughly 0.3–0.4s, without added bounce.
            var dt = frameTime
            var response = 24
            var decay = Math.exp(-response * dt)
            var p = root._offset
            var v = root._velocity
            var c = Qt.vector4d(v.x + response * p.x, v.y + response * p.y,
                                v.z + response * p.z, v.w + response * p.w)
            root._offset = Qt.vector4d((p.x + c.x * dt) * decay, (p.y + c.y * dt) * decay,
                                      (p.z + c.z * dt) * decay, (p.w + c.w * dt) * decay)
            root._velocity = Qt.vector4d((v.x - response * c.x * dt) * decay,
                                        (v.y - response * c.y * dt) * decay,
                                        (v.z - response * c.z * dt) * decay,
                                        (v.w - response * c.w * dt) * decay)
            p = root._offset
            v = root._velocity
            if (Math.max(Math.abs(p.x), Math.abs(p.y), Math.abs(p.z), Math.abs(p.w)) < 0.25
                    && Math.max(Math.abs(v.x), Math.abs(v.y), Math.abs(v.z), Math.abs(v.w)) < 2)
                root.reset()
        }
    }
}
