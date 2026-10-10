pragma ComponentBehavior: Bound
import QtQuick

/*
 * Wheel and trackpad scrolling for a Flickable, in place of Qt's own — which
 * is either an instant step (mouse notch) or a somewhat muted one-to-one
 * trace with no momentum (trackpad), neither of which reads as the smooth,
 * continuous motion of a browser or Google Docs.
 *
 * A mouse notch does not jump `target.contentY` — it extends a destination
 * (`_wheelTarget`) that every frame eases a fraction of the remaining
 * distance closer to, exactly like easing towards a moving point. Spin the
 * wheel again before that catches up and the destination just moves further
 * off; the same easing keeps running rather than restarting, so a burst of
 * notches reads as one continuous, accelerating-then-settling scroll and not
 * a staircase of teleports. The very last notch's approach is what a browser
 * would call its "deceleration" — there is no separate momentum phase to
 * bolt on afterwards, which is what made the previous version look like it
 * stopped and then started coasting a beat later.
 *
 * A trackpad swipe is different in kind, not degree: the deltas already carry
 * the finger's real speed, so tracking them one-to-one during the gesture is
 * correct and easing them would only add lag. What Qt's own handling is
 * missing is what happens after — this keeps a blended velocity estimate
 * live while events are arriving and, once they stop (idle, or an explicit
 * `Qt.ScrollEnd`/`ScrollMomentum` phase), launches an exponentially decaying
 * coast from it: velocity shrinks by a fixed fraction every second of real
 * time, so a hard flick travels further and takes longer to settle than a
 * gentle one without any special-casing for "hard" versus "gentle".
 *
 * Wired up as a sibling placed in front of `target`, not a child of it: a
 * Flickable claims every pointer event for its own interactive tracking
 * before a handler declared inside it is even asked, so nothing declared
 * inside `target` would ever see the wheel. A MouseArea rather than a
 * WheelHandler for the same reason PointerHandlers get skipped further up
 * this file's history — `acceptedButtons: Qt.NoButton` claims wheel input
 * only, so press, hover and drag on whatever `target` contains pass straight
 * through untouched.
 *
 *     SmoothWheel { target: myFlickable }
 *
 * placed anywhere in the same file as `myFlickable` is the whole of the API.
 */
Item {
    id: root

    required property Flickable target

    // Pixels a single mouse-wheel notch (angleDelta of 120) moves the view.
    // Chrome and Firefox both land around three lines, which on most desktop
    // font sizes comes out in this neighbourhood.
    property real wheelStep: 110

    // How long a single notch's eased approach takes to mostly finish. This
    // is what a browser's wheel scroll actually looks like — not a teleport,
    // not a slow drift, a couple of hundred milliseconds of ease-out.
    property real wheelChaseTime: 0.22

    // Trackpads report real screen pixels, but tracking them at a strict 1:1
    // reads as sluggish next to a browser — both the live trace and the
    // momentum it hands off from are scaled up by this.
    property real trackpadGain: 2.0

    // Ctrl+wheel, handed back to whoever asked for it instead of being
    // scrolled. Everything below accepts every wheel event unconditionally,
    // which is the only way to be sure Qt's own handling never also runs — so
    // a view that wants Ctrl+wheel to mean something else has no way to see
    // one otherwise. Opt-in rather than on for everybody: Ctrl+wheel still
    // scrolls the track table and every other list in the app.
    property bool forwardZoomWheel: false
    signal zoomWheel(var event)

    parent: target.parent
    anchors.fill: target
    z: target.z + 1

    property real _wheelTarget: 0
    property bool _wheelChasing: false
    property real _scrollVelocity: 0
    property bool _trackpadCoasting: false
    property real _lastWheelTs: 0

    // No wheel event in this long ⇒ the gesture is over — the only reliable
    // signal on backends that don't report WheelEvent.phase. Short enough
    // that it reads as instantaneous once the fingers actually lift.
    Timer {
        id: _wheelIdle
        interval: 60
        onTriggered: {
            if (Math.abs(root._scrollVelocity) > 15) {
                root._trackpadCoasting = true
                _driver.running = true
            }
        }
    }

    FrameAnimation {
        id: _driver
        running: false
        onTriggered: {
            var f = root.target
            var dt = Math.min(frameTime, 0.05)   // guard a stalled frame from jumping the view
            var max = Math.max(0, f.contentHeight - f.height)
            var lo = f.originY
            var hi = lo + max

            if (root._wheelChasing) {
                // 98% of the way to the target by `wheelChaseTime` seconds in
                // — fast while far from it, gentler as it nears, and never
                // overshoots. A plain `Math.pow(k, dt)` decay is parameterised
                // by a per-second rate, which is awkward to reason about
                // directly; rewriting it as "done by roughly this long"
                // is the one that actually matches how a browser's per-notch
                // scroll animation is usually described.
                var factor = 1 - Math.pow(0.02, dt / root.wheelChaseTime)
                var y = f.contentY + (root._wheelTarget - f.contentY) * factor
                y = Math.max(lo, Math.min(hi, y))
                f.contentY = y
                if (Math.abs(root._wheelTarget - y) < 0.5) {
                    f.contentY = root._wheelTarget
                    root._wheelChasing = false
                }
            } else if (root._trackpadCoasting) {
                var y2 = f.contentY + root._scrollVelocity * dt
                if (y2 <= lo || y2 >= hi) {
                    f.contentY = Math.max(lo, Math.min(hi, y2))
                    root._scrollVelocity = 0
                    root._trackpadCoasting = false
                } else {
                    f.contentY = y2
                    // Keeps ~22% of itself every second of real time — a hard
                    // flick (large v0) travels further and takes longer to
                    // decay under the stop threshold below than a gentle one,
                    // because time-to-stop scales with log(v0), not v0 itself.
                    root._scrollVelocity *= Math.pow(0.22, dt)
                    if (Math.abs(root._scrollVelocity) < 15)
                        root._trackpadCoasting = false
                }
            }

            if (!root._wheelChasing && !root._trackpadCoasting)
                running = false
        }
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.NoButton
        onWheel: (event) => root.handleWheel(event)
    }

    // Exposed (not `_`-prefixed) so another item covering part of `target` —
    // a sticky header strip, say — can forward a wheel event here itself
    // rather than needing its own copy of this logic.
    function handleWheel(event) {
        if (root.forwardZoomWheel && (event.modifiers & Qt.ControlModifier)) {
            root.zoomWheel(event)
            event.accepted = true
            return
        }

        var f = root.target
        var max = Math.max(0, f.contentHeight - f.height)
        var lo = f.originY
        var hi = lo + max

        if (event.pixelDelta.y !== 0) {
            // Trackpad: a live gesture always wins over whatever the view was
            // already doing.
            root._trackpadCoasting = false
            root._wheelChasing = false

            var delta = event.pixelDelta.y * root.trackpadGain
            f.contentY = Math.max(lo, Math.min(hi, f.contentY - delta))

            var now = Date.now()
            var dt = root._lastWheelTs > 0
                   ? Math.min(0.1, Math.max(0.001, (now - root._lastWheelTs) / 1000))
                   : 0.016
            root._lastWheelTs = now
            var instVel = -delta / dt   // px/sec, this event alone
            // Blended rather than trusting the latest sample outright: the
            // last tick before the fingers lift is often smaller than the
            // swipe's real speed, and a single small sample would otherwise
            // gut the coast.
            root._scrollVelocity = root._scrollVelocity * 0.4 + instVel * 0.6

            if (event.phase === Qt.ScrollEnd || event.phase === Qt.ScrollMomentum) {
                if (Math.abs(root._scrollVelocity) > 15) {
                    root._trackpadCoasting = true
                    _driver.running = true
                }
            } else {
                _wheelIdle.restart()
            }
        } else {
            // Mouse notch: extend the chase target rather than jump to it.
            var step = (event.angleDelta.y / 120) * root.wheelStep
            var base = root._wheelChasing ? root._wheelTarget : f.contentY
            var rawTarget = base - step
            var clampedTarget = Math.max(lo, Math.min(hi, rawTarget))
            if (clampedTarget !== rawTarget) {
                // This notch runs past the end of the content — there is
                // nothing left to ease towards, so the end of the list is
                // where it stops, right now, rather than gliding the
                // remaining distance at the usual per-notch pace. A fast
                // scroll hitting the top or bottom is meant to hit it, not
                // visibly slow down first.
                f.contentY = clampedTarget
                root._wheelChasing = false
            } else {
                root._wheelTarget = clampedTarget
                root._wheelChasing = true
                _driver.running = true
            }
        }
        event.accepted = true
    }
}
