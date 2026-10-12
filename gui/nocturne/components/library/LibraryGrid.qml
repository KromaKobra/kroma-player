import QtQuick
import QtQuick.Controls
import Nocturne
import "../artwork"

// Layout only. Models and delegates retain ownership of collection behavior.
GridView {
    id: root
    property var controller: null
    property int tileSize: 200
    property int artworkPadding: Theme.gapSm
    property int labelHeight: 48
    property real artworkRadius: Theme.radius
    property bool roundArtwork: false
    readonly property bool listMode: false
    readonly property int columns: Math.max(1, Math.floor(width / Math.max(120, Math.min(300, tileSize))))
    readonly property int artSize: Math.max(1, cellWidth - 2 * artworkPadding)
    property alias artMask: sharedMask
    signal viewportChanged()

    // Resolve selected positions once per change, never through live delegates:
    // neighbors can be offscreen or pooled. Resizing only changes the stride.
    readonly property var selectedIds: controller ? controller.selectedIds : null
    property var _selectedRows: new Set()

    function refreshSelection() {
        var rows = []
        if (model && selectedIds && selectedIds.size > 0) {
            if (typeof model.selectedRows === "function") rows = model.selectedRows()
            else {
                for (var i = 0; i < model.rowCount(); ++i)
                    if (selectedIds.has(model.uidAt(i))) rows.push(i)
            }
        }
        _selectedRows = new Set(rows)
    }
    function scheduleSelectionRefresh() {
        // Controllers publish IDs before writing to the model. Coalesce both
        // signals so the batched selectedRows() call sees the committed state.
        Qt.callLater(refreshSelection)
    }
    // Physical neighbors: left (1), right (2), above (4), below (8).
    function selectionEdges(row) {
        if (row < 0 || row >= count || !_selectedRows.has(row)) return 0
        var column = row % columns
        var left = column > 0 && _selectedRows.has(row - 1)
        var right = column < columns - 1 && row + 1 < count && _selectedRows.has(row + 1)
        if (effectiveLayoutDirection === Qt.RightToLeft) {
            var swap = left
            left = right
            right = swap
        }
        return (left ? 1 : 0) | (right ? 2 : 0)
             | (row >= columns && _selectedRows.has(row - columns) ? 4 : 0)
             | (row + columns < count && _selectedRows.has(row + columns) ? 8 : 0)
    }
    onSelectedIdsChanged: scheduleSelectionRefresh()
    onModelChanged: { _selectedRows = new Set(); scheduleSelectionRefresh() }
    Component.onCompleted: scheduleSelectionRefresh()
    Connections {
        target: root.model
        ignoreUnknownSignals: true
        function onSelectionChanged() { root.scheduleSelectionRefresh() }
        function onModelReset() { root.scheduleSelectionRefresh() }
        function onRowsInserted() { root.scheduleSelectionRefresh() }
        function onRowsRemoved() { root.scheduleSelectionRefresh() }
        function onRowsMoved() { root.scheduleSelectionRefresh() }
        function onLayoutChanged() { root.scheduleSelectionRefresh() }
    }

    cellWidth: Math.max(1, Math.floor(width / columns))
    cellHeight: cellWidth + labelHeight
    clip: true
    reuseItems: true
    cacheBuffer: cellHeight * 2
    maximumFlickVelocity: 6000
    flickDeceleration: 3000
    onContentYChanged: viewportChanged()
    onHeightChanged: viewportChanged()
    onColumnsChanged: viewportChanged()
    onCellHeightChanged: viewportChanged()
    onCountChanged: viewportChanged()

    LibrarySelectionBackground { view: root }

    // One texture per view, never one mask layer per recycled delegate.
    CornerMask {
        id: sharedMask
        targetWidth: root.artSize
        targetHeight: root.artSize
        radius: root.roundArtwork ? width / 2 : root.artworkRadius
    }
    ScrollBar.vertical: ScrollBar { width: 7 }
    SmoothWheel { target: root; visible: root.visible }
}
