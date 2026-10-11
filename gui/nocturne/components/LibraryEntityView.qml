import QtQuick
import QtQuick.Controls
import Nocturne

// Artist and album adapters supply their own model, delegate and labels.
Item {
    id: root
    required property string viewKey
    required property var model
    required property Component delegate
    property string headingText: ""
    property string emptyIcon: ""
    property string emptyTitle: ""
    property string emptyDescription: ""
    property bool roundArtwork: false
    property real artworkRadius: Theme.radiusLg
    readonly property int count: collection.count
    readonly property int tileSize: Math.max(120, Math.min(300, Theme.prefs[viewKey + "TileSize"]))
    function openOptions(anchor) { options.openFor(anchor) }

    // Navigation refreshes the model to pick up library changes. A reset should
    // not move the user away from the rows they were browsing on this page.
    function refresh() {
        var view = collection.activeView
        var offset = view ? view.contentY - view.originY : 0
        root.model.refresh()
        Qt.callLater(function() {
            if (!view || collection.activeView !== view) return
            view.forceLayout()
            view.contentY = view.originY + Math.max(0, Math.min(offset, view.contentHeight - view.height))
        })
    }

    Binding { target: root.model; property: "sortField"; value: Theme.prefs[root.viewKey + "Sort"] }
    Binding { target: root.model; property: "sortAscending"; value: Theme.prefs[root.viewKey + "SortAsc"] }

    Item {
        id: heading
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: Theme.gapXl
        anchors.rightMargin: Theme.gapXl
        height: 32
        SectionLabel { anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; text: root.headingText }
    }
    ButtonGroup { id: directions }
    ViewOptionsMenu {
        id: options
        objectName: root.viewKey + "ViewOptionsMenu"
        viewKey: root.viewKey
        viewportWidth: collection.activeView ? collection.activeView.width : collection.width
        MenuRow { text: "Sort by"; enabled: false }
        MenuRow {
            text: "Most tracks"
            iconName: Theme.prefs[root.viewKey + "Sort"] === "trackCount" ? "check" : ""
            onTriggered: Theme.prefs[root.viewKey + "Sort"] = "trackCount"
        }
        MenuRow {
            text: "Name"
            iconName: Theme.prefs[root.viewKey + "Sort"] === "name" ? "check" : ""
            onTriggered: Theme.prefs[root.viewKey + "Sort"] = "name"
        }
        MenuRow {
            text: "Date added"
            iconName: Theme.prefs[root.viewKey + "Sort"] === "dateAdded" ? "check" : ""
            onTriggered: Theme.prefs[root.viewKey + "Sort"] = "dateAdded"
        }
        MenuSeparator {}
        MenuRow {
            ButtonGroup.group: directions
            text: "Ascending"
            checkable: true
            checked: Theme.prefs[root.viewKey + "SortAsc"]
            iconName: checked ? "check" : ""
            onTriggered: Theme.prefs[root.viewKey + "SortAsc"] = true
        }
        MenuRow {
            ButtonGroup.group: directions
            text: "Descending"
            checkable: true
            checked: !Theme.prefs[root.viewKey + "SortAsc"]
            iconName: checked ? "check" : ""
            onTriggered: Theme.prefs[root.viewKey + "SortAsc"] = false
        }
    }
    LibraryCollectionView {
        id: collection
        anchors.top: heading.bottom
        anchors.topMargin: Theme.gapSm
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: Theme.gapXl
        anchors.rightMargin: Theme.gapXl
        model: root.model
        delegate: root.delegate
        viewObjectName: root.viewKey + "CollectionView"
        listMode: !Theme.prefs[root.viewKey + "Grid"]
        compact: Theme.prefs[root.viewKey + "Compact"]
        tileSize: root.tileSize
        artworkPadding: 17
        artworkRadius: root.artworkRadius
        roundArtwork: root.roundArtwork
        LibraryEmptyState {
            anchors.centerIn: parent
            visible: collection.count === 0
            iconName: root.emptyIcon
            title: root.emptyTitle
            description: root.emptyDescription
        }
    }
}
