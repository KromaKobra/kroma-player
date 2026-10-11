import QtQuick
import QtQuick.Controls
import Nocturne

SettingRow {
    id: root
    property string accessibleLabel: label
    property bool checked: false
    signal toggled(bool value)
    stacked: false
    controlWidth: 50
    AbstractButton {
        id: control
        width: 50
        height: 36
        padding: 0
        checked: root.checked
        Accessible.name: root.accessibleLabel
        Accessible.description: root.description
        onClicked: root.toggled(!root.checked)
        checkable: false
        Accessible.role: Accessible.CheckBox
        Accessible.checked: root.checked
        background: Rectangle {
            x: 2
            y: 5
            width: 46
            height: 26
            radius: 13
            color: root.checked ? Theme.accent : Theme.alpha(Theme.text, control.down ? 0.25 : 0.15)
            border.color: control.visualFocus ? Theme.text : "transparent"
            border.width: 2
            Rectangle {
                x: root.checked ? 23 : 3
                y: 3
                width: 20
                height: 20
                radius: 10
                color: root.checked ? Theme.accentInk : Theme.text
                Behavior on x {
                    enabled: !Theme.prefs.reducedMotion
                    NumberAnimation { duration: Theme.fast; easing.type: Easing.OutCubic }
                }
            }
        }
    }
}
