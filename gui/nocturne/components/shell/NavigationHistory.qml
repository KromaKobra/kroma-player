import QtQuick

// Session-only descriptors, never view instances or database rows. Reassign
// arrays for QML notification; bound both memory and history-menu work.
QtObject {
    property var entries: []
    property int cursor: -1
    property int limit: 60
    readonly property bool canGoBack: cursor > 0
    readonly property bool canGoForward: cursor >= 0 && cursor < entries.length - 1
    function update(route) {
        if (cursor < 0) return
        var next = entries.slice()
        next[cursor] = route
        entries = next
    }
    function push(route) {
        if (cursor >= 0 && entries[cursor].key === route.key) {
            update(route)
            return
        }
        var next = entries.slice(0, cursor + 1)
        next.push(route)
        entries = next.slice(-Math.max(1, limit))
        cursor = entries.length - 1
    }
}
