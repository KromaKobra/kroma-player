import QtQuick
import QtQuick.Controls
import Nocturne

// The parent owns selection and persistence. This navigation adapts to the
// space left by the app's sidebars without hiding any category.
Item {
    id: root
    property var tabs: []
    property var descriptions: []
    property var icons: ["settings", "volumeHigh", "mic", "library", "sparkle", "download", "info"]
    property int currentIndex: 0
    property bool compact: false
    signal selected(int index)
    implicitHeight: compact ? 46 : column.implicitHeight
    function ensureCurrentVisible() {
        var item = compact ? horizontalTabs.itemAt(currentIndex) : verticalTabs.itemAt(currentIndex)
        if (!item) return
        if (compact) flick.contentX = Math.max(0, Math.min(item.x, flick.contentWidth - flick.width))
        else flick.contentY = Math.max(0, Math.min(item.y, flick.contentHeight - flick.height))
    }
    onCurrentIndexChanged: Qt.callLater(ensureCurrentVisible)
    onCompactChanged: Qt.callLater(ensureCurrentVisible)
    Flickable {
        id: flick
        anchors.fill: parent
        contentWidth: root.compact ? row.implicitWidth : width
        contentHeight: root.compact ? height : column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        Column {
            id: column
            width: parent.width
            spacing: 5
            visible: !root.compact
            Repeater {
                id: verticalTabs
                model: root.compact ? [] : root.tabs
                delegate: navItem
            }
        }
        Row {
            id: row
            spacing: 5
            visible: root.compact
            Repeater {
                id: horizontalTabs
                model: root.compact ? root.tabs : []
                delegate: navItem
            }
        }
    }
    WheelSteps {
        anchors.fill: parent
        onStepped: function(delta) {
            var next = Math.max(0, Math.min(root.tabs.length - 1, root.currentIndex + delta))
            if (next !== root.currentIndex) root.selected(next)
        }
    }
    Component {
        id: navItem
        Button {
            id: button
            required property int index
            required property var modelData
            width: root.compact ? title.implicitWidth + 30 : root.width
            height: root.compact ? 40 : 64
            Accessible.name: modelData
            Accessible.role: Accessible.PageTab
            Accessible.selected: root.currentIndex === index
            onClicked: root.selected(index)
            background: Rectangle {
                radius: Theme.radius
                color: root.currentIndex === button.index ? Theme.accentSoft
                       : button.down ? Theme.raised : button.hovered ? Theme.alpha(Theme.text, 0.04) : "transparent"
                border.color: button.visualFocus ? Theme.accent : "transparent"
            }
            contentItem: Item {
                Icon {
                    visible: !root.compact
                    x: 8
                    anchors.verticalCenter: parent.verticalCenter
                    name: root.icons[button.index] || "settings"
                    size: 18
                    color: root.currentIndex === button.index ? Theme.accent : Theme.dim
                }
                Column {
                    x: root.compact ? 8 : 36
                    anchors.verticalCenter: parent.verticalCenter
                    width: parent.width - x - 6
                    spacing: 4
                    Text {
                        id: title
                        text: button.modelData
                        color: root.currentIndex === button.index ? Theme.text : Theme.dim
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzBody
                        font.weight: Font.DemiBold
                    }
                    Text {
                        width: parent.width
                        visible: !root.compact
                        text: root.descriptions[button.index] || ""
                        color: Theme.dim
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzTiny
                        elide: Text.ElideRight
                    }
                }
            }
        }
    }
}
