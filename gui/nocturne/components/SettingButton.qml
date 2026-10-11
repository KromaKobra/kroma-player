import QtQuick
import QtQuick.Controls
import Nocturne

// Native button semantics, keyboard activation, focus ring and instant press.
Button {
    id: root
    implicitHeight: 36
    implicitWidth: Math.max(36, label.implicitWidth + 26)
    hoverEnabled: true
    font.family: Theme.fontFamily
    font.pixelSize: Theme.fzSmall
    Accessible.name: text
    contentItem: Text {
        id: label
        text: root.text
        font: root.font
        color: root.enabled ? Theme.text : Theme.faint
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: Theme.radiusSm
        color: root.down ? Theme.accentSoft : root.hovered ? Theme.raised : Theme.sunken
        border.color: root.visualFocus ? Theme.accent : Theme.line
        border.width: root.visualFocus ? 2 : 1
    }
}
