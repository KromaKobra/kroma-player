import QtQuick
import QtQuick.Controls
import QtQuick.Window
import Nocturne

// The native drag surface sits behind real controls, also over the sidebars.
Item {
    id: root
    property var targetWindow: null
    property real sidebarWidth: 240
    property bool canGoBack: false
    property bool canGoForward: false
    property var historyEntries: []
    property int historyCursor: -1
    property alias searchText: search.text
    property string searchPlaceholder: "Search music…"
    signal backRequested()
    signal forwardRequested()
    signal historyRequested(int index)
    signal historyOpened()
    signal searchEdited(string text)
    signal searchAccepted(string text)
    signal searchDismissed()
    implicitHeight: Theme.titleBarHeight

    function focusSearch() { search.forceFocus(); search.selectAll() }
    function chooseHistory(index) {
        // Closing releases the lazy list model and destroys its delegates.
        // Dispatch from the toolbar's lifetime, rather than from a delegate.
        history.close()
        root.historyRequested(index)
    }
    Keys.onEscapePressed: root.searchDismissed()
    function toggleMaximised() {
        if (!targetWindow || targetWindow.visibility === Window.FullScreen) return
        if (targetWindow.visibility === Window.Maximized) targetWindow.showNormal()
        else targetWindow.showMaximized()
    }

    MouseArea {
        objectName: "titleBarDragRegion"
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
        onPressed: function (mouse) {
            if (mouse.button === Qt.LeftButton && root.targetWindow)
                root.targetWindow.startSystemMove()
        }
        onDoubleClicked: function (mouse) {
            if (mouse.button === Qt.LeftButton) root.toggleMaximised()
        }
    }

    Row {
        x: 22
        anchors.verticalCenter: parent.verticalCenter
        spacing: 10
        Image {
            anchors.verticalCenter: parent.verticalCenter
            width: 20; height: 20
            source: "../assets/icon.png"
            sourceSize: Qt.size(40, 40)
            fillMode: Image.PreserveAspectFit
            smooth: true
        }
        Text {
            visible: root.sidebarWidth >= 180
            anchors.verticalCenter: parent.verticalCenter
            text: "Kroma Player"
            color: Theme.text
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzBody
            font.weight: Font.DemiBold
        }
    }

    // Buttons provide keyboard activation and accessible names. Feedback is
    // immediate on press; there is no timer or animation on the input path.
    component ChromeButton: Button {
        id: button
        property string glyph: ""
        property string hint: Accessible.name
        property bool closing: false
        implicitWidth: 36; implicitHeight: 36
        padding: 0
        hoverEnabled: true
        opacity: enabled ? 1 : 0.35
        Accessible.name: text
        background: Rectangle {
            radius: 6
            color: button.closing && (button.hovered || button.down) ? Theme.closeHover
                : button.down ? Theme.alpha(Theme.text, 0.14)
                : button.hovered ? Theme.alpha(Theme.text, 0.07) : "transparent"
            border.width: button.visualFocus ? 1 : 0
            border.color: Theme.accent
        }
        contentItem: Item {
            Icon {
                anchors.centerIn: parent
                name: button.glyph; size: 17
                color: button.closing && (button.hovered || button.down)
                    ? Theme.closeHoverInk : Theme.dim
            }
        }
        NocturneTooltip { text: button.hint; visible: button.hovered }
    }

    Row {
        id: navigation
        x: root.sidebarWidth + 16
        anchors.verticalCenter: parent.verticalCenter
        spacing: 4
        ChromeButton {
            objectName: "navigationHistoryButton"
            text: "Navigation history"; glyph: "clock"
            onClicked: {
                if (history.opened) history.close()
                else { root.historyOpened(); history.open() }
            }
        }
        ChromeButton {
            objectName: "navigationBackButton"
            text: "Back"; hint: "Back (Alt+Left)"; glyph: "chevronLeft"
            enabled: root.canGoBack
            onClicked: root.backRequested()
        }
        ChromeButton {
            objectName: "navigationForwardButton"
            text: "Forward"; hint: "Forward (Alt+Right)"; glyph: "chevronRight"
            enabled: root.canGoForward
            onClicked: root.forwardRequested()
        }
    }

    NocturneField {
        id: search
        objectName: "globalSearchField"
        x: navigation.x + navigation.width + 20
        anchors.verticalCenter: parent.verticalCenter
        // Reserve a useful native drag strip even at the minimum width.
        width: Math.max(120, Math.min(480, controls.x - x - 64))
        height: 36
        iconName: "search"
        accessibleLabel: "Search music"
        placeholder: root.searchPlaceholder
        onEdited: function (text) { root.searchEdited(text) }
        onAccepted: function (text) { root.searchAccepted(text) }
    }

    Row {
        id: controls
        anchors.right: parent.right
        anchors.rightMargin: 8
        anchors.verticalCenter: parent.verticalCenter
        ChromeButton {
            objectName: "windowMinimizeButton"
            text: "Minimize"; glyph: "winMin"
            width: 46; height: 40
            onClicked: if (root.targetWindow) root.targetWindow.showMinimized()
        }
        ChromeButton {
            objectName: "windowMaximizeButton"
            text: root.targetWindow && root.targetWindow.visibility === Window.Maximized
                ? "Restore" : "Maximize"
            glyph: text === "Restore" ? "winRestore" : "winMax"
            width: 46; height: 40
            onClicked: root.toggleMaximised()
        }
        ChromeButton {
            objectName: "windowCloseButton"
            text: "Close"; glyph: "winClose"; closing: true
            width: 46; height: 40
            onClicked: if (root.targetWindow) root.targetWindow.close()
        }
    }

    Popup {
        id: history
        objectName: "navigationHistoryPopup"
        parent: root
        x: navigation.x
        y: root.height - 4
        width: Math.min(340, root.width - x - 12)
        height: Math.min(360, historyList.contentHeight + 16)
        padding: 8
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle {
            color: Theme.dialogBg; radius: Theme.radius
            border.width: 1; border.color: Theme.lineHard
        }
        contentItem: ListView {
            id: historyList
            clip: true
            model: history.visible ? root.historyEntries.slice().reverse() : []
            ScrollBar.vertical: ScrollBar { width: 6 }
            delegate: ItemDelegate {
                id: entry
                required property var modelData
                required property int index
                readonly property int routeIndex: root.historyEntries.length - 1 - index
                objectName: "navigationHistoryEntry_" + routeIndex
                width: ListView.view.width; height: 40
                text: modelData.label
                highlighted: ListView.isCurrentItem
                Accessible.name: text + (routeIndex === root.historyCursor ? ", current view" : "")
                onClicked: root.chooseHistory(routeIndex)
                background: Rectangle {
                    radius: Theme.radiusSm
                    color: entry.down ? Theme.accentSoft
                        : entry.highlighted || entry.hovered ? Theme.raised : "transparent"
                    border.width: entry.visualFocus ? 1 : 0
                    border.color: Theme.accent
                }
                contentItem: Text {
                    text: (entry.routeIndex === root.historyCursor ? "✓  " : "    ") + entry.text
                    color: entry.routeIndex === root.historyCursor ? Theme.accent : Theme.text
                    font.family: Theme.fontFamily; font.pixelSize: Theme.fzBody
                    verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                }
            }
            Keys.onReturnPressed: if (currentItem) currentItem.clicked()
            Keys.onEnterPressed: if (currentItem) currentItem.clicked()
            Keys.onSpacePressed: if (currentItem) currentItem.clicked()
        }
        onOpened: { historyList.currentIndex = 0; historyList.forceActiveFocus() }
    }
}
