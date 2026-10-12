import QtQuick
import QtQuick.Controls
import Nocturne

Button {
    id: root
    property string displayTitle: ""
    property string iconName: "play"
    property bool artworkOverlay: false
    property real artworkRadius: Theme.radius
    property string actionName: "Play "
    implicitWidth: 36
    implicitHeight: 36
    Accessible.name: actionName + displayTitle
    contentItem: Item {
        Icon { anchors.centerIn: parent; name: root.iconName; size: 16; color: root.artworkOverlay ? Theme.imageInk : Theme.accentInk }
    }
    background: Rectangle {
        radius: root.artworkOverlay ? root.artworkRadius : height / 2
        color: root.artworkOverlay ? (root.down ? Qt.darker(Theme.imageScrim, 1.2) : Theme.imageScrim)
               : root.down ? Qt.darker(Theme.accent, 1.1)
               : root.visualFocus ? Qt.lighter(Theme.accent, 1.2) : Theme.accent
    }
}
