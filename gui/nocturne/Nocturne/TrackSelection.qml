pragma Singleton
import QtQuick

// Which track list the deselect gestures act on.
//
// Deselecting is a window-level gesture — Escape, or a press on the ambient
// background — but the selection lives in a TrackTable five levels down. The
// visible table registers itself here so the window can reach it without every
// view in between having to forward a signal it has no other use for.
//
// Exactly one table is ever registered: a table is `active` only while its page
// is the one on screen, and the two pages that hold a pair — queue/history,
// library/import — only ever mount one of them at a time.
QtObject {
    // The TrackTable on the visible page, or null when that page has no list.
    property var table: null

    function clear() {
        if (table)
            table.clearSelection()
    }
}
