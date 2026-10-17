import QtQuick
import QtQuick.Window
import Nocturne

// Edge and corner grips for a frameless window. Without these the window would
// have no way to be resized at all, so they cover all eight directions and are
// disabled while maximised.
Item {
    id: root

    property var targetWindow: null
    property int grip: 5
    property int corner: 14

    anchors.fill: parent
    z: 900

    readonly property bool allowed: targetWindow
                                    && targetWindow.visibility !== Window.Maximized
                                    && targetWindow.visibility !== Window.FullScreen

    component Grip: MouseArea {
        id: gripArea
        property int edges: 0
        enabled: root.allowed
        visible: root.allowed
        acceptedButtons: Qt.LeftButton
        containmentMask: QtObject {
            function contains(point: point): bool { return ScrollBarPriority.resizeContains(gripArea, point) }
        }
        onPressed: if (root.targetWindow) root.targetWindow.startSystemResize(edges)
    }

    // ------------------------------------------------------------ edges
    Grip {
        edges: Qt.LeftEdge
        cursorShape: Qt.SizeHorCursor
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.topMargin: root.corner
        anchors.bottomMargin: root.corner
        width: root.grip
    }
    Grip {
        edges: Qt.RightEdge
        cursorShape: Qt.SizeHorCursor
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.topMargin: root.corner
        anchors.bottomMargin: root.corner
        width: root.grip
    }
    Grip {
        edges: Qt.TopEdge
        cursorShape: Qt.SizeVerCursor
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: root.corner
        anchors.rightMargin: root.corner
        height: root.grip
    }
    Grip {
        edges: Qt.BottomEdge
        cursorShape: Qt.SizeVerCursor
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: root.corner
        anchors.rightMargin: root.corner
        height: root.grip
    }

    // ---------------------------------------------------------- corners
    Grip {
        edges: Qt.LeftEdge | Qt.TopEdge
        cursorShape: Qt.SizeFDiagCursor
        anchors.left: parent.left
        anchors.top: parent.top
        width: root.corner
        height: root.corner
    }
    Grip {
        edges: Qt.RightEdge | Qt.TopEdge
        cursorShape: Qt.SizeBDiagCursor
        anchors.right: parent.right
        anchors.top: parent.top
        width: root.corner
        height: root.corner
    }
    Grip {
        edges: Qt.LeftEdge | Qt.BottomEdge
        cursorShape: Qt.SizeBDiagCursor
        anchors.left: parent.left
        anchors.bottom: parent.bottom
        width: root.corner
        height: root.corner
    }
    Grip {
        edges: Qt.RightEdge | Qt.BottomEdge
        cursorShape: Qt.SizeFDiagCursor
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        width: root.corner
        height: root.corner
    }
}
