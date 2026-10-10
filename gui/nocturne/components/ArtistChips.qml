import QtQuick

// The track editor's artist field: chips you pick, not a string you type.
//
// The machinery is `EntityChips`; what belongs here is the half that is about
// artists. Names are resolved by exact match, so "daft punk" typed beside an
// existing "Daft Punk" mints a second row and neither has the whole catalogue —
// a chip carries the id instead and cannot drift. The order of the chips is the
// track's credit order: who mattered most on this recording, which is a thing
// the user arranges rather than a thing the library decides.
//
// Round pictures, because these are people. `AlbumChips` is the same component
// with square ones, and that is very nearly the whole difference.
Item {
    id: root

    readonly property int chipCount: chips.chipCount
    readonly property int labelWidth: chips.labelWidth
    readonly property bool dragging: chips.dragging

    signal changed(var artistIds, var artistNames)
    signal artistOpened(int artistId)

    implicitHeight: chips.implicitHeight

    // Forwarded rather than reached through, so a caller — the editor page, or
    // a test driving the drag — talks to this component and not to its insides.
    function setFromIds(ids, fallbackNames) { chips.setFromIds(ids, fallbackNames) }
    function staged() { return chips.staged() }
    function _removeAt(index) { chips._removeAt(index) }
    function _beginDrag(index) { chips._beginDrag(index) }
    function _dragTo(x, y) { chips._dragTo(x, y) }
    function _endDrag(commit) { chips._endDrag(commit) }

    EntityChips {
        id: chips
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top

        label: "Artists"
        placeholderEmpty: "Add an artist — start typing to search"
        placeholderMore: "Add another…"
        avatarRadiusRatio: 0.5
        imageKey: "imageHash"

        lookup: function (id) { return artistsVM.artist(id) }
        suggestFn: function (text, limit) { return artistsVM.suggest(text, limit) }
        idForName: function (name) { return artistsVM.idForName(name) }

        onChanged: function (ids, names) { root.changed(ids, names) }
        onEntityOpened: function (id) { root.artistOpened(id) }
    }
}
