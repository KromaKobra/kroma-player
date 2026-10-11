import QtQuick
import QtQuick.Controls
import Nocturne

// Presentation belongs here; navigation and persistence belong to the window.
Item {
    id: root
    property int currentIndex: 0
    property int libraryMode: 0
    property real expandedWidth: 240
    property bool collapsed: false
    property real maximumWidth: 360
    property bool reducedMotion: false
    property bool showEdit: false
    property bool showArtist: false
    property bool showAlbum: false
    property string artistLabel: "Artist"
    property string albumLabel: "Album"
    property int artistView: 9
    property int albumView: 11

    readonly property real minimumExpandedWidth: 200
    readonly property real collapsedWidth: 64
    readonly property real collapseThreshold: 156
    readonly property real labelOpacity: Math.max(0, Math.min(1, (width - 100) / 100))
    readonly property bool resizing: resizeHandle.pressed
    readonly property real effectiveMaximum: resizeHandle.effectiveMaximum
    readonly property real targetWidth: resizeHandle.targetWidth

    signal navigated(int index)
    signal libraryNavigated(int mode)

    readonly property var mainItems: [
        { view: 13, icon: "navHome", label: "Home" },
        { view: 0, icon: "navPlaying", label: "Now Playing" },
        { view: 1, icon: "navQueue", label: "Queue" },
        { view: 2, icon: "navSearch", label: "Search" },
        { view: 4, mode: 0, icon: "navSong", label: "Songs", group: "Library" },
        { view: 4, mode: 2, icon: "navAlbum", label: "Albums" },
        { view: 4, mode: 1, icon: "navArtist", label: "Artists" },
        { view: 3, icon: "navPlaylist", label: "Playlists" },
        { view: 8, icon: "navImport", label: "Import", group: "Tools" }
    ]
    readonly property var openItems: []
        .concat(showArtist ? [{ view: artistView, icon: "navArtist", label: artistLabel }] : [])
        .concat(showAlbum ? [{ view: albumView, icon: "navAlbum", label: albumLabel }] : [])
        .concat(showEdit ? [{ view: 7, icon: "edit", label: "Edit track" }] : [])
    readonly property var settingsItem: ({ view: 5, icon: "navSettings", label: "Settings" })
    readonly property var items: mainItems.concat(openItems).concat([settingsItem])
    readonly property int currentSlot: {
        for (var i = 0; i < items.length; ++i)
            if (isSelected(items[i])) return i
        return -1
    }
    function isSelected(item) {
        return currentIndex === item.view
            && (item.mode === undefined || libraryMode === item.mode)
    }
    function activate(item) {
        if (item.mode !== undefined) libraryNavigated(item.mode)
        else navigated(item.view)
    }
    function toggle() { collapsed = !collapsed }

    readonly property real displayedWidth: resizeHandle.displayedWidth
    width: displayedWidth
    implicitWidth: targetWidth

    SidebarSurface { anchors.fill: parent }
    Rectangle {
        anchors.right: parent.right
        height: parent.height; width: 1
        color: Theme.prefs.ambient ? Theme.line : Theme.lineSoft
    }
    Button {
        id: toggleButton
        objectName: "sidebarToggle"
        x: 12; y: 8; width: 40; height: 36
        Accessible.name: root.collapsed ? "Expand sidebar" : "Collapse sidebar"
        onClicked: root.toggle()
        background: Rectangle {
            radius: 8
            color: toggleButton.down ? Theme.alpha(Theme.text, 0.12)
                : toggleButton.hovered ? Theme.alpha(Theme.text, 0.06) : "transparent"
            border.width: toggleButton.visualFocus ? 1 : 0
            border.color: Theme.accent
        }
        contentItem: Item {
            Icon { anchors.centerIn: parent; name: "navSidebar"; size: 18; color: Theme.dim }
        }
        NocturneTooltip {
            text: toggleButton.Accessible.name + " (Ctrl+B)"
            visible: toggleButton.hovered
        }
    }
    Text {
        x: 60; y: 8; height: 36
        width: Math.max(0, root.width - x - 12)
        verticalAlignment: Text.AlignVCenter
        text: "Navigation"
        font.family: Theme.fontFamily; font.pixelSize: Theme.fzSmall
        font.weight: Font.Medium; color: Theme.faint
        opacity: root.labelOpacity; elide: Text.ElideRight
    }
    // Preserve wheel-to-destination navigation, with trackpad throttling.
    WheelSteps {
        anchors.fill: parent
        onStepped: function (delta) {
            if (root.currentSlot < 0) return
            var next = Math.max(0, Math.min(root.items.length - 1, root.currentSlot + delta))
            if (next !== root.currentSlot) root.activate(root.items[next])
        }
    }
    component Heading: Item {
        property string text
        width: navigation.width
        height: 44 - 28 * (1 - root.labelOpacity)
        clip: true
        Text {
            x: 24; anchors.bottom: parent.bottom; anchors.bottomMargin: 8
            text: parent.text; color: Theme.faint
            font.family: Theme.fontFamily; font.pixelSize: Theme.fzSmall
            font.weight: Font.DemiBold; font.letterSpacing: 0.15
            opacity: root.labelOpacity
        }
    }
    component Destination: Button {
        id: destination
        required property var entry
        objectName: "nav_" + entry.label
        x: 12; width: root.width - 24; height: 36
        padding: 0
        readonly property bool selected: root.isSelected(entry)
        Accessible.name: entry.label
        Accessible.checkable: true
        Accessible.checked: selected
        onClicked: root.activate(entry)
        onActiveFocusChanged: if (activeFocus) {
            reveal()
        }
        onSelectedChanged: if (selected) Qt.callLater(reveal)
        function reveal() {
            if (entry.view === 5) return
            var position = mapToItem(navigation, 0, 0).y
            if (position >= 0 && position < navigation.height) {
                if (position < scroll.contentY) scroll.contentY = position
                else if (position + height > scroll.contentY + scroll.height)
                    scroll.contentY = Math.max(0, Math.min(scroll.contentHeight - scroll.height,
                                              position + height - scroll.height))
            }
        }
        background: Rectangle {
            radius: 8
            color: destination.down ? Theme.alpha(Theme.text, 0.13)
                : destination.selected ? Theme.alpha(Theme.text, 0.09)
                : destination.hovered ? Theme.alpha(Theme.text, 0.05) : "transparent"
            border.width: destination.visualFocus ? 1 : 0
            border.color: Theme.accent
        }
        contentItem: Item {
            clip: true
            Icon {
                x: 11; anchors.verticalCenter: parent.verticalCenter
                name: destination.entry.icon; size: 18; strokeWidth: 1.75
                color: destination.selected ? Theme.accent : Theme.dim
            }
            Text {
                x: 40; width: Math.max(0, parent.width - x - 10)
                anchors.verticalCenter: parent.verticalCenter
                text: destination.entry.label; elide: Text.ElideRight
                font.family: Theme.fontFamily; font.pixelSize: Theme.fzBody
                font.weight: Font.Medium
                color: destination.selected || destination.hovered ? Theme.text : Theme.dim
                opacity: root.labelOpacity
            }
        }
        NocturneTooltip {
            text: destination.entry.label
            visible: destination.hovered && root.labelOpacity < 1
            x: destination.width + 8
        }
    }
    Flickable {
        id: scroll
        anchors.top: toggleButton.bottom; anchors.topMargin: 8
        anchors.bottom: footer.top; anchors.bottomMargin: 8
        width: parent.width; contentHeight: navigation.height
        clip: true; interactive: false; boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
        Column {
            id: navigation
            width: scroll.width; spacing: 1
            Repeater {
                model: root.mainItems
                delegate: Column {
                    required property var modelData
                    width: navigation.width
                    Heading { text: modelData.group || ""; visible: text !== "" }
                    Destination { entry: modelData }
                }
            }
            Heading { text: "Open pages"; visible: root.openItems.length > 0 }
            Repeater {
                model: root.openItems
                delegate: Destination { required property var modelData; entry: modelData }
            }
            Heading { text: "Pinned" }
        }
    }
    Item {
        id: footer
        anchors.bottom: parent.bottom; width: parent.width; height: 57
        Rectangle { width: parent.width; height: 1; color: Theme.lineSoft }
        Destination { y: 10; entry: root.settingsItem }
    }
    SidebarResizeHandle {
        id: resizeHandle
        objectName: "sidebarResizeHandle"
        x: root.width - width / 2
        height: parent.height
        expandedWidth: root.expandedWidth
        collapsed: root.collapsed
        collapsedWidth: root.collapsedWidth
        minimumExpandedWidth: root.minimumExpandedWidth
        maximumWidth: root.maximumWidth
        collapseThreshold: root.collapseThreshold
        reducedMotion: root.reducedMotion
        onResizeCommitted: function (preferredWidth, collapse) {
            root.expandedWidth = preferredWidth
            root.collapsed = collapse
        }
    }
}
