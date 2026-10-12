import QtQuick
import Nocturne

// Animated three-bar level meter marking the row that is playing. Pauses with
// the music, which makes the paused state readable at a glance.
Row {
    id: root

    property color color: Theme.accent
    property bool playing: true
    property real barWidth: 2.5
    property int maxHeight: 14

    spacing: 2.5
    height: maxHeight

    Repeater {
        model: [
            { min: 0.30, max: 1.00, dur: 520 },
            { min: 0.45, max: 0.70, dur: 700 },
            { min: 0.20, max: 0.90, dur: 610 }
        ]

        delegate: Rectangle {
            id: bar
            required property var modelData
            required property int index

            width: root.barWidth
            radius: width / 2
            color: root.color
            anchors.verticalCenter: parent.verticalCenter
            height: root.maxHeight * modelData.min

            SequentialAnimation on height {
                running: root.playing && root.visible && !Theme.prefs.reducedMotion
                loops: Animation.Infinite
                alwaysRunToEnd: false

                NumberAnimation {
                    to: root.maxHeight * bar.modelData.max
                    duration: bar.modelData.dur
                    easing.type: Easing.InOutSine
                }
                NumberAnimation {
                    to: root.maxHeight * bar.modelData.min
                    duration: bar.modelData.dur
                    easing.type: Easing.InOutSine
                }
            }

            // When paused, settle to a flat, quiet state. Stopping the animation
            // alone would freeze the bars mid-bounce, so drive them back down.
            Connections {
                target: root
                function onPlayingChanged() {
                    if (!root.playing)
                        bar.height = root.maxHeight * bar.modelData.min
                }
            }

            Behavior on height {
                enabled: !root.playing && !Theme.prefs.reducedMotion
                NumberAnimation { duration: Theme.normal; easing.type: Theme.easing }
            }
        }
    }
}
