import QtQuick
import QtQuick.Controls
import Nocturne
import "../controls"

// Owners declare their existing actions normally. Append display controls once
// after them so all collections share the same menu hierarchy and behavior.
NocturneMenu {
    id: root
    required property string viewKey
    property real viewportWidth: 0
    readonly property bool gridMode: Theme.prefs[viewKey + "Grid"]
    // Whether anything the owner declared is showing above the display
    // controls. Off, the separator between the two goes too, rather than
    // opening the menu on a rule with nothing above it.
    property bool leadingSeparator: true
    transformOrigin: Item.TopRight

    function openFor(anchor) {
        popup(anchor, anchor.width - width, anchor.height + Theme.gapXs)
    }

    property Item displaySeparator: MenuSeparator {
        visible: root.leadingSeparator
        height: visible ? implicitHeight : 0
        padding: 0; topPadding: 4; bottomPadding: 4
        contentItem: Rectangle { implicitHeight: 1; color: Theme.line }
    }
    property MenuItem layoutOption: MenuRow {
        objectName: root.viewKey + "LayoutOption"
        text: root.gridMode ? "Switch to list view" : "Switch to grid view"
        iconName: root.gridMode ? "navPlaylist" : "library"
        onTriggered: Theme.prefs[root.viewKey + "Grid"] = !root.gridMode
    }
    property MenuItem compactOption: MenuRow {
        objectName: root.viewKey + "CompactOption"
        text: "Compact mode"
        visible: !root.gridMode
        height: visible ? implicitHeight : 0
        checkable: true
        checked: Theme.prefs[root.viewKey + "Compact"]
        iconName: checked ? "check" : ""
        onTriggered: Theme.prefs[root.viewKey + "Compact"] = !Theme.prefs[root.viewKey + "Compact"]
    }
    property Item sizeOption: Item {
        visible: root.gridMode
        implicitWidth: 232
        implicitHeight: 66
        height: visible ? implicitHeight : 0
        Text {
            x: 13; y: 6
            text: "Tile size"
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }
        TileSizeSlider {
            objectName: root.viewKey + "TileSlider"
            x: 13; y: 27
            sliderWidth: 162
            viewportWidth: root.viewportWidth
            tileSize: Math.max(120, Math.min(300, Theme.prefs[root.viewKey + "TileSize"]))
            visible: true
            onSizeRequested: function(size) { Theme.prefs[root.viewKey + "TileSize"] = size }
        }
    }
    Component.onCompleted: {
        addItem(displaySeparator)
        addItem(layoutOption)
        addItem(compactOption)
        addItem(sizeOption)
    }
}
