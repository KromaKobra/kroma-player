import QtQuick

// Segmented control. `options` is a list of label strings; the selected index
// is animated with a sliding accent pill.
Item {
    id: root

    property var options: []
    property int currentIndex: 0
    property int itemPadding: 18
    property int fontSize: Theme.fzSmall

    signal selected(int index)

    implicitHeight: 32
    implicitWidth: track.implicitWidth

    Rectangle {
        id: track
        anchors.fill: parent
        radius: height / 2
        color: Theme.sunken
        border.width: 1
        border.color: Theme.line
        implicitWidth: row.implicitWidth + 6

        Rectangle {
            id: thumb
            y: 3
            height: parent.height - 6
            radius: height / 2
            color: Theme.accentGlow
            border.width: 1
            border.color: Theme.alpha(Theme.accent, 0.45)

            x: {
                var offset = 3
                for (var i = 0; i < root.currentIndex && i < row.children.length; i++)
                    offset += row.children[i].width
                return offset
            }
            width: row.children.length > root.currentIndex && root.currentIndex >= 0
                   ? row.children[root.currentIndex].width : 0

            Behavior on x { NumberAnimation { duration: Theme.normal; easing.type: Theme.easing } }
            Behavior on width { NumberAnimation { duration: Theme.normal; easing.type: Theme.easing } }
        }

        Row {
            id: row
            x: 3
            height: parent.height

            Repeater {
                model: root.options

                delegate: Item {
                    id: seg
                    required property int index
                    required property var modelData

                    width: label.implicitWidth + root.itemPadding * 2
                    height: row.height

                    Text {
                        id: label
                        anchors.centerIn: parent
                        text: seg.modelData
                        font.family: Theme.fontFamily
                        font.pixelSize: root.fontSize
                        font.weight: root.currentIndex === seg.index ? Font.DemiBold : Font.Medium
                        color: root.currentIndex === seg.index
                               ? Theme.text
                               : (segMouse.containsMouse ? Theme.dim : Theme.faint)

                        Behavior on color { ColorAnimation { duration: Theme.fast } }
                    }

                    MouseArea {
                        id: segMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            root.currentIndex = seg.index
                            root.selected(seg.index)
                        }
                    }
                }
            }
        }
    }
}
