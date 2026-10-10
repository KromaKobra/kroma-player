import QtQuick

// Round icon button with a hover halo. Used for transport, window controls,
// and row actions.
Item {
    id: root

    property string icon
    property int iconSize: 19
    property int diameter: 36
    property color color: Theme.dim
    property color hoverColor: Theme.text
    property color hoverBackground: Theme.raised
    property real strokeWidth: 1.7
    property bool active: false
    property color activeColor: Theme.accent
    property bool enabledAction: true
    property string tooltip: ""

    signal clicked()

    implicitWidth: diameter
    implicitHeight: diameter
    opacity: enabledAction ? 1.0 : 0.35

    Behavior on opacity { NumberAnimation { duration: Theme.fast } }

    Rectangle {
        id: halo
        anchors.centerIn: parent
        width: root.diameter
        height: root.diameter
        radius: width / 2
        color: root.hoverBackground
        opacity: mouse.containsMouse && root.enabledAction ? 1 : 0
        scale: mouse.pressed ? 0.9 : 1.0

        Behavior on opacity { NumberAnimation { duration: Theme.fast } }
        Behavior on scale { NumberAnimation { duration: Theme.fast; easing.type: Theme.easing } }
    }

    Icon {
        anchors.centerIn: parent
        name: root.icon
        size: root.iconSize
        strokeWidth: root.strokeWidth
        color: root.active ? root.activeColor
                           : (mouse.containsMouse && root.enabledAction ? root.hoverColor
                                                                        : root.color)
        scale: mouse.pressed ? 0.88 : 1.0
        Behavior on scale { NumberAnimation { duration: Theme.fast; easing.type: Theme.easing } }
    }

    // Active-state dot, so toggles read as on/off without a colour-only cue.
    Rectangle {
        visible: root.active
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 1
        width: 3.5
        height: 3.5
        radius: 2
        color: root.activeColor
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        enabled: root.enabledAction
        cursorShape: Qt.PointingHandCursor
        onClicked: root.clicked()
    }

    NocturneTooltip {
        text: root.tooltip
        visible: root.tooltip !== "" && mouse.containsMouse
    }
}
