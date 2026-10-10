import QtQuick
import Nocturne

// A labelled numeric setting with − / + steppers, laid out to match
// ToggleSwitch so the two stack cleanly in a settings column.
//
// The value is not bound straight to the backend property: it is committed on
// click, and pulled back in whenever the backend's value changes. That keeps a
// held-down stepper from writing to the settings file on every repeat.
Item {
    id: root

    property real value: 0
    property real minimum: 0
    property real maximum: 100
    property real step: 1
    property int decimals: 0
    property string suffix: ""
    // Room for the readout. Enough for a plain count by default; a signed value
    // with a unit after it needs more before it runs into the + button.
    property int valueWidth: 62
    property string label: ""
    property string description: ""

    signal committed(real value)

    function _clamp(v) {
        return Math.max(root.minimum, Math.min(root.maximum, v))
    }

    function _nudge(delta) {
        var next = _clamp(root.value + delta)
        if (next === root.value)
            return
        root.value = next
        root.committed(next)
    }

    implicitHeight: Math.max(38, textColumn.implicitHeight + 12)
    implicitWidth: 300

    Column {
        id: textColumn
        anchors.left: parent.left
        anchors.right: stepper.left
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

    Row {
        id: stepper
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        spacing: 2

        component StepButton: Rectangle {
            id: stepButton

            property string glyph: ""
            property bool available: true

            signal pressed()

            width: 26
            height: 26
            radius: Theme.radiusSm
            color: stepMouse.containsMouse && stepButton.available
                   ? Theme.alpha(Theme.text, 0.10) : Theme.alpha(Theme.text, 0.04)
            opacity: stepButton.available ? 1 : 0.35

            Behavior on color { ColorAnimation { duration: Theme.fast } }

            Text {
                anchors.centerIn: parent
                text: stepButton.glyph
                color: Theme.text
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzBody
            }

            MouseArea {
                id: stepMouse
                anchors.fill: parent
                hoverEnabled: true
                enabled: stepButton.available
                cursorShape: Qt.PointingHandCursor
                onClicked: stepButton.pressed()
            }
        }

        StepButton {
            anchors.verticalCenter: parent.verticalCenter
            glyph: "−"
            available: root.value > root.minimum
            onPressed: root._nudge(-root.step)
        }

        Text {
            anchors.verticalCenter: parent.verticalCenter
            width: root.valueWidth
            horizontalAlignment: Text.AlignHCenter
            text: root.value.toFixed(root.decimals) + root.suffix
            color: Theme.text
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzBody
            font.weight: Font.Medium
        }

        StepButton {
            anchors.verticalCenter: parent.verticalCenter
            glyph: "+"
            available: root.value < root.maximum
            onPressed: root._nudge(root.step)
        }
    }
}
