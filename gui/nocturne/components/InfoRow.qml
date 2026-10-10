import QtQuick
import Nocturne

// Label/value pair in the details panel. Hides itself when there is no value,
// so the panel never shows a column of blanks.
Item {
    id: root

    property string label: ""
    property string value: ""
    property bool mono: false

    width: parent ? parent.width : 0
    height: visible ? Math.max(24, valueText.implicitHeight + 8) : 0
    visible: value !== "" && value !== "—"

    Text {
        id: labelText
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.topMargin: 4
        width: 74
        text: root.label
        color: Theme.faint
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fzSmall
    }

    Text {
        id: valueText
        anchors.left: labelText.right
        anchors.leftMargin: Theme.gapSm
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.topMargin: 4
        text: root.value
        wrapMode: Text.WordWrap
        color: Theme.text
        font.family: root.mono ? "Consolas" : Theme.fontFamily
        font.pixelSize: root.mono ? Theme.fzTiny : Theme.fzSmall
    }
}
