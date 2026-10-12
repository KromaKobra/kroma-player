import QtQuick

// Selection belongs to the model; this controller mirrors it for pooled delegates.
Item {
    id: root
    property var model: null
    property var selectedIds: new Set()
    property int anchor: -1
    property alias deselectTimer: timer
    visible: false

    function sync(resetAnchor) {
        if (resetAnchor) { timer.stop(); anchor = -1 }
        var next = model && typeof model.selectedUids === "function"
                   ? new Set(model.selectedUids()) : new Set()
        if (next.size !== selectedIds.size
                || !Array.from(next).every(function(uid) { return selectedIds.has(uid) }))
            selectedIds = next
    }
    function rowOf(uid) {
        if (!model) return -1
        for (var i = 0; i < model.rowCount(); ++i)
            if (model.uidAt(i) === uid) return i
        return -1
    }
    function selectRange(from, to) {
        var a = rowOf(from), b = rowOf(to)
        if (a < 0 || b < 0) return
        var next = new Set()
        for (var i = Math.min(a, b); i <= Math.max(a, b); ++i)
            next.add(model.uidAt(i))
        write(next)
    }
    function write(next) {
        selectedIds = next
        if (model && typeof model.setSelectedMultiple === "function")
            model.setSelectedMultiple(Array.from(next))
    }
    function click(uid, ctrl, shift) {
        timer.stop()
        if (!model) return
        if (shift && anchor >= 0) selectRange(anchor, uid)
        else if (ctrl) {
            var next = new Set(selectedIds)
            if (next.has(uid)) next.delete(uid)
            else next.add(uid)
            anchor = uid
            write(next)
        } else if (selectedIds.size === 1 && selectedIds.has(uid)) {
            timer.uid = uid
            timer.restart()
        } else {
            anchor = uid
            selectedIds = new Set([uid])
            if (typeof model.setSelectedExclusive === "function") model.setSelectedExclusive(uid)
        }
    }
    function activate(uid) {
        timer.stop()
        if (!selectedIds.has(uid)) click(uid, false, false)
        timer.stop()
    }
    function clear() {
        timer.stop()
        anchor = -1
        selectedIds = new Set()
        if (model && typeof model.clearSelection === "function") model.clearSelection()
    }
    Timer {
        id: timer
        interval: Math.min(Application.styleHints.mouseDoubleClickInterval, 250)
        property var uid: -1
        onTriggered: if (root.selectedIds.size === 1 && root.selectedIds.has(uid)) root.clear()
    }
    onModelChanged: sync(true)
    Connections {
        target: root.model
        ignoreUnknownSignals: true
        function onSelectionChanged() { root.sync(false) }
        function onModelReset() {
            root.sync(false)
            if (root.anchor >= 0 && root.rowOf(root.anchor) < 0) root.anchor = -1
        }
    }
}
