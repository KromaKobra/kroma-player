import QtQuick
import QtQuick.Controls
import Nocturne

SettingRow {
    id: root
    property real value: 0
    property real minimum: 0
    property real maximum: 100
    property real step: 1
    property int decimals: 0
    property string suffix: ""
    property int valueWidth: 76
    signal committed(real value)
    controlWidth: valueWidth + 84 + unit.implicitWidth

    function commit(v) {
        if (!isFinite(v)) return
        var next = Number(Math.max(minimum, Math.min(maximum, v)).toFixed(decimals))
        if (next !== value) committed(next)
        input.text = Qt.binding(function() { return root.value.toFixed(root.decimals) })
    }
    Row {
        spacing: 4
        SettingButton {
            text: "−"
            width: 36
            enabled: root.value > root.minimum
            Accessible.name: "Decrease " + root.label
            onClicked: root.commit(root.value - root.step)
        }
        TextField {
            id: input
            objectName: "numberInput"
            width: root.valueWidth
            height: 36
            text: root.value.toFixed(root.decimals)
            selectByMouse: true
            horizontalAlignment: TextInput.AlignHCenter
            color: Theme.text
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzBody
            Accessible.name: root.label + (root.suffix ? " (" + root.suffix.trim() + ")" : "")
            Accessible.description: root.description + " Range: " + root.minimum + " to " + root.maximum
            validator: DoubleValidator {
                bottom: root.minimum
                top: root.maximum
                decimals: root.decimals
                notation: DoubleValidator.StandardNotation
                locale: "C"
            }
            onEditingFinished: if (text.trim() && acceptableInput) root.commit(Number(text))
            onActiveFocusChanged: if (!activeFocus) text = Qt.binding(function() { return root.value.toFixed(root.decimals) })
            Keys.onUpPressed: root.commit(root.value + root.step)
            Keys.onDownPressed: root.commit(root.value - root.step)
            Keys.onEscapePressed: {
                text = Qt.binding(function() { return root.value.toFixed(root.decimals) })
                focus = false
            }
            background: Rectangle {
                radius: Theme.radiusSm
                color: Theme.sunken
                border.color: input.activeFocus ? (input.acceptableInput ? Theme.accent : Theme.danger) : Theme.line
            }
        }
        SettingButton {
            text: "+"
            width: 36
            enabled: root.value < root.maximum
            Accessible.name: "Increase " + root.label
            onClicked: root.commit(root.value + root.step)
        }
        Text {
            id: unit
            anchors.verticalCenter: parent.verticalCenter
            text: root.suffix
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }
    }
}
