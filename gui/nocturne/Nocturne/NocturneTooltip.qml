import QtQuick
import QtQuick.Controls

// Small dark tooltip. Built on Popup so it is never clipped by an ancestor.
ToolTip {
    id: root

    delay: 480
    padding: 0
    margins: 0
    x: Math.round((parent ? parent.width : 0) / 2 - implicitWidth / 2)
    y: (parent ? parent.height : 0) + 8

    enter: Transition {
        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.fast }
    }
    exit: Transition {
        NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Theme.fast }
    }

    background: Rectangle {
        color: Theme.tooltipBg
        radius: Theme.radiusSm
        border.width: 1
        border.color: Theme.lineHard
    }

    contentItem: Text {
        text: root.text
        color: Theme.text
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fzSmall
        leftPadding: 9
        rightPadding: 9
        topPadding: 5
        bottomPadding: 6
    }
}
