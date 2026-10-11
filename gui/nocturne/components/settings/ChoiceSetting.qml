import QtQuick
import QtQuick.Controls
import Nocturne

SettingRow {
    id: root
    property var options: []
    property int currentIndex: -1
    property bool segmented: false
    signal selected(int index)
    controlWidth: segmented ? 260 : 230
    Flow {
        width: parent.width
        spacing: 4
        visible: root.segmented
        Repeater {
            model: root.segmented ? root.options : []
            delegate: SettingButton {
                required property int index
                required property var modelData
                text: modelData
                highlighted: root.currentIndex === index
                Accessible.role: Accessible.RadioButton
                Accessible.checked: highlighted
                Accessible.name: root.label + ": " + text
                background: Rectangle {
                    radius: Theme.radiusSm
                    color: parent.highlighted ? Theme.accentSoft : parent.down ? Theme.raised : Theme.sunken
                    border.color: parent.visualFocus || parent.highlighted ? Theme.accent : Theme.line
                }
                onClicked: root.selected(index)
            }
        }
    }
    ComboBox {
        id: combo
        visible: !root.segmented
        width: parent.width
        height: 36
        model: root.segmented ? [] : root.options
        currentIndex: root.currentIndex
        Accessible.name: root.label
        Accessible.description: root.description
        onActivated: function(index) {
            root.selected(index)
            currentIndex = Qt.binding(function() { return root.currentIndex })
        }
        palette.button: Theme.sunken
        palette.buttonText: Theme.text
        palette.base: Theme.card
        palette.text: Theme.text
        palette.highlight: Theme.accent
        palette.highlightedText: Theme.accentInk
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fzSmall
        leftPadding: 12
        rightPadding: 30
        background: Rectangle {
            radius: Theme.radiusSm
            color: combo.down ? Theme.raised : Theme.sunken
            border.color: combo.visualFocus ? Theme.accent : Theme.line
            border.width: combo.visualFocus ? 2 : 1
        }
        contentItem: Text {
            text: combo.displayText
            font: combo.font
            color: Theme.text
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    }
}
