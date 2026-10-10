pragma ComponentBehavior: Bound
import QtQuick

/*
 * Wheel and trackpad scrolling for a control that moves in whole steps — a
 * navigation rail, a tab bar — rather than by the pixel.
 *
 * A wheel event is continuous and a tab change is not, so the delta cannot
 * drive the control directly. One two-finger flick arrives as dozens of
 * events carrying hundreds of pixels between them; handed straight to a rail
 * of six destinations that is not a scroll, it is a strobe. What is wanted is
 * the opposite reading of the same gesture: how much has been scrolled since
 * the last step, and has it yet added up to another one.
 *
 * So deltas accumulate and the accumulator is what gets tested. A mouse notch
 * (angleDelta of 120) crosses the threshold on its own, which is what makes
 * one notch mean exactly one step; a hi-res wheel reporting 40 at a time
 * needs three, and gets there by the same arithmetic without knowing it is a
 * different kind of mouse. Trackpad pixels have a threshold of their own,
 * plus a floor on how often a step may fire at all — a hard flick should
 * advance a few slots at a pace you can follow, not empty the rail. Between
 * gestures the accumulator is dropped rather than carried: a reversal, an
 * explicit `Qt.ScrollEnd`, or simply nothing arriving for a moment, so half a
 * swipe never leaks into the next one and turns its first notch into two.
 *
 * Reports and does nothing else. `stepped(-1)` means the previous entry and
 * `stepped(+1)` the next; what those mean, and whether either exists, is for
 * the control to decide — the same division NavRail keeps with its clicks.
 *
 *     WheelSteps {
 *         anchors.fill: parent
 *         onStepped: function (delta) { ... }
 *     }
 *
 * A MouseArea rather than a WheelHandler, for the reason given at length in
 * SmoothWheel: `acceptedButtons: Qt.NoButton` claims wheel input only, so
 * press and hover on whatever this covers pass straight through. Wheel is
 * ignored by a MouseArea with no `onWheel` of its own, so the per-slot mouse
 * areas layered above this one do not swallow it on the way down.
 */
Item {
    id: root

    // Wheel units per step. A notch is 120, so a notch is a step.
    property real notchThreshold: 120

    // Trackpad pixels per step. Roughly a third of what a notch scrolls a
    // list by, because a discrete step is heavier than a discrete scroll and
    // wants a shorter run-up before it will admit it was asked for.
    property real pixelThreshold: 55

    // Milliseconds a step waits before another may follow it. A flick delivers
    // its pixels far faster than a view change can be read, and without this
    // the whole rail goes past in the time it takes to lift your fingers.
    property int minInterval: 130

    // -1 for the previous entry, +1 for the next. Never 0, never further.
    signal stepped(int delta)

    property real _accum: 0
    property real _lastStepTs: 0

    // Nothing for this long and the gesture is over — the same reasoning, and
    // roughly the same interval, as SmoothWheel's idle timer.
    Timer {
        id: _idle
        interval: 120
        onTriggered: root._accum = 0
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.NoButton
        onWheel: (event) => root.handleWheel(event)
    }

    // Exposed the way SmoothWheel's is, so something covering part of this
    // area can forward a wheel event here rather than repeat the accumulator.
    function handleWheel(event) {
        var pixels = event.pixelDelta.y !== 0 || event.pixelDelta.x !== 0
        var dy = pixels ? event.pixelDelta.y : event.angleDelta.y
        var dx = pixels ? event.pixelDelta.x : event.angleDelta.x

        // Vertical is the gesture either bar is really asking for, including
        // the horizontal one: the settings tabs run left to right but the
        // wheel over them is still a wheel. `x` is the fallback, for a
        // sideways trackpad swipe or a shift+wheel, and points the other way —
        // a positive x is a scroll to the left, which is the previous tab.
        var d = dy !== 0 ? dy : -dx
        if (d === 0) {
            event.accepted = true
            return
        }

        // The coast after the fingers lift belongs to scrolling, not to
        // stepping: a list is meant to keep travelling and a rail of six
        // destinations is not. On the backends that report it, this is what
        // keeps a hard flick to the distance you actually pushed it.
        if (event.phase === Qt.ScrollMomentum) {
            root._accum = 0
            event.accepted = true
            return
        }

        // A reversal is a new gesture, not a continuation of the old one: what
        // had accumulated towards a step in the other direction would
        // otherwise have to be spent back before this one counted at all.
        if (root._accum !== 0 && (d > 0) !== (root._accum > 0))
            root._accum = 0

        root._accum += d

        var threshold = pixels ? root.pixelThreshold : root.notchThreshold
        if (Math.abs(root._accum) >= threshold) {
            var now = Date.now()
            if (now - root._lastStepTs >= root.minInterval) {
                root._lastStepTs = now
                root.stepped(root._accum > 0 ? -1 : 1)
            }
            // Dropped whether or not it fired. Keeping the remainder would
            // bank a throttled step and spend it the instant the interval was
            // up, which is the flick tearing through the rail again, only
            // late.
            root._accum = 0
        }

        if (event.phase === Qt.ScrollEnd)
            root._accum = 0
        else
            _idle.restart()

        event.accepted = true
    }
}
