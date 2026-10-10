import QtQuick
import Nocturne

// Mute toggle plus a slider. Clicking the icon mutes and remembers the previous
// level so unmuting restores it.
Row {
    id: root

    property real volume: 0.5
    property int sliderWidth: 84
    property real _restore: 0.5

    signal volumeRequested(real value)

    spacing: 2
    height: 32

    readonly property string glyph: volume <= 0.001 ? "volumeMute"
                                  : (volume < 0.5 ? "volumeLow" : "volumeHigh")

    IconButton {
        anchors.verticalCenter: parent.verticalCenter
        icon: root.glyph
        iconSize: 18
        diameter: 30
        tooltip: root.volume <= 0.001 ? "Unmute" : "Mute"
        onClicked: {
            if (root.volume > 0.001) {
                root._restore = root.volume
                root.volumeRequested(0)
            } else {
                root.volumeRequested(root._restore > 0.02 ? root._restore : 0.4)
            }
        }
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
                width: parent.width * Math.min(1, Math.max(0, root.volume))
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
            x: groove.width * Math.min(1, Math.max(0, root.volume)) - width / 2
            opacity: slideMouse.containsMouse || slideMouse.pressed ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
        }

        MouseArea {
            id: slideMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            preventStealing: true

            function apply(x) {
                root.volumeRequested(Math.min(1, Math.max(0, x / Math.max(1, groove.width))))
            }
            onPressed: function (e) { apply(e.x) }
            onPositionChanged: function (e) { if (pressed) apply(e.x) }
            onWheel: function (e) {
                var step = e.angleDelta.y > 0 ? 0.05 : -0.05
                root.volumeRequested(Math.min(1, Math.max(0, root.volume + step)))
            }
        }
    }
}
