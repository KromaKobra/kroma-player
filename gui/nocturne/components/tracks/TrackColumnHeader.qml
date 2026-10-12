import QtQuick
import QtQuick.Controls
import Nocturne
import "../controls"
import "TrackColumns.js" as Catalog

/*
 * The header strip above a track list.
 *
 * It mirrors the row's geometry exactly — same side margins, same lead and
 * trail reservations, same gap between cells — by reading the *same* properties
 * off TrackTable that the row delegate reads. Neither computes its own layout,
 * so the two cannot drift apart.
 *
 * Two stacked mouse areas per cell:
 *
 *   • a resize handle, one gap wide, on the cell's LEFT edge. Dragging it
 *     resizes the cell to its right; the columns to the left give up the
 *     inverse, nearest first and ending at the title column, so every edge from
 *     this one rightwards stays put and the divider tracks the cursor to the
 *     pixel.
 *
 *   • a reorder area filling the rest of the cell, offset past the handle so
 *     the two never fight over the same pixels.
 *
 * Right-click toggles columns.
 *
 * A click that never became a reorder sorts, when the list asks for it by
 * handing over `sortDirections`. Each header steps through its sort keys —
 * natural direction, flipped, then the next key, then off; see
 * TrackColumns.nextSort — and a chevron beside the label says which column and
 * which way. The header only *requests* a sort: whoever owns the query owns the
 * state, and the arrow follows `sortField`/`sortAscending` back down.
 *
 * Note what is deliberately *not* here: a full-fill MouseArea that claims
 * clicks. This header sits over the list in the Library, and one that did
 * would swallow presses meant for the rows underneath. Hover and right-click
 * go through handlers instead. Wheel input does get a full-fill MouseArea —
 * `acceptedButtons: Qt.NoButton` below claims wheel only, forwarding it to the
 * list so scrolling doesn't die over this 24px strip.
 */
Item {
    id: header

    property var layout: null
    property real sideMargin: Theme.gapXl
    property int leadWidth: 0
    property int trailWidth: 0

    // Called with the wheel event when a scroll lands on the header, so it
    // can be handled identically to a scroll over the rows below it.
    property var onWheelDelta: null

    readonly property var columns: layout ? layout.renderedColumns : []
    readonly property real gap: Theme.gap

    // ------------------------------------------------------------- sorting

    // {fieldKey: ascendingFirst}, from TrackColumns.sortDirections. Empty —
    // the default — leaves every header inert, which is what the queue,
    // playlists and the other lists whose order means something want.
    property var sortDirections: ({})
    // The sort the owner has chosen, "" for none. Only drawn from here, never
    // written: the click asks, `sortRequested`, and the answer comes back in.
    property string sortField: ""
    property bool sortAscending: false

    signal sortRequested(string field, bool ascending)

    function cycle(keys) {
        var next = Catalog.nextSort(keys, sortField, sortAscending, sortDirections);
        if (next.field !== sortField || next.ascending !== sortAscending)
            sortRequested(next.field, next.ascending);
    }

    function sortDescription(name, active) {
        return active ? name + ", sorted " + (sortAscending ? "ascending" : "descending")
                      : name;
    }

    readonly property var titleSortKeys: Catalog.sortableKeys(Catalog.TITLE_SORT_KEYS,
                                                              sortDirections)
    readonly property bool titleSorted: titleSortKeys.indexOf(sortField) !== -1

    implicitHeight: 24

    // ------------------------------------------------------------ drag state

    property string draggedKey: ""
    property string dragLabel: ""
    property int dropIndex: -1        // index in `columns` to insert before
    property bool isDragging: false
    property real ghostX: 0

    /* Which slot the cursor is over, as an index into the rendered columns.
     * `columns.length` means "after the last one". The title column is pinned
     * at index 0 of the row and is not a drop target, so this never returns a
     * position left of the first data column. */
    function computeDropIndex(xInHeader) {
        for (var i = 0; i < columns.length; ++i) {
            var item = cellRepeater.itemAt(i);
            if (!item)
                continue;
            if (xInHeader < item.x + item.width / 2)
                return i;
        }
        return columns.length;
    }

    /* Translate a rendered-column index into an index in the layout's `order`.
     * They differ whenever a narrow window has dropped a column, so this cannot
     * be skipped. */
    function orderIndexOf(renderedIndex) {
        if (!layout)
            return 0;
        if (renderedIndex >= columns.length)
            return layout.order.length;
        return layout.order.indexOf(columns[renderedIndex].key);
    }

    function endDrag(commitMove) {
        // Read everything out first: moveColumn rebuilds the Repeater, which
        // destroys the delegate that called this, and anything read afterwards
        // comes back undefined.
        var key = draggedKey;
        var target = commitMove && dropIndex >= 0 ? orderIndexOf(dropIndex) : -1;

        isDragging = false;
        draggedKey = "";
        dragLabel = "";
        dropIndex = -1;

        if (target >= 0 && layout)
            layout.moveColumn(key, target);
    }

    // ------------------------------------------------------------ background

    // Only paints once the header has actually pinned itself over the list; a
    // solid band while it is still riding the Library's overview would read as
    // a seam across it.
    //
    // Fully opaque, not frosted. Rows scroll underneath, and at even 94% the
    // titles and artwork passing behind show through as a smear across the
    // labels. Theme.bg is the window's own colour, so where the ambient
    // backdrop is dim — which is everywhere in the middle of a list — the band
    // is indistinguishable from what it covers.
    property bool pinned: false

    Rectangle {
        anchors.fill: parent
        color: Theme.bg
        opacity: header.pinned ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.fast } }
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: header.sideMargin
        anchors.rightMargin: header.sideMargin
        height: 1
        color: Theme.line
    }

    HoverHandler { id: headerHover }

    TapHandler {
        acceptedButtons: Qt.RightButton
        gesturePolicy: TapHandler.ReleaseWithinBounds
        onTapped: columnMenu.popup()
    }

    // The header covers the top of the list in sticky mode, so without this the
    // wheel dies over a 24px strip. A MouseArea rather than a WheelHandler —
    // acceptedButtons: Qt.NoButton claims wheel input only, and it's the
    // mechanism TrackTable's own wheel catcher uses for the same reason.
    MouseArea {
        anchors.fill: parent
        enabled: header.onWheelDelta !== null
        acceptedButtons: Qt.NoButton
        onWheel: (event) => header.onWheelDelta(event)
    }

    // ----------------------------------------------------------------- cells

    Item {
        id: cellRow
        anchors.fill: parent
        anchors.leftMargin: header.sideMargin + header.leadWidth
        anchors.rightMargin: header.sideMargin + header.trailWidth

        // The title column: pinned, never reordered, and resized only through
        // the divider of whichever column sits to its right. Its cell stacks
        // the title over the artist, so its label names whichever of the two
        // the list is sorted by.
        Item {
            id: titleCell
            objectName: "columnHeader_title"
            x: 0
            width: header.layout ? header.layout.titleWidth : 0
            height: parent.height

            readonly property bool sortable: header.titleSortKeys.length > 0
            readonly property string name: header.sortField === "artist" ? "Artist" : "Title"

            Accessible.role: Accessible.ColumnHeader
            Accessible.name: header.sortDescription(name, header.titleSorted)
            Accessible.onPressAction: if (sortable) header.cycle(header.titleSortKeys)

            SortLabel {
                id: titleLabel
                anchors.fill: parent
                text: titleCell.name.toUpperCase()
                active: header.titleSorted
                ascending: header.sortAscending
                hovered: titleArea.containsMouse
                pressed: titleArea.pressed
            }

            MouseArea {
                id: titleArea
                anchors.fill: parent
                enabled: titleCell.sortable
                hoverEnabled: true
                acceptedButtons: Qt.LeftButton
                cursorShape: Qt.PointingHandCursor
                onClicked: header.cycle(header.titleSortKeys)
            }
        }

        Repeater {
            id: cellRepeater
            model: header.columns

            delegate: Item {
                id: cell
                objectName: "columnHeader_" + colDef.key

                required property int index
                required property var modelData
                readonly property var colDef: modelData

                readonly property real colWidth: {
                    if (!header.layout)
                        return colDef.pref;
                    var w = header.layout.renderedWidths[colDef.key];
                    return (w !== undefined && w > 0) ? w : colDef.pref;
                }

                readonly property bool beingDragged: header.isDragging
                                                     && header.draggedKey === colDef.key

                readonly property var sortKeys: Catalog.sortableKeys(colDef.sortKeys,
                                                                     header.sortDirections)
                readonly property bool sortable: sortKeys.length > 0
                readonly property bool sorted: sortKeys.indexOf(header.sortField) !== -1

                Accessible.role: Accessible.ColumnHeader
                Accessible.name: header.sortDescription(colDef.label || "", sorted)
                Accessible.onPressAction: if (sortable) header.cycle(sortKeys)

                // x is derived from the layout rather than laid out by a Row, so
                // that the header and the row delegate run the same arithmetic
                // over the same numbers. Reading the layout instead of the
                // sibling items also keeps this a real binding -- itemAt() is a
                // method call, and depending on it would leave cells stale.
                // No Behavior: during a drag this must track the cursor exactly.
                x: {
                    if (!header.layout)
                        return 0;
                    var acc = header.layout.titleWidth + header.gap;
                    var w = header.layout.renderedWidths;
                    for (var i = 0; i < index && i < header.columns.length; ++i)
                        acc += w[header.columns[i].key] + header.gap;
                    return acc;
                }
                width: colWidth
                height: parent.height

                // ---- resize handle, on this cell's left edge ----
                MouseArea {
                    id: handle
                    visible: colDef.resizable !== false
                    enabled: visible
                    z: 10
                    width: header.gap
                    x: -header.gap
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    cursorShape: Qt.SizeHorCursor
                    hoverEnabled: true
                    preventStealing: true

                    // The cell's right edge in cellRow coordinates, captured on
                    // press. It does not move during the drag -- the absorber
                    // to the left takes the inverse delta -- so the width is
                    // this minus the cursor, computed absolutely every frame
                    // rather than accumulated from deltas that would drift.
                    property real rightEdge: 0

                    // How far into the handle the cursor grabbed. The handle is
                    // a gap wide, so without this the divider snaps to the
                    // cursor on mousedown and the column jumps by up to a gap
                    // before the drag has moved at all.
                    property real grabOffset: 0

                    onPressed: function (mouse) {
                        rightEdge = cell.x + cell.width;
                        grabOffset = mapToItem(cellRow, mouse.x, 0).x - cell.x;
                        if (header.layout)
                            header.layout.beginResize();
                    }

                    onPositionChanged: function (mouse) {
                        if (!(mouse.buttons & Qt.LeftButton) || !header.layout)
                            return;
                        var xInRow = mapToItem(cellRow, mouse.x, 0).x - grabOffset;
                        header.layout.resizeTo(colDef.key, Math.round(rightEdge - xInRow));
                    }

                    onReleased: if (header.layout) header.layout.endResize()
                    onCanceled: if (header.layout) header.layout.endResize()

                    Rectangle {
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.verticalCenter: parent.verticalCenter
                        width: 1
                        height: parent.height * 0.5
                        color: header.dropIndex === cell.index ? Theme.accent
                             : handle.containsMouse ? Theme.alpha(Theme.text, 0.45)
                             : headerHover.hovered ? Theme.alpha(Theme.text, 0.14)
                             : "transparent"
                        Behavior on color { ColorAnimation { duration: Theme.fast } }
                    }
                }

                // Drop marker for a cell with no handle of its own (a
                // fixed-width column), and for dropping past the last cell.
                Rectangle {
                    visible: colDef.resizable === false
                             && header.dropIndex === cell.index
                    x: -header.gap / 2
                    anchors.verticalCenter: parent.verticalCenter
                    width: 2
                    height: parent.height * 0.6
                    color: Theme.accent
                    z: 20
                }

                Rectangle {
                    visible: cell.index === header.columns.length - 1
                             && header.dropIndex === header.columns.length
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    width: 2
                    height: parent.height * 0.6
                    color: Theme.accent
                    z: 20
                }

                // ---- reorder area, clear of the handle strip ----
                MouseArea {
                    id: dragArea
                    anchors.fill: parent
                    z: 5
                    acceptedButtons: Qt.LeftButton
                    // Hover only lights the label of a header that sorts; one
                    // that doesn't has nothing to offer a click.
                    hoverEnabled: cell.sortable
                    cursorShape: cell.beingDragged ? Qt.ClosedHandCursor
                                                   : Qt.OpenHandCursor

                    property real startX: 0
                    property bool dragging: false

                    onPressed: function (mouse) {
                        startX = mouse.x;
                        dragging = false;
                    }

                    // Hover reports moves too; only a held button reorders.
                    onPositionChanged: function (mouse) {
                        if (!pressed)
                            return;
                        if (!dragging && Math.abs(mouse.x - startX) > 5) {
                            dragging = true;
                            header.draggedKey = colDef.key;
                            header.dragLabel = colDef.label || "";
                            header.isDragging = true;
                        }
                        if (!dragging)
                            return;
                        var p = mapToItem(cellRow, mouse.x, 0).x;
                        header.ghostX = mapToItem(header, mouse.x, 0).x;
                        header.dropIndex = header.computeDropIndex(p);
                    }

                    // A press that never travelled far enough to be a reorder
                    // is a click, and a click sorts — but only if it lets go
                    // over the cell, so dragging off is still a way to change
                    // your mind.
                    onReleased: {
                        var wasDragging = dragging;
                        dragging = false;
                        if (wasDragging)
                            header.endDrag(true);
                        else if (cell.sortable && containsMouse)
                            header.cycle(cell.sortKeys);
                    }

                    onCanceled: {
                        dragging = false;
                        header.endDrag(false);
                    }
                }

                // ---- label ----
                SortLabel {
                    anchors.fill: parent
                    text: colDef.label || ""
                    alignRight: colDef.align === "right"
                    centred: colDef.align === "centre"
                    active: cell.sorted
                    ascending: header.sortAscending
                    hovered: dragArea.containsMouse
                    pressed: dragArea.pressed && !dragArea.dragging
                    opacity: cell.beingDragged ? 0.3 : 1
                    Behavior on opacity { NumberAnimation { duration: Theme.fast } }
                }
            }
        }
    }

    // ------------------------------------------------------------- drag ghost

    Rectangle {
        id: ghost
        visible: header.isDragging
        x: Math.max(0, Math.min(header.width - width, header.ghostX - width / 2))
        anchors.verticalCenter: parent.verticalCenter
        z: 100
        width: ghostLabel.implicitWidth + 18
        height: 20
        radius: Theme.radiusSm
        color: Theme.raised
        border.width: 1
        border.color: Theme.alpha(Theme.accent, 0.5)

        Text {
            id: ghostLabel
            anchors.centerIn: parent
            text: header.dragLabel
            color: Theme.text
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzTiny
            font.weight: Font.DemiBold
            font.letterSpacing: 1.4
        }
    }

    // -------------------------------------------------------- visibility menu

    NocturneMenu {
        id: columnMenu

        Instantiator {
            model: header.layout ? header.layout.menuItems : []
            delegate: MenuRow {
                required property var modelData
                text: modelData.label
                // The checkmark idiom used by the rest of the interface -- see
                // the Library's sort menu.
                iconName: modelData.visible ? "check" : ""
                onTriggered: {
                    // Hiding the column a list is sorted by would leave an
                    // order with nothing on screen to explain it, or to undo
                    // it from — so the sort goes with the column.
                    var col = Catalog.entry(modelData.key);
                    if (modelData.visible && col
                            && Catalog.sortableKeys(col.sortKeys, header.sortDirections)
                                      .indexOf(header.sortField) !== -1)
                        header.sortRequested("", false);
                    header.layout.toggle(modelData.key);
                }
            }
            onObjectAdded: (index, obj) => columnMenu.insertItem(index, obj)
            onObjectRemoved: (index, obj) => columnMenu.removeItem(obj)
        }

        MenuSeparator {
            padding: 0
            topPadding: 4
            bottomPadding: 4
            contentItem: Rectangle { implicitHeight: 1; color: Theme.line }
        }

        MenuRow {
            text: "Reset columns"
            onTriggered: header.layout.reset()
        }
    }

    // ------------------------------------------------------------ sort label

    /* A header's label, and the chevron that says the list is sorted by it.
     *
     * The chevron sits on the side away from the column's alignment — after a
     * left-aligned label, before a right-aligned one — so the label never moves
     * when it appears, and right-aligned figures stay over their digits. It
     * reserves its room only while shown, so an unsorted label keeps the whole
     * cell to elide into.
     *
     * One glyph, turned rather than swapped: a flip reads as the same arrow
     * reversing, and a rotation is a transform, so the SVG is never
     * rasterised again for it. */
    component SortLabel: Item {
        id: sortLabel

        property string text: ""
        property bool alignRight: false
        property bool centred: false
        property bool active: false
        property bool ascending: false
        property bool hovered: false
        property bool pressed: false

        readonly property int arrowSize: 10
        readonly property int arrowGap: 4
        readonly property int reserve: active ? arrowSize + arrowGap : 0
        readonly property int motion: Theme.prefs.reducedMotion ? 0 : Theme.fast

        // The direction the chevron shows, held while the label is not the
        // sorted one. Every label is handed the list's direction, and without
        // this one that is fading out would turn to follow a sort that has
        // moved to another column.
        property bool shownAscending: false
        Binding on shownAscending {
            when: sortLabel.active
            value: sortLabel.ascending
            restoreMode: Binding.RestoreNone
        }

        Text {
            id: label
            objectName: "sortLabelText"
            anchors.verticalCenter: parent.verticalCenter
            x: sortLabel.alignRight ? parent.width - width
             : sortLabel.centred ? Math.round((parent.width - width) / 2)
             : 0
            width: Math.min(implicitWidth, Math.max(0, parent.width - sortLabel.reserve))
            text: sortLabel.text
            elide: Text.ElideRight
            color: sortLabel.pressed ? Theme.text
                 : (sortLabel.active || sortLabel.hovered) ? Theme.dim
                 : Theme.faint
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzTiny
            font.weight: Font.DemiBold
            font.letterSpacing: 1.4
            Behavior on color { ColorAnimation { duration: Theme.fast } }

            // The stacked title header renames itself as it moves from one of
            // its keys to the next: out, swap, back in, so the change reads as
            // the header changing what it means rather than as a flicker.
            Behavior on text {
                enabled: sortLabel.motion > 0
                SequentialAnimation {
                    NumberAnimation { target: label; property: "opacity"; to: 0
                                      duration: sortLabel.motion / 2 }
                    PropertyAction {}
                    NumberAnimation { target: label; property: "opacity"; to: 1
                                      duration: sortLabel.motion / 2 }
                }
            }
        }

        Icon {
            id: arrow
            objectName: "sortArrow"
            name: "chevronDown"
            size: sortLabel.arrowSize
            strokeWidth: 2.4
            // Fixed rather than following the label's hover tint: Icon's
            // colour is its SVG, and animating it would re-rasterise the glyph
            // every frame of the fade.
            color: Theme.dim
            anchors.verticalCenter: label.verticalCenter
            x: sortLabel.alignRight ? label.x - sortLabel.arrowGap - width
                                    : label.x + label.width + sortLabel.arrowGap
            rotation: sortLabel.shownAscending ? 180 : 0

            opacity: sortLabel.active ? 1 : 0
            scale: sortLabel.active ? 1 : 0.8
            visible: opacity > 0

            // Only a chevron already on screen turns. One arriving takes its
            // direction at once, so it never fades in mid-spin.
            Behavior on rotation {
                enabled: arrow.opacity === 1
                NumberAnimation { duration: Theme.prefs.reducedMotion ? 0 : Theme.normal
                                  easing.type: Theme.easing }
            }
            Behavior on opacity { NumberAnimation { duration: sortLabel.motion } }
            Behavior on scale {
                NumberAnimation { duration: sortLabel.motion; easing.type: Theme.easing }
            }
        }
    }
}
