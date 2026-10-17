import QtQuick
import Nocturne

// Consistent page header. Anything declared inside lands in the right-aligned
// action row.
//
// A page opened inside a tab sets `showBack` and gets the same leading arrow,
// in the same place, that DetailHero gives the artist, album and playlist
// pages — so going up a level is one control wherever you are.
Item {
    id: root

    property string title: ""
    property string subtitle: ""
    property int sideMargin: Theme.gapXl
    property bool showBack: false
    // Names where the arrow goes, not just that it goes back.
    property string backTooltip: "Back"

    signal backRequested()

    default property alias actions: actionRow.data

    implicitHeight: 74

    IconButton {
        id: backButton
        objectName: "viewHeaderBack"
        visible: root.showBack
        anchors.left: parent.left
        anchors.leftMargin: Theme.gapLg
        anchors.verticalCenter: parent.verticalCenter
        icon: "chevronLeft"
        iconSize: 19
        diameter: 34
        tooltip: root.backTooltip
        Accessible.role: Accessible.Button
        Accessible.name: root.backTooltip
        onClicked: root.backRequested()
    }

    Column {
        anchors.left: root.showBack ? backButton.right : parent.left
        anchors.leftMargin: root.showBack ? Theme.gapSm : root.sideMargin
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
