import QtQuick
import Nocturne

// Scrub bar. The elapsed fill uses the accent gradient; the groove thickens and
// grows a handle on hover so it stays unobtrusive while still being easy to hit.
//
// While dragging, the displayed position comes from the drag rather than from
// the backend, so the handle does not fight position updates arriving at the
// player's tick rate.
Item {
    id: root

    property real position: 0
    property real duration: 0
    property bool interactive: true
    property int grooveHeight: 4
    property int hoverHeight: 6

    signal seeked(real seconds)
    signal scrubbing(real seconds)

    readonly property real fraction: duration > 0
                                     ? Math.min(1, Math.max(0, position / duration)) : 0
    property bool dragging: false
    property real dragFraction: 0
    readonly property real shownFraction: dragging ? dragFraction : fraction
    readonly property bool hot: (mouse.containsMouse || dragging) && interactive && duration > 0

    implicitHeight: 16

    function _fractionAt(x) {
        return Math.min(1, Math.max(0, x / Math.max(1, groove.width)))
    }

    Rectangle {
        id: groove
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        height: root.hot ? root.hoverHeight : root.grooveHeight
        radius: height / 2
        color: Theme.alpha(Theme.text, 0.13)

        Behavior on height {
            NumberAnimation { duration: Theme.fast; easing.type: Theme.easing }
        }

        Rectangle {
            id: fill
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: parent.width * root.shownFraction
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
        x: groove.width * root.shownFraction - width / 2
        opacity: root.hot ? 1 : 0
        scale: root.dragging ? 1.12 : 1.0

        Behavior on opacity { NumberAnimation { duration: Theme.fast } }
        Behavior on scale { NumberAnimation { duration: Theme.fast; easing.type: Theme.easing } }
        Behavior on width { NumberAnimation { duration: Theme.fast } }
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        anchors.topMargin: -4
        anchors.bottomMargin: -4
        hoverEnabled: true
        enabled: root.interactive && root.duration > 0
        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        preventStealing: true

        onPressed: function (event) {
            root.dragFraction = root._fractionAt(event.x)
            root.dragging = true
            root.scrubbing(root.dragFraction * root.duration)
        }
        onPositionChanged: function (event) {
            if (!root.dragging)
                return
            root.dragFraction = root._fractionAt(event.x)
            root.scrubbing(root.dragFraction * root.duration)
        }
        onReleased: function (event) {
            if (!root.dragging)
                return
            var f = root._fractionAt(event.x)
            root.dragging = false
            root.seeked(f * root.duration)
        }
        onCanceled: root.dragging = false
    }
}
