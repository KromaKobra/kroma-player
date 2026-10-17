import QtQuick
import QtQuick.Controls
import Nocturne
import "../controls"

// The same collection sorts are available from grid menus and list headers.
NocturneMenu {
    id: root
    required property string viewKey
    objectName: viewKey + "SortMenu"
    transformOrigin: Item.TopRight

    function openFor(anchor) {
        popup(anchor, anchor.width - width, anchor.height + Theme.gapXs)
    }

    ButtonGroup { id: directions }
    MenuRow { text: "Sort by"; enabled: false }
    MenuRow {
        objectName: root.viewKey + "Sort_trackCount"
        text: "Most tracks"
        iconName: Theme.prefs[root.viewKey + "Sort"] === "trackCount" ? "check" : ""
        onTriggered: Theme.prefs[root.viewKey + "Sort"] = "trackCount"
    }
    MenuRow {
        objectName: root.viewKey + "Sort_name"
        text: "Name"
        iconName: Theme.prefs[root.viewKey + "Sort"] === "name" ? "check" : ""
        onTriggered: Theme.prefs[root.viewKey + "Sort"] = "name"
    }
    MenuRow {
        objectName: root.viewKey + "Sort_dateAdded"
        text: "Date added"
        iconName: Theme.prefs[root.viewKey + "Sort"] === "dateAdded" ? "check" : ""
        onTriggered: Theme.prefs[root.viewKey + "Sort"] = "dateAdded"
    }
    MenuRow {
        objectName: root.viewKey + "Sort_recentlyModified"
        text: "Recently modified"
        visible: root.viewKey === "playlist"; height: visible ? implicitHeight : 0
        iconName: Theme.prefs[root.viewKey + "Sort"] === "recentlyModified" ? "check" : ""
        onTriggered: {
            Theme.prefs[root.viewKey + "SortAsc"] = false
            Theme.prefs[root.viewKey + "Sort"] = "recentlyModified"
        }
    }
    MenuRow {
        objectName: root.viewKey + "Sort_totalDuration"
        text: "Total time"
        iconName: Theme.prefs[root.viewKey + "Sort"] === "totalDuration" ? "check" : ""
        onTriggered: Theme.prefs[root.viewKey + "Sort"] = "totalDuration"
    }
    MenuRow {
        objectName: root.viewKey + "Sort_primaryArtists"
        text: "Primary artists"
        visible: root.viewKey === "album"; height: visible ? implicitHeight : 0
        iconName: Theme.prefs[root.viewKey + "Sort"] === "primaryArtists" ? "check" : ""
        onTriggered: Theme.prefs[root.viewKey + "Sort"] = "primaryArtists"
    }
    MenuRow {
        objectName: root.viewKey + "Sort_isComplete"
        text: "Complete albums"
        visible: root.viewKey === "album"; height: visible ? implicitHeight : 0
        iconName: Theme.prefs[root.viewKey + "Sort"] === "isComplete" ? "check" : ""
        onTriggered: Theme.prefs[root.viewKey + "Sort"] = "isComplete"
    }
    MenuRow {
        objectName: root.viewKey + "Sort_releaseDate"
        text: "Release date"
        visible: root.viewKey === "album"; height: visible ? implicitHeight : 0
        iconName: Theme.prefs[root.viewKey + "Sort"] === "releaseDate" ? "check" : ""
        onTriggered: Theme.prefs[root.viewKey + "Sort"] = "releaseDate"
    }
    MenuSeparator {}
    MenuRow {
        objectName: root.viewKey + "SortAscending"
        ButtonGroup.group: directions
        text: "Ascending"
        checkable: true
        checked: Theme.prefs[root.viewKey + "SortAsc"]
        iconName: checked ? "check" : ""
        onTriggered: Theme.prefs[root.viewKey + "SortAsc"] = true
    }
    MenuRow {
        objectName: root.viewKey + "SortDescending"
        ButtonGroup.group: directions
        text: "Descending"
        checkable: true
        checked: !Theme.prefs[root.viewKey + "SortAsc"]
        iconName: checked ? "check" : ""
        onTriggered: Theme.prefs[root.viewKey + "SortAsc"] = false
    }
}
