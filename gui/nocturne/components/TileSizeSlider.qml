import QtQuick
import Nocturne

// How many artists or albums fit across the wall. A slider flanked by the two
// walls it chooses between — a dense 3x3 on the left, a roomy 2x2 on the right
// — so the control says what it does without a label beside the sort button.
//
// Hand-rolled from a Rectangle groove and a MouseArea rather than a
// QtQuick.Controls Slider, which this package uses nowhere: the visual grammar
// here is VolumeControl's, down to the 4px groove that grows to 5 on hover and
// the handle that only fades in once the pointer is over it.
//
// The one thing it does not share with a volume slider is its range. A grid
// fills its row — `cellWidth = floor(width / columns)` — so the size a user
// actually sees is quantised by the column count, and a slider walking a target
// width in even pixel steps would sit dead for several steps in a row at the
// large end (at 1200px, every target from 250 to 300 lays out four columns).
// **So the stops are column counts and the value written back is a width.**
// Every notch visibly changes the wall; the number that reaches settings.json
// is `width / columns`, which still means the same thing after a resize.
Row {
    id: root

    // The current target width, pushed in from the preference. Not written to
    // directly — the owner decides what a request does with it.
    property real tileSize: 170
    property real minimum: 120
    property real maximum: 300

    // The grid's width, which is what turns a width into a column count. Zero
    // until the wall has been laid out, and `available` is false until then.
    property real viewportWidth: 0

    // Matches the grids' own `Math.max(2, ...)` floor: a wall one tile wide is
    // a list, and a worse one than the track table.
    property int minColumns: 2
    property int sliderWidth: 76

    signal sizeRequested(int tileSize)

    spacing: 3
    height: 30

    // ------------------------------------------------------------- the stops

    function _colsFor(t) {
        return Math.max(minColumns, Math.floor(viewportWidth / Math.max(1, t)))
    }

    // Smaller tiles mean more columns, so the minimum tile size is the maximum
    // column count. Inverted names would read as a typo everywhere below.
    readonly property int _maxCols: _colsFor(minimum)
    readonly property int _minCols: _colsFor(maximum)
    readonly property int _span: Math.max(1, _maxCols - _minCols)

    // Which stop the current size sits on, clamped in case the size pushed in
    // came from outside the range this viewport can reach.
    readonly property int columns:
        Math.max(_minCols, Math.min(_maxCols, _colsFor(tileSize)))

    // Inverted against the column count: dragging right means fewer columns
    // and bigger tiles, which is the direction the icons promise.
    readonly property real _fraction: (_maxCols - columns) / _span

    // On a viewport too narrow to offer more than one layout there is nothing
    // to drag, so the control leaves the heading rather than sitting inert.
    readonly property bool available: _maxCols > _minCols
    visible: available

    // Ask for a column count. Public so a test can drive the control without
    // synthesising mouse events — and so the arithmetic that matters is
    // reachable in one call rather than only through a drag.
    function applyColumns(cols) {
        var c = Math.max(_minCols, Math.min(_maxCols, Math.round(cols)))
        var size = Math.round(viewportWidth / Math.max(1, c))
        root.sizeRequested(Math.round(Math.max(minimum, Math.min(maximum, size))))
    }

    function _applyFraction(f) {
        var clamped = Math.max(0, Math.min(1, f))
        applyColumns(_maxCols - clamped * _span)
    }

    // ---------------------------------------------------------------- shape

    Icon {
        anchors.verticalCenter: parent.verticalCenter
        name: "tilesSmall"
        size: 14
        color: Theme.faint
    }

    Item {
        width: root.sliderWidth
        height: root.height
        anchors.verticalCenter: parent.verticalCenter

        Rectangle {
            id: groove
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            height: slideMouse.containsMouse || slideMouse.pressed ? 5 : 4
            radius: height / 2
            color: Theme.alpha(Theme.text, 0.13)

            Behavior on height { NumberAnimation { duration: Theme.fast } }

            Rectangle {
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                width: parent.width * root._fraction
                radius: height / 2
                color: slideMouse.containsMouse || slideMouse.pressed
                       ? Theme.accent : Theme.alpha(Theme.text, 0.62)
                Behavior on color { ColorAnimation { duration: Theme.fast } }
            }
        }

        Rectangle {
            width: 11
            height: 11
            radius: 5.5
            color: Theme.text
            anchors.verticalCenter: groove.verticalCenter
            x: groove.width * root._fraction - width / 2
            opacity: slideMouse.containsMouse || slideMouse.pressed ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
            Behavior on x {
                NumberAnimation { duration: Theme.fast; easing.type: Theme.easing }
            }
        }

        MouseArea {
            id: slideMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            preventStealing: true

            onPressed: function (e) { root._applyFraction(e.x / Math.max(1, groove.width)) }
            onPositionChanged: function (e) {
                if (pressed)
                    root._applyFraction(e.x / Math.max(1, groove.width))
            }
            // One stop per notch rather than a fraction of the range: the whole
            // point of this slider is that a step always does something.
            onWheel: function (e) {
                root.applyColumns(root.columns + (e.angleDelta.y > 0 ? -1 : 1))
            }
        }

        // The number the stops actually mean, which the groove cannot show. In
        // the same place as the sort chevron's tooltip beside it.
        NocturneTooltip {
            text: Fmt.count(root.columns, "across", "across")
            visible: slideMouse.containsMouse || slideMouse.pressed
        }
    }

    Icon {
        anchors.verticalCenter: parent.verticalCenter
        name: "tilesLarge"
        size: 14
        color: Theme.faint
    }
}
