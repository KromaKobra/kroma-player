import QtQuick
import Nocturne

// Text button in three weights: filled (accent), soft, and ghost.
Item {
    id: root

    property string text: ""
    property string iconName: ""
    // "filled" | "soft" | "ghost"
    property string variant: "soft"
    property bool danger: false
    property bool enabledAction: true
    property int hPadding: 16
    property int fontSize: Theme.fzBody

    signal clicked()

    implicitHeight: 36
    implicitWidth: content.implicitWidth + hPadding * 2

    readonly property color baseColor: danger ? Theme.danger : Theme.accent

    opacity: enabledAction ? 1 : 0.4
    Behavior on opacity { NumberAnimation { duration: Theme.fast } }

    Rectangle {
        id: bg
        anchors.fill: parent
        radius: Theme.radius

        color: {
            if (root.variant === "filled")
                return mouse.containsMouse ? Qt.lighter(root.baseColor, 1.14) : root.baseColor
            if (root.variant === "soft")
                return mouse.containsMouse ? Theme.alpha(root.baseColor, 0.22)
                                           : Theme.alpha(root.baseColor, 0.13)
            return mouse.containsMouse ? Theme.alpha(Theme.text, 0.08) : "transparent"
        }
        border.width: root.variant === "ghost" ? 1 : 0
        border.color: Theme.line
        scale: mouse.pressed && root.enabledAction ? 0.975 : 1.0

        Behavior on color { ColorAnimation { duration: Theme.fast } }
        Behavior on scale { NumberAnimation { duration: Theme.fast; easing.type: Theme.easing } }
    }

    Row {
        id: content
        anchors.centerIn: parent
        spacing: 8

        Icon {
            anchors.verticalCenter: parent.verticalCenter
            visible: root.iconName !== ""
            name: root.iconName
            size: 16
            color: root.variant === "filled" ? Theme.accentInk
                 : (root.danger ? Theme.danger : Theme.text)
        }

        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: root.text
            color: root.variant === "filled" ? Theme.accentInk
                 : (root.danger ? Theme.danger : Theme.text)
            font.family: Theme.fontFamily
            font.pixelSize: root.fontSize
            font.weight: Font.DemiBold
        }
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        enabled: root.enabledAction
        cursorShape: Qt.PointingHandCursor
        onClicked: root.clicked()
    }
}
