import QtQuick
import Nocturne

// Transient status message. Slides up from the bottom of its parent and
// dismisses itself; clicking closes it early.
Item {
    id: root

    property int duration: 3600
    property string message: ""
    property bool isError: false
    property bool shown: false

    function show(text, error) {
        message = text
        isError = error === true
        shown = true
        timer.restart()
    }

    function dismiss() {
        shown = false
        timer.stop()
    }

    anchors.horizontalCenter: parent ? parent.horizontalCenter : undefined
    anchors.bottom: parent ? parent.bottom : undefined
    anchors.bottomMargin: shown ? Theme.gapXl : -60
    width: card.width
    height: card.height
    opacity: shown ? 1 : 0
    visible: opacity > 0
    z: 100

    Behavior on anchors.bottomMargin {
        NumberAnimation { duration: Theme.normal; easing.type: Theme.easing }
    }
    Behavior on opacity { NumberAnimation { duration: Theme.normal } }

    Timer {
        id: timer
        interval: root.duration
        onTriggered: root.shown = false
    }

    Rectangle {
        id: card
        width: label.implicitWidth + glyph.width + 42
        height: 44
        radius: Theme.radius
        color: Theme.toastBg
        border.width: 1
        border.color: root.isError ? Theme.alpha(Theme.danger, 0.45) : Theme.lineHard

        Icon {
            id: glyph
            anchors.left: parent.left
            anchors.leftMargin: 14
            anchors.verticalCenter: parent.verticalCenter
            name: root.isError ? "info" : "check"
            size: 17
            color: root.isError ? Theme.danger : Theme.positive
        }

        Text {
            id: label
            anchors.left: glyph.right
            anchors.leftMargin: 11
            anchors.verticalCenter: parent.verticalCenter
            text: root.message
            color: Theme.text
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzBody
        }

        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: root.dismiss()
        }
    }
}
