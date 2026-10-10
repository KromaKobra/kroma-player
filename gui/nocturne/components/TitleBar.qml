import QtQuick
import QtQuick.Window
import Nocturne

// Custom title bar for the frameless window: wordmark, drag region, and window
// controls. Double-clicking the drag region toggles maximise, matching what a
// native title bar would do.
Item {
    id: root

    property var targetWindow: null
    property string caption: ""

    implicitHeight: Theme.titleBarHeight

    // Chrome, not background: a press on the bar must not drop the track
    // selection. The drag region below covers everything left of the window
    // buttons already; this catches the margin beside them. First child, so the
    // drag region and the buttons still get their own presses. See the ambient
    // catcher in main.qml.
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
    }

    // ------------------------------------------------------------- wordmark

    Row {
        anchors.left: parent.left
        anchors.leftMargin: 18
        anchors.verticalCenter: parent.verticalCenter
        spacing: 9

        // A copy of assets/icon.png: the bundle holds only this GUI's own tree.
        // sourceSize decodes the 512 px original at display size, doubled for
        // high-DPI screens, rather than keeping all of it in memory.
        Image {
            anchors.verticalCenter: parent.verticalCenter
            width: 18
            height: 18
            source: "../assets/icon.png"
            sourceSize: Qt.size(36, 36)
            fillMode: Image.PreserveAspectFit
            smooth: true
            mipmap: true
        }

        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: "Kroma Player"
            color: Theme.text
            font.family: Theme.displayFamily
            font.pixelSize: Theme.fzBody
            font.weight: Font.DemiBold
            font.letterSpacing: 0.4
        }

        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: root.caption
            visible: text !== ""
            color: Theme.faint
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }
    }

    // ----------------------------------------------------------- drag region

    MouseArea {
        anchors.left: parent.left
        anchors.right: controls.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        acceptedButtons: Qt.LeftButton

        onPressed: {
            if (root.targetWindow)
                root.targetWindow.startSystemMove()
        }
        onDoubleClicked: root.toggleMaximised()
    }

    function toggleMaximised() {
        if (!targetWindow)
            return
        if (targetWindow.visibility === Window.Maximized)
            targetWindow.showNormal()
        else
            targetWindow.showMaximized()
    }

    // -------------------------------------------------------- window buttons

    Row {
        id: controls
        anchors.right: parent.right
        anchors.rightMargin: 6
        anchors.verticalCenter: parent.verticalCenter
        spacing: 0

        IconButton {
            icon: "winMin"
            iconSize: 15
            diameter: 32
            color: Theme.dim
            onClicked: if (root.targetWindow) root.targetWindow.showMinimized()
        }

        IconButton {
            icon: root.targetWindow && root.targetWindow.visibility === Window.Maximized
                  ? "winRestore" : "winMax"
            iconSize: 14
            diameter: 32
            color: Theme.dim
            onClicked: root.toggleMaximised()
        }

        IconButton {
            icon: "winClose"
            iconSize: 15
            diameter: 32
            color: Theme.dim
            hoverColor: Theme.closeHoverInk
            hoverBackground: Theme.closeHover
            onClicked: if (root.targetWindow) root.targetWindow.close()
        }
    }
}
