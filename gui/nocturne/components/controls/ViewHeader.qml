import QtQuick
import Nocturne

// Consistent page header. Anything declared inside lands in the right-aligned
// action row.
Item {
    id: root

    property string title: ""
    property string subtitle: ""
    property int sideMargin: Theme.gapXl

    default property alias actions: actionRow.data

    implicitHeight: 74

    Column {
        anchors.left: parent.left
        anchors.leftMargin: root.sideMargin
        anchors.right: actionRow.left
        anchors.rightMargin: Theme.gap
        anchors.verticalCenter: parent.verticalCenter
        spacing: 3

        Text {
            width: parent.width
            text: root.title
            elide: Text.ElideRight
            color: Theme.text
            font.family: Theme.displayFamily
            font.pixelSize: Theme.fzTitle
            font.weight: Font.DemiBold
            font.letterSpacing: -0.3
        }

        Text {
            width: parent.width
            text: root.subtitle
            visible: text !== ""
            elide: Text.ElideRight
            color: Theme.faint
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }
    }

    Row {
        id: actionRow
        anchors.right: parent.right
        anchors.rightMargin: root.sideMargin
        anchors.verticalCenter: parent.verticalCenter
        spacing: Theme.gapSm
    }
}
