import QtQuick
import QtQuick.Controls
import Nocturne
import "../controls"
import "../tracks"
import "../tracks/TrackColumns.js" as Catalog

// Artist and album adapters supply their own model, delegate and labels.
Item {
    id: root
    required property string viewKey
    required property var model
    required property Component delegate
    property bool active: false
    property var columnLayout: null
    property bool allowCreate: false
    property Component emptyAction: null
    property var contextMenu: ownMenu
    readonly property var selectedIds: selection.selectedIds
    readonly property var activeView: collection.activeView
    property var menuEntity: ({})
    signal openRequested(int entityId, string coverHash)
    signal editRequested(int entityId)
    signal contextRequested(var entity)
    signal createRequested()

    LibrarySelection { id: selection; model: root.model }
    function handleClick(uid, ctrl, shift) { selection.click(uid, ctrl, shift) }
    function clearSelection() { selection.clear() }
    function prepareActivation(uid) { selection.activate(uid) }
    function playEntity(uid) {
        selection.deselectTimer.stop()
        playerVM.playEntries(model.playbackEntries([uid]))
    }
    function openContext(uid) {
        selection.deselectTimer.stop()
        if (!selectedIds.has(uid)) handleClick(uid, false, false)
        menuEntity = model.entity(uid)
        contextRequested(menuEntity)
        contextMenu.entries = model.playbackEntries(Array.from(selectedIds))
        contextMenu.selectionCount = selectedIds.size
        contextMenu.inDetail = false
        contextMenu.popup()
    }
    onActiveChanged: {
        if (active) {
            TrackSelection.table = root
            sidebarVM.setSelection([])
        } else if (TrackSelection.table === root) TrackSelection.table = null
    }
    LibraryCollectionMenu {
        id: ownMenu
        objectName: root.viewKey + "CollectionMenu"
        onOpenRequested: root.openRequested(root.menuEntity.id, root.menuEntity.coverHash)
        onEditRequested: root.editRequested(root.menuEntity.id)
    }
    readonly property int headerHeight: !Theme.prefs[viewKey + "Grid"] && columnLayout && count > 0 ? 24 : 0
    readonly property real leadWidth: Catalog.NUM_W + Theme.gap
        + (activeView && activeView.listMode ? activeView.artSize
           : Theme.prefs[viewKey + "Compact"] ? 30 : 48) + Theme.gap
    function pushBudget() {
        if (columnLayout) columnLayout.setBudget(collection.width - 16 - leadWidth, Theme.gap)
    }
    onColumnLayoutChanged: pushBudget()
    onLeadWidthChanged: pushBudget()
    property string emptyIcon: ""
    property string emptyTitle: ""
    property string emptyDescription: ""
    property bool roundArtwork: false
    property real artworkRadius: Theme.radiusLg
    readonly property int count: collection.count
    readonly property int tileSize: Math.max(120, Math.min(300, Theme.prefs[viewKey + "TileSize"]))
    function openOptions(anchor) { options.openFor(anchor) }
    function openSort(anchor) { sortMenu.openFor(anchor) }

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

    ViewOptionsMenu {
        id: options
        objectName: root.viewKey + "ViewOptionsMenu"
        viewKey: root.viewKey
        viewportWidth: collection.activeView ? collection.activeView.width : collection.width
        leadingSeparator: root.allowCreate
        MenuRow {
            text: "New playlist…"; iconName: "plus"
            visible: root.allowCreate; height: visible ? implicitHeight : 0
            onTriggered: root.createRequested()
        }
    }
    CollectionSortMenu {
        id: sortMenu
        viewKey: root.viewKey
    }
    TrackColumnHeader {
        visible: root.headerHeight > 0
        height: root.headerHeight
        anchors.left: parent.left; anchors.right: parent.right
        sideMargin: Theme.gapXl + 8
        leadWidth: root.leadWidth
        trailWidth: 0
        layout: root.columnLayout
        title: sortField === "primaryArtists" ? "Artists" : "Name"
        titleFields: root.viewKey === "album" ? ["name", "primaryArtists"] : ["name"]
        sortDirections: ({name: true, primaryArtists: true, trackCount: false,
                          totalDuration: false, isComplete: false, releaseDate: false})
        sortField: Theme.prefs[root.viewKey + "Sort"]
        sortAscending: Theme.prefs[root.viewKey + "SortAsc"]
        onSortRequested: function(field, ascending) {
            Theme.prefs[root.viewKey + "SortAsc"] = ascending
            Theme.prefs[root.viewKey + "Sort"] = field
        }
        onWheelDelta: root.activeView && root.activeView.listMode ? root.activeView.wheelHandler.handleWheel : null
    }
    LibraryCollectionView {
        id: collection
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.topMargin: root.headerHeight
        controller: root
        onWidthChanged: root.pushBudget()
        anchors.leftMargin: Theme.gapXl
        anchors.rightMargin: Theme.gapXl
        model: root.model
        delegate: root.delegate
        viewObjectName: root.viewKey + "CollectionView"
        scrollBarInset: Theme.gapXl
        listMode: !Theme.prefs[root.viewKey + "Grid"]
        compact: Theme.prefs[root.viewKey + "Compact"]
        tileSize: root.tileSize
        artworkRadius: root.artworkRadius
        roundArtwork: root.roundArtwork
        LibraryEmptyState {
            anchors.centerIn: parent
            visible: collection.count === 0
            iconName: root.emptyIcon
            title: root.emptyTitle
            description: root.emptyDescription
            Loader {
                anchors.horizontalCenter: parent.horizontalCenter
                sourceComponent: root.emptyAction
            }
        }
    }
}
