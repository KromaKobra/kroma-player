import QtQuick
import QtQuick.Controls
import Nocturne

// One row of a context menu, with an optional leading icon and a danger accent
// for destructive entries.
MenuItem {
    id: root

    property string iconName: ""
    // A picture instead of a glyph — a plugin's own icon, from
    // `image://plugin/...`. Drawn as it is: a glyph takes the row's tint, a
    // picture keeps its colours.
    property string iconSource: ""
    property bool danger: false
    property string shortcutHint: ""

    implicitHeight: 34
    implicitWidth: 232
    padding: 0

    readonly property color tint: !enabled ? Theme.faint
                                : (danger ? Theme.danger
                                : (highlighted ? Theme.text : Theme.dim))

    contentItem: Item {
        Icon {
            id: glyph
            visible: root.iconName !== "" && root.iconSource === ""
            anchors.left: parent.left
            anchors.leftMargin: 11
            anchors.verticalCenter: parent.verticalCenter
            name: root.iconName
            size: 16
            color: root.tint
        }

        Image {
            id: picture
            visible: root.iconSource !== ""
            anchors.left: parent.left
            anchors.leftMargin: 11
            anchors.verticalCenter: parent.verticalCenter
            width: 16
            height: 16
            sourceSize: Qt.size(32, 32)
            source: root.iconSource
            fillMode: Image.PreserveAspectFit
            opacity: root.enabled ? 1 : 0.5
        }

        Text {
            readonly property Item lead: picture.visible ? picture : glyph
            anchors.left: lead.visible ? lead.right : parent.left
            anchors.leftMargin: lead.visible ? 11 : 13
            anchors.right: hint.left
            anchors.rightMargin: 8
            anchors.verticalCenter: parent.verticalCenter
            text: root.text
            elide: Text.ElideRight
            color: root.danger ? Theme.danger
                               : (root.enabled ? Theme.text : Theme.faint)
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzBody
        }

        Text {
            id: hint
            anchors.right: arrow.visible ? arrow.left : parent.right
            anchors.rightMargin: 11
            anchors.verticalCenter: parent.verticalCenter
            text: root.shortcutHint
            color: Theme.faint
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzTiny
        }

        Icon {
            id: arrow
            visible: root.subMenu !== null
            anchors.right: parent.right
            anchors.rightMargin: 9
            anchors.verticalCenter: parent.verticalCenter
            name: "chevronRight"
            size: 14
            color: root.tint
        }
    }

    background: Rectangle {
        radius: 7
        color: root.highlighted && root.enabled
               ? (root.danger ? Theme.alpha(Theme.danger, 0.14) : Theme.raised)
               : "transparent"
    }

    // Menus inherit the app-wide Controls style; the arrow indicator is drawn
    // in contentItem above, so suppress the built-in one.
    arrow: Item {}
    indicator: Item {}
}
