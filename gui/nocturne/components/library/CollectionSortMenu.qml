import QtQuick
import QtQuick.Controls
import Nocturne
import "../controls"

// Collections have no sortable columns in either their grid or list layout.
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
