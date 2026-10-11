import QtQuick
import QtQuick.Controls
import Nocturne

SettingRow {
    id: root
    property string path: ""
    property string placeholder: "No folder selected"
    property string actionText: "Choose…"
    property string secondaryText: ""
    signal requested()
    signal secondaryRequested()
    stacked: true
    Column {
        width: parent.width
        spacing: 8
        TextField {
            width: parent.width
            height: 36
            text: root.path
            placeholderText: root.placeholder
            readOnly: true
            selectByMouse: true
            color: Theme.text
            placeholderTextColor: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
            Accessible.name: root.label
            background: Rectangle {
                radius: Theme.radiusSm
                color: Theme.sunken
                border.color: parent.activeFocus ? Theme.accent : Theme.line
            }
        }
        Flow {
            width: parent.width
            spacing: 8
            SettingButton { text: root.actionText; onClicked: root.requested() }
            SettingButton { text: root.secondaryText; visible: text !== ""; onClicked: root.secondaryRequested() }
        }
    }
}
