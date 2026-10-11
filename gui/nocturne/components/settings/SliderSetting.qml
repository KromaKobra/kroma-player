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
    signal committed(real value)
    controlWidth: 260
    Column {
        width: parent.width
        spacing: 2
        Text {
            text: slider.value.toFixed(root.decimals) + root.suffix
            color: Theme.text
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzBody
            font.weight: Font.DemiBold
        }
        Slider {
            id: slider
            width: parent.width
            height: 28
            from: root.minimum
            to: root.maximum
            stepSize: root.step
            value: root.value
            Accessible.name: root.label
            Accessible.description: root.description
            onMoved: {
                root.committed(value)
                value = Qt.binding(function() { return root.value })
            }
            Keys.onPressed: function(event) {
                if (event.key === Qt.Key_Home || event.key === Qt.Key_End) {
                    root.committed(event.key === Qt.Key_Home ? root.minimum : root.maximum)
                    event.accepted = true
                }
            }
            background: Rectangle {
                x: slider.leftPadding
                y: (slider.height - height) / 2
                width: slider.availableWidth
                height: 4
                radius: 2
                color: Theme.line
                Rectangle { width: slider.visualPosition * parent.width; height: parent.height; radius: 2; color: Theme.accent }
            }
            handle: Rectangle {
                x: slider.leftPadding + slider.visualPosition * (slider.availableWidth - width)
                y: (slider.height - height) / 2
                width: 18
                height: 18
                radius: 9
                color: Theme.text
                border.color: slider.visualFocus ? Theme.accent : Theme.line
                border.width: 2
            }
        }
        Row {
            width: parent.width
            Text { width: parent.width / 2; text: root.minimum + root.suffix; color: Theme.dim; font.pixelSize: Theme.fzTiny }
            Text { width: parent.width / 2; horizontalAlignment: Text.AlignRight; text: root.maximum + root.suffix; color: Theme.dim; font.pixelSize: Theme.fzTiny }
        }
    }
}
