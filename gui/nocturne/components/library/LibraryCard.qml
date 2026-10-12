import QtQuick
import QtQuick.Controls
import Nocturne
import "../artwork"
import "../playback"
import "../tracks/TrackColumns.js" as Catalog

// Shared library entry. Role adapters supply data; the owning view supplies actions.
FocusScope {
    id: root
    readonly property var view: GridView.view || ListView.view
    readonly property bool listMode: view ? view.listMode : false
    property var controller: view ? view.controller : null
    property int entityId: -1
    property int rowIndex: -1
    property string displayTitle: ""
    property string subtitle: ""
    property string artworkHash: ""
    property int count: -1
    property real contentTime: 0
    property int releaseDate: 0
    property bool centered: false
    property bool attention: false
    property bool complete: false
    property bool playable: false
    property bool contextActions: true
    property bool placeholderGlyph: true
    property bool selected: controller ? controller.selectedIds.has(entityId) : false
    property bool playing: false
    property bool pooled: false
    property bool mouseFocus: false
    onActiveFocusChanged: if (!activeFocus) mouseFocus = false
    readonly property var columnLayout: controller ? controller.columnLayout : null
    readonly property real leadWidth: controller ? controller.leadWidth : 0
    signal activated()
    signal contextRequested()
    signal playRequested()

    function select(ctrl, shift) {
        if (controller) controller.handleClick(entityId, ctrl, shift)
    }
    function open() {
        if (controller) controller.prepareActivation(entityId)
        activated()
    }
    function menu() {
        if (controller && typeof controller.openContext === "function") controller.openContext(entityId)
        else contextRequested()
    }
    function play() {
        if (controller && typeof controller.playEntity === "function") controller.playEntity(entityId)
        else playRequested()
    }
    function cellText(key) {
        if (key === "trackCount") return String(count)
        if (key === "totalDuration") return Fmt.duration(contentTime)
        if (key === "releaseDate") return Fmt.releaseDate(releaseDate)
        return ""
    }
    width: view ? view.cellWidth : 0
    height: view ? view.cellHeight : 0
    activeFocusOnTab: true
    Accessible.role: Accessible.ListItem
    Accessible.name: displayTitle + ", " + subtitle
    Accessible.selected: selected
    Accessible.description: (count >= 0 ? Fmt.count(count, "track") : "")
                            + (playing ? ", now playing" : "")
    Accessible.onPressAction: select(false, false)
    Keys.onReturnPressed: open()
    Keys.onEnterPressed: open()
    Keys.onSpacePressed: function(event) {
        mouseFocus = false
        select(Boolean(event.modifiers & Qt.ControlModifier), Boolean(event.modifiers & Qt.ShiftModifier))
    }
    Keys.onMenuPressed: if (contextActions) menu()
    ListView.onPooled: pooled = true
    ListView.onReused: pooled = false
    GridView.onPooled: pooled = true
    GridView.onReused: pooled = false

    Rectangle {
        objectName: "entrySelectionBackground"
        anchors.fill: parent
        anchors.margins: root.listMode ? 2 : 3
        radius: Theme.radiusLg
        color: root.selected || mouse.pressed ? Theme.accentSoft
               : root.activeFocus && !root.mouseFocus ? Theme.alpha(Theme.accent, 0.12)
               : entryHover.hovered ? Theme.alpha(Theme.text, 0.06) : "transparent"
    }
    HoverHandler { id: entryHover }
    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: root.contextActions ? Qt.LeftButton | Qt.RightButton : Qt.LeftButton
        onClicked: function(event) {
            root.forceActiveFocus(Qt.MouseFocusReason)
            root.mouseFocus = true
            if (event.button === Qt.RightButton) root.menu()
            else root.select(Boolean(event.modifiers & Qt.ControlModifier), Boolean(event.modifiers & Qt.ShiftModifier))
        }
        onDoubleClicked: function(event) { if (event.button === Qt.LeftButton) root.open() }
    }
    Item {
        id: content
        anchors.fill: parent
        anchors.margins: root.view ? root.view.artworkPadding : 0
        Item {
            width: Catalog.NUM_W
            height: parent.height
            visible: root.listMode
            Text {
                anchors.centerIn: parent
                text: root.rowIndex + 1
                visible: !root.playing
                color: Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzTiny
            }
            EqualizerBars {
                anchors.centerIn: parent
                visible: root.playing
                playing: root.playing && playerVM.is_playing && root.visible && !root.pooled
                maxHeight: 13
            }
        }
        Item {
            id: artwork
            x: root.listMode ? Catalog.NUM_W + Theme.gap : 0
            width: root.view ? root.view.artSize : 0
            height: width
            y: root.listMode ? (parent.height - height) / 2 : 0
            CoverArt {
                anchors.fill: parent
                radius: root.view && root.view.roundArtwork ? width / 2
                        : root.view ? root.view.artworkRadius : Theme.radius
                tier: root.listMode ? 128 : 512
                maskSource: root.view ? root.view.artMask : null
                hash: root.artworkHash
                placeholderText: root.displayTitle
                showPlaceholderGlyph: root.placeholderGlyph
            }
            Loader {
                anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 2
                width: 22; height: 22
                active: root.attention
                sourceComponent: Rectangle {
                    radius: 11; color: Theme.card
                    Icon { anchors.centerIn: parent; name: "warning"; size: 12; color: Theme.accentAlt }
                }
            }
            Rectangle {
                anchors.left: parent.left; anchors.top: parent.top; anchors.margins: 8
                width: 28; height: 26; radius: 7
                visible: !root.listMode && root.playing
                color: Theme.alpha(Theme.card, 0.9)
                EqualizerBars {
                    anchors.centerIn: parent
                    playing: root.playing && playerVM.is_playing && root.visible && !root.pooled
                }
            }
            Row {
                anchors.left: parent.left; anchors.bottom: parent.bottom; anchors.margins: 8
                spacing: 5
                visible: !root.listMode && root.count >= 0 && !(root.view && root.view.roundArtwork)
                Rectangle {
                    objectName: "libraryTrackCountChip"
                    width: countText.implicitWidth + 14; height: 25; radius: 7
                    color: Theme.alpha(Theme.card, 0.88)
                    Text {
                        id: countText
                        anchors.centerIn: parent; text: root.count
                        color: Theme.alpha(Theme.text, 0.85)
                        font.family: Theme.fontFamily; font.pixelSize: Theme.fzTiny
                    }
                }
                CompleteMark { anchors.verticalCenter: parent.verticalCenter; complete: root.complete; visible: root.complete; size: 16 }
            }
            LibraryPlayButton {
                id: playControl
                objectName: "collectionPlayButton"
                width: root.listMode ? artwork.width : 36
                height: width
                anchors.right: parent.right; anchors.bottom: parent.bottom
                anchors.margins: root.listMode ? 0 : 8
                visible: root.playable && (entryHover.hovered || root.activeFocus || hovered || visualFocus || root.playing)
                artworkOverlay: root.listMode
                artworkRadius: root.view && root.view.roundArtwork ? width / 2 : root.view ? root.view.artworkRadius : Theme.radius
                displayTitle: root.displayTitle
                onClicked: root.play()
            }
        }
        Column {
            id: labels
            x: root.listMode ? root.leadWidth : 0
            y: root.listMode ? (parent.height - height) / 2 : artwork.height + 10
            width: root.listMode && root.columnLayout ? root.columnLayout.titleWidth : parent.width - x
            spacing: 3
            Text {
                width: parent.width
                horizontalAlignment: !root.listMode && root.centered ? Text.AlignHCenter : Text.AlignLeft
                objectName: "libraryEntryTitle"
                text: root.displayTitle; elide: Text.ElideRight
                color: root.playing ? Theme.accent : Theme.text
                font.family: Theme.fontFamily; font.pixelSize: Theme.fzBody; font.weight: Font.DemiBold
            }
            Text {
                width: parent.width
                horizontalAlignment: !root.listMode && root.centered ? Text.AlignHCenter : Text.AlignLeft
                objectName: "libraryEntrySubtitle"
                text: root.subtitle; elide: Text.ElideRight
                color: Theme.dim
                font.family: Theme.fontFamily; font.pixelSize: Theme.fzTiny
            }
        }
        Row {
            x: root.leadWidth + (root.columnLayout ? root.columnLayout.titleWidth : 0) + Theme.gap
            height: parent.height
            spacing: Theme.gap
            visible: root.listMode
            Repeater {
                model: root.listMode && root.columnLayout ? root.columnLayout.renderedColumns : []
                delegate: Item {
                    required property var modelData
                    width: root.columnLayout.renderedWidths[modelData.key]
                    height: content.height
                    Text {
                        anchors.fill: parent
                        verticalAlignment: Text.AlignVCenter
                        horizontalAlignment: modelData.align === "right" ? Text.AlignRight : Text.AlignLeft
                        text: root.cellText(modelData.key); elide: Text.ElideRight
                        color: root.playing ? Theme.accent : Theme.dim
                        font.family: Theme.fontFamily; font.pixelSize: Theme.fzTiny
                        font.features: modelData.numeric ? ({"tnum": 1}) : ({})
                        visible: modelData.key !== "isComplete"
                    }
                    CompleteMark {
                        anchors.centerIn: parent; complete: root.complete; size: 16
                        visible: modelData.key === "isComplete" && root.complete
                    }
                }
            }
        }
    }
    Rectangle {
        anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
        width: 3; height: 26; radius: 2; color: Theme.accent
        visible: root.listMode && root.playing
    }
}
