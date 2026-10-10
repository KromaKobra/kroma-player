import QtQuick
import Nocturne

// A tick box with a label, a figure beside it, and a line of small print under
// it. Written for the import options card, where the list of what was found is
// also the control for what gets taken — so each row has to carry a count and a
// consequence as well as a name.
//
// It follows ToggleSwitch's discipline rather than Pill's: it never ticks
// itself. It draws the `checked` it is handed and says it was clicked, which is
// what lets a card cancel without an undo — nothing changed.
Item {
    id: root

    property bool checked: true
    // The thing being chosen, and how many of it there are. `detail` sits to
    // the right of the count, greyed, and is where the cost of the choice goes.
    property string label: ""
    property string count: ""
    property string detail: ""
    // Small print under the row. Empty hides the line rather than reserving it.
    property string hint: ""
    property bool enabledAction: true

    signal toggled(bool value)

    implicitHeight: Math.max(34, textColumn.implicitHeight + 10)
    implicitWidth: 300

    // An unticked row is still readable — it has to be, or it could not be
    // ticked back on — but it stops competing with the ones that are in.
    opacity: !root.enabledAction ? 0.45 : (root.checked ? 1 : 0.55)

    Behavior on opacity { NumberAnimation { duration: Theme.normal } }

    Rectangle {
        id: box
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.topMargin: 5
        width: 18
        height: 18
        radius: Theme.radiusSm
        color: root.checked ? Theme.accent : "transparent"
        border.width: 1
        border.color: root.checked ? Theme.accent : Theme.lineHard

        Behavior on color { ColorAnimation { duration: Theme.fast } }
        Behavior on border.color { ColorAnimation { duration: Theme.fast } }

        Icon {
            anchors.centerIn: parent
            name: "check"
            size: 13
            strokeWidth: 2.4
            color: Theme.accentInk
            opacity: root.checked ? 1 : 0

            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
        }
    }

    Column {
        id: textColumn
        anchors.left: box.right
        anchors.leftMargin: Theme.gapSm + 2
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        spacing: 1

        Row {
            spacing: Theme.gapSm

            Text {
                text: root.label
                color: Theme.text
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzBody
                font.weight: Font.Medium
            }
            Text {
                text: root.count
                visible: text !== ""
                color: Theme.dim
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzBody
                // Figures line up down the column only if they do not reflow
                // with the width of the word beside them.
                font.features: ({ "tnum": 1 })
            }
            Text {
                text: root.detail
                visible: text !== ""
                color: Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
                anchors.verticalCenter: parent.verticalCenter
            }
        }

        Text {
            width: textColumn.width
            text: root.hint
            visible: text !== ""
            wrapMode: Text.WordWrap
            color: Theme.faint
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzTiny
        }
    }

    MouseArea {
        anchors.fill: parent
        enabled: root.enabledAction
        cursorShape: Qt.PointingHandCursor
        onClicked: root.toggled(!root.checked)
    }
}
