import QtQuick
import Nocturne

// Underline tabs for a page's sub-sections.
//
// Follows NavRail's convention rather than Pill's: this never assigns to its
// own `currentIndex`. It draws the index it is handed and emits `selected`,
// leaving the page that owns the index as the only thing that can change it.
// Pill does the opposite, which is exactly why the settings pills need pushing
// back into sync by hand every time the backend disagrees with them — a tab bar
// restored from preferences would have the same problem.
Item {
    id: root

    property var tabs: []
    property int currentIndex: 0

    // Breathing room either side of a label. Also the underline's overhang:
    // it runs half of this past the text at each end, so it reads as belonging
    // to the word rather than to the whole hit target.
    property int itemPadding: 15
    property int sideMargin: Theme.gapXl

    signal selected(int index)

    implicitHeight: 42

    // The delegate for the current tab, or null before the Repeater has built
    // them. Everything about the underline comes off this: the Row has already
    // worked out where each label sits, so there is nothing to recompute here
    // and nothing to drift out of step when a label's width settles late.
    readonly property Item currentTab:
        currentIndex >= 0 && currentIndex < row.children.length
        ? row.children[currentIndex] : null

    // Scrolling over the strip moves one tab at a time, down or right for the
    // next. Raised as selected() rather than written here, for the same reason
    // the click is: the index belongs to the page, and coming back around
    // through it is what keeps the preference write and the glide on one path.
    WheelSteps {
        anchors.fill: parent
        onStepped: function (delta) {
            var next = Math.max(
                0, Math.min(root.tabs.length - 1, root.currentIndex + delta))
            if (next !== root.currentIndex)
                root.selected(next)
        }
    }

    Row {
        id: row
        anchors.left: parent.left
        // Pulled back by the first label's own padding, so the text starts on
        // the same vertical as the page title above it rather than the hit
        // target around it.
        anchors.leftMargin: root.sideMargin - root.itemPadding
        anchors.top: parent.top
        anchors.bottom: rule.top

        Repeater {
            model: root.tabs

            delegate: Item {
                id: tab
                required property int index
                required property var modelData

                width: label.implicitWidth + root.itemPadding * 2
                height: row.height

                Text {
                    id: label
                    anchors.centerIn: parent
                    text: tab.modelData
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzSmall
                    font.weight: root.currentIndex === tab.index
                                 ? Font.DemiBold : Font.Medium
                    color: root.currentIndex === tab.index
                           ? Theme.text
                           : (tabMouse.containsMouse ? Theme.dim : Theme.faint)

                    Behavior on color { ColorAnimation { duration: Theme.fast } }
                }

                MouseArea {
                    id: tabMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.selected(tab.index)
                }
            }
        }
    }

    // The rule runs the full width of the page and the underline is drawn over
    // it, so the accent reads as a piece of the divider lit up rather than as a
    // second bar floating above one.
    Rectangle {
        id: rule
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: Theme.line
    }

    Rectangle {
        anchors.bottom: parent.bottom
        height: 2
        radius: 1
        color: Theme.accent

        x: root.currentTab
           ? row.x + root.currentTab.x + root.itemPadding / 2 : 0
        width: root.currentTab
               ? root.currentTab.width - root.itemPadding : 0
        opacity: width > 0 ? 1 : 0

        Behavior on x {
            NumberAnimation { duration: Theme.normal; easing.type: Theme.easing }
        }
        Behavior on width {
            NumberAnimation { duration: Theme.normal; easing.type: Theme.easing }
        }
    }
}
