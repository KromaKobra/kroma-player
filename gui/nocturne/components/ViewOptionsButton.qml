import QtQuick
import QtQuick.Controls
import Nocturne

// Native button semantics, shared placement and immediate press feedback.
Button {
    id: root
    property string collectionName: "Library"
    implicitWidth: 36
    implicitHeight: 36
    padding: 0
    hoverEnabled: true
    Accessible.name: collectionName + " view options"
    ToolTip.text: Accessible.name
    ToolTip.visible: hovered
    ToolTip.delay: 500
    contentItem: Item {
        Icon {
            anchors.centerIn: parent
            name: "more"
            size: 18
            rotation: 90
            color: Theme.text
        }
    }
    background: Rectangle {
        radius: height / 2
        color: root.down ? Theme.accentSoft
                        : root.hovered ? Theme.raised : Theme.alpha(Theme.text, 0.07)
        border.width: root.visualFocus ? 2 : 0
        border.color: Theme.accent
    }
}
