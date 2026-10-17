import QtQuick
import QtQuick.Controls
import Nocturne
import "../artwork"
import "../controls"

// Presentation belongs here; navigation and persistence belong to the window.
// A page opened inside a tab — an artist, an album,
// an editor, a playlist — lights that tab rather than adding an entry of its
// own; the window says which tab is current through `currentIndex`.
Item {
    id: root
    property int currentIndex: 0
    property int libraryMode: 0
    property real expandedWidth: 240
    property bool collapsed: false
    property real maximumWidth: 360
    property bool reducedMotion: false
    property bool drawSurface: true
    property Item headerHost: null
    readonly property Item headerToggle: toggleButton
    property bool headerHovered: false
    property var pinModel: null
    readonly property var pinnedItems: pinModel ? pinModel.items : []
    property string activePinType: ""
    property int activePinId: -1
    readonly property bool hovered: sidebarHover.hovered

    readonly property real minimumExpandedWidth: 200
    readonly property real collapsedWidth: 64
    readonly property real collapseThreshold: 156
    readonly property real labelOpacity: Math.max(0, Math.min(1, (width - 100) / 100))
    // Follow the same live geometry as the destination icons and labels,
    // including held threshold previews, reversals, and cancelled gestures.
    readonly property real toggleRowProgress: headerHost ? 1 - labelOpacity : 1
    readonly property bool resizing: resizeHandle.pressed
    // Under the pointer or springing to rest: the edge is moving either way.
    readonly property bool moving: resizeHandle.dragging || resizeHandle.animating
    readonly property real effectiveMaximum: resizeHandle.effectiveMaximum
    readonly property real targetWidth: resizeHandle.targetWidth

    signal navigated(int index)
    signal libraryNavigated(int mode)
    signal pinActivated(string contentType, int contentId)

    readonly property var mainItems: [
        // { view: 13, icon: "navHome", label: "Home" },
        { view: 0, icon: "navPlaying", label: "Now Playing" },
        { view: 1, icon: "navQueue", label: "Queue" },
        { view: 2, icon: "navSearch", label: "Search" },
        { view: 4, mode: 0, icon: "navSong", label: "Songs", group: "Library" },
        { view: 4, mode: 2, icon: "navAlbum", label: "Albums" },
        { view: 4, mode: 1, icon: "navArtist", label: "Artists" },
        { view: 3, icon: "navPlaylist", label: "Playlists" },
        { view: 8, icon: "navImport", label: "Import", group: "Tools" }
    ]
    readonly property var settingsItem: ({ view: 5, icon: "navSettings", label: "Settings" })
    readonly property var items: mainItems.concat([settingsItem])
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

    SidebarSurface { anchors.fill: parent; visible: root.drawSurface }
    HoverHandler { id: sidebarHover }
    Rectangle {
        visible: root.drawSurface
        anchors.right: parent.right
        height: parent.height; width: 1
        color: Theme.prefs.ambient ? Theme.line : Theme.lineSoft
    }
    Button {
        id: toggleButton
        objectName: "sidebarToggle"
        // One control retains focus and identity in both presentations. Its
        // visual parent is the toolbar, while this rail owns its behaviour.
        parent: root.headerHost || root
        x: root.headerHost ? Math.max(12, root.width - width - 12) : 12
        readonly property real headerY: root.headerHost
            ? (root.headerHost.height - height) / 2 : 8
        y: !root.headerHost ? 8 : headerY
            + (root.headerHost.height + 8 - headerY) * root.toggleRowProgress
        width: 40; height: 36
        focusPolicy: Qt.TabFocus
        opacity: root.toggleRowProgress > 0 || root.resizing || root.hovered || root.headerHovered
            || hovered || activeFocus ? 1 : 0
        Behavior on opacity {
            NumberAnimation { duration: root.reducedMotion ? 0 : Theme.fast }
        }
        Accessible.name: resizeHandle.previewCollapsed ? "Expand sidebar" : "Collapse sidebar"
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
    // Preserve wheel-to-destination navigation, with trackpad throttling.
    WheelSteps {
        anchors.fill: parent
        enabled: scroll.contentHeight <= scroll.height
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
    component PinnedDestination: Button {
        id: pin
        required property var entry
        objectName: "pinned_" + entry.contentType + "_" + entry.contentId
        x: 12; width: root.width - 24; height: 48
        padding: 0
        readonly property bool selected: root.activePinType === entry.contentType
            && root.activePinId === entry.contentId
        Accessible.name: entry.title + ", " + entry.subtitle
        Accessible.checkable: true
        Accessible.checked: selected
        onClicked: root.pinActivated(entry.contentType, entry.contentId)
        onActiveFocusChanged: if (activeFocus) {
            var position = mapToItem(navigation, 0, 0).y
            if (position < scroll.contentY) scroll.contentY = position
            else if (position + height > scroll.contentY + scroll.height)
                scroll.contentY = Math.max(0, position + height - scroll.height)
        }
        function openMenu() {
            pinMenu.contentType = entry.contentType
            pinMenu.contentId = entry.contentId
            pinMenu.popup(pin, 0, height)
        }
        Keys.onMenuPressed: openMenu()
        Keys.onPressed: function(event) {
            if (event.key === Qt.Key_F10 && (event.modifiers & Qt.ShiftModifier)) {
                openMenu()
                event.accepted = true
            }
        }
        TapHandler {
            acceptedButtons: Qt.RightButton
            onTapped: function(eventPoint) {
                pinMenu.contentType = pin.entry.contentType
                pinMenu.contentId = pin.entry.contentId
                pinMenu.popup(pin, eventPoint.position)
            }
        }
        background: Rectangle {
            radius: 8
            color: pin.down ? Theme.alpha(Theme.text, 0.13)
                : pin.selected ? Theme.alpha(Theme.text, 0.09)
                : pin.hovered ? Theme.alpha(Theme.text, 0.05) : "transparent"
            border.width: pin.visualFocus ? 1 : 0
            border.color: Theme.accent
        }
        contentItem: Item {
            clip: true
            CoverArt {
                id: artwork
                x: 4; anchors.verticalCenter: parent.verticalCenter
                width: 32; height: 32
                hash: pin.entry.coverHash
                radius: pin.entry.contentType === "artist" ? 16 : 5
                maskSource: pin.entry.contentType === "artist" ? roundPinMask : pinMask
                showPlaceholderGlyph: false
                Icon {
                    anchors.centerIn: parent
                    visible: !artwork.ready
                    name: ({track: "navSong", album: "navAlbum", artist: "navArtist",
                            playlist: "navPlaylist"})[pin.entry.contentType]
                    size: 18; color: Theme.dim
                }
            }
            Column {
                x: 48; width: Math.max(0, parent.width - x - 8)
                anchors.verticalCenter: parent.verticalCenter
                opacity: root.labelOpacity
                spacing: 2
                Text {
                    width: parent.width
                    text: pin.entry.title; elide: Text.ElideRight
                    font.family: Theme.fontFamily; font.pixelSize: Theme.fzBody
                    font.weight: Font.Medium; color: Theme.text
                }
                Text {
                    width: parent.width
                    text: pin.entry.subtitle; elide: Text.ElideRight
                    font.family: Theme.fontFamily; font.pixelSize: Theme.fzSmall
                    color: Theme.dim
                }
            }
        }
        NocturneTooltip {
            text: pin.entry.title + "\n" + pin.entry.subtitle
            visible: pin.hovered
            x: pin.width + 8
        }
    }
    NocturneMenu {
        id: pinMenu
        objectName: "sidebarPinMenu"
        property string contentType: ""
        property int contentId: -1
        MenuRow {
            objectName: "unpinFromSidebar"
            text: "Unpin from sidebar"; iconName: "pin"
            onTriggered: if (root.pinModel) root.pinModel.unpin(pinMenu.contentType, pinMenu.contentId)
        }
    }
    CornerMask { id: pinMask; width: 32; height: 32; radius: 5 }
    CornerMask { id: roundPinMask; width: 32; height: 32; radius: 16 }
    Flickable {
        id: scroll
        anchors.top: parent.top
        anchors.topMargin: 8 + 44 * root.toggleRowProgress
        anchors.bottom: footer.top; anchors.bottomMargin: 8
        width: parent.width; contentHeight: navigation.height
        clip: true; interactive: false; boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: EdgeScrollBar {}
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
            Heading {
                objectName: "pinnedHeading"
                text: "PINNED"
                visible: root.pinnedItems.length > 0
            }
            Repeater {
                model: root.pinnedItems
                delegate: PinnedDestination {
                    required property var modelData
                    entry: modelData
                }
            }
        }
        onContentHeightChanged: contentY = Math.max(0, Math.min(contentY, contentHeight - height))
    }
    SmoothWheel { target: scroll; enabled: scroll.contentHeight > scroll.height }
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
