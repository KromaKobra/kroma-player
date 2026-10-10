import QtQuick
import Nocturne

// One labelled row of the track editor whose input is a slider rather than a
// text field. Same geometry as EditFieldRow — the same 116px label column — so
// the two stack without the form looking assembled from parts.
//
// The fill grows out of the *origin* rather than out of the left edge, because
// the values this is built for are trims: zero is the middle and the interesting
// thing about a value is which side of nothing it is on, not how far along a
// range it sits.
//
// Like EditFieldRow, `value` is assigned into and `edited` only fires for
// pointer input, so the editor's pushes on load and revert do not read as user
// changes. `edited` fires *throughout* a drag, not on release: the control it
// exists for is audible while you move it, and a value that only arrived at the
// end would make that a guess.
Item {
    id: root

    property string label: ""
    property string hint: ""
    property real value: 0
    property real minimum: -12
    property real maximum: 12
    property real origin: 0
    property real step: 0.5
    property int decimals: 1
    property string suffix: ""
    // Signed, so a boost and a cut are told apart at a glance rather than by
    // reading the number.
    property bool showSign: true
    property int readoutWidth: 58

    signal edited(real value)

    readonly property int labelWidth: 116

    // Kept for the handle's own styling only; the position comes from `value`.
    property bool dragging: false
    // Clamped on the way out rather than trusting the push. The view model does
    // clamp, but the fill is drawn as a fraction of the groove and a value from
    // outside the range would paint past the end of it.
    readonly property real shownValue: _clamp(value)

    implicitHeight: Math.max(30, labels.implicitHeight)

    function _clamp(v) {
        return Math.min(root.maximum, Math.max(root.minimum, v))
    }

    function _snap(v) {
        return root.step > 0 ? Math.round(v / root.step) * root.step : v
    }

    function _fractionOf(v) {
        var span = root.maximum - root.minimum
        return span > 0 ? (v - root.minimum) / span : 0
    }

    function _valueAt(x) {
        var f = Math.min(1, Math.max(0, x / Math.max(1, groove.width)))
        return root._clamp(root._snap(root.minimum + f * (root.maximum - root.minimum)))
    }

    // The handle owns where it is, exactly as a NocturneField owns its text: it
    // moves because it was dragged, not because something pushed a value back.
    // Waiting to be told would leave the handle wherever it was last loaded the
    // moment a drag ends — the editor stages a change without announcing one,
    // so nothing is coming to move it.
    //
    // Only on an actual change of the snapped value. A drag crosses many pixels
    // per step, and every repeat of the same number would be another trip
    // through the view model and another push at the audio output.
    function _commit(v) {
        if (v === root.value)
            return
        root.value = v
        root.edited(v)
    }

    function _text(v) {
        var body = Math.abs(v).toFixed(root.decimals) + root.suffix
        if (!root.showSign)
            return v.toFixed(root.decimals) + root.suffix
        if (Math.abs(v) < Math.pow(10, -root.decimals) / 2)
            return body
        return (v > 0 ? "+" : "−") + body
    }

    Column {
        id: labels
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        width: root.labelWidth - Theme.gap
        spacing: 2

        Text {
            width: parent.width
            text: root.label
            wrapMode: Text.WordWrap
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }
        Text {
            width: parent.width
            text: root.hint
            visible: text !== ""
            wrapMode: Text.WordWrap
            color: Theme.alpha(Theme.text, 0.3)
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzTiny
            lineHeight: 1.2
        }
    }

    Text {
        id: readout
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        width: root.readoutWidth
        horizontalAlignment: Text.AlignRight
        text: root._text(root.shownValue)
        color: root.shownValue === root.origin ? Theme.dim : Theme.text
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fzSmall
        font.weight: Font.Medium
    }

    Item {
        id: track
        anchors.left: parent.left
        anchors.leftMargin: root.labelWidth
        anchors.right: readout.left
        anchors.rightMargin: Theme.gap
        anchors.verticalCenter: parent.verticalCenter
        height: root.implicitHeight

        readonly property real originFraction: root._fractionOf(root.origin)
        readonly property real valueFraction: root._fractionOf(root.shownValue)

        Rectangle {
            id: groove
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            height: mouse.containsMouse || root.dragging ? 6 : 4
            radius: height / 2
            color: Theme.alpha(Theme.text, 0.13)

            Behavior on height {
                NumberAnimation { duration: Theme.fast; easing.type: Theme.easing }
            }

            // The origin tick, so centre is findable without watching the number.
            Rectangle {
                x: parent.width * track.originFraction - width / 2
                anchors.verticalCenter: parent.verticalCenter
                width: 2
                height: 10
                radius: 1
                color: Theme.alpha(Theme.text, 0.22)
            }

            Rectangle {
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                x: parent.width * Math.min(track.originFraction, track.valueFraction)
                width: parent.width * Math.abs(track.valueFraction - track.originFraction)
                radius: height / 2
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0.0; color: Theme.accent }
                    GradientStop { position: 1.0; color: Theme.accentAlt }
                }
            }
        }

        Rectangle {
            id: handle
            width: root.dragging ? 14 : 12
            height: width
            radius: width / 2
            color: Theme.text
            anchors.verticalCenter: groove.verticalCenter
            x: groove.width * track.valueFraction - width / 2
            opacity: mouse.containsMouse || root.dragging ? 1 : 0.55
            scale: root.dragging ? 1.12 : 1.0

            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
            Behavior on scale { NumberAnimation { duration: Theme.fast; easing.type: Theme.easing } }
            Behavior on width { NumberAnimation { duration: Theme.fast } }
        }

        MouseArea {
            id: mouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            preventStealing: true

            onPressed: function (event) {
                root.dragging = true
                root._commit(root._valueAt(event.x))
            }
            onPositionChanged: function (event) {
                if (root.dragging)
                    root._commit(root._valueAt(event.x))
            }
            onReleased: root.dragging = false
            onCanceled: root.dragging = false
            // Back to the origin. The fastest way to answer "was that actually
            // better?", which is the question anyone setting a level by ear is
            // asking every few seconds.
            onDoubleClicked: {
                root.dragging = false
                root._commit(root.origin)
            }
            onWheel: function (event) {
                var delta = event.angleDelta.y > 0 ? root.step : -root.step
                root._commit(root._clamp(root._snap(root.value + delta)))
            }
        }
    }
}
