import QtQuick
import Nocturne

Item {
    id: root

    property bool checked: false
    property string label: ""
    property string description: ""

    signal toggled(bool value)

    implicitHeight: Math.max(38, textColumn.implicitHeight + 12)
    implicitWidth: 300

    Column {
        id: textColumn
        anchors.left: parent.left
        anchors.right: knob.left
        anchors.rightMargin: Theme.gap
        anchors.verticalCenter: parent.verticalCenter
        spacing: 2

        Text {
            width: parent.width
            text: root.label
            color: Theme.text
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzBody
            font.weight: Font.Medium
        }
        Text {
            width: parent.width
            text: root.description
            visible: text !== ""
            wrapMode: Text.WordWrap
            color: Theme.faint
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzTiny
        }
    }

    Rectangle {
        id: knob
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        width: 42
        height: 24
        radius: 12
        color: root.checked ? Theme.accent : Theme.alpha(Theme.text, 0.12)
        border.width: 1
        border.color: root.checked ? Theme.accent : Theme.line

        Behavior on color { ColorAnimation { duration: Theme.normal } }

        Rectangle {
            width: 18
            height: 18
            radius: 9
            color: root.checked ? Theme.accentInk : Theme.dim
            anchors.verticalCenter: parent.verticalCenter
            x: root.checked ? parent.width - width - 3 : 3

            Behavior on x { NumberAnimation { duration: Theme.normal; easing.type: Theme.easing } }
            Behavior on color { ColorAnimation { duration: Theme.normal } }
        }
    }

    MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: root.toggled(!root.checked)
    }
}
