import QtQuick
import QtQuick.Controls
import Nocturne

SettingRow {
    id: root
    property string text: ""
    property string placeholder: ""
    signal edited(string text)
    TextField {
        width: parent.width
        height: 36
        text: root.text
        placeholderText: root.placeholder
        selectByMouse: true
        color: Theme.text
        placeholderTextColor: Theme.dim
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fzSmall
        Accessible.name: root.label
        Accessible.description: root.description
        onTextEdited: root.edited(text)
        background: Rectangle {
            radius: Theme.radiusSm
            color: Theme.sunken
            border.color: parent.activeFocus ? Theme.accent : Theme.line
        }
    }
}
