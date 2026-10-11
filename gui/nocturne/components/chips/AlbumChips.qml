import QtQuick
import Nocturne

// The track editor's album field: every record this track is on, in the track's
// own order, the first one the record it belongs to.
//
// The same component as `ArtistChips` with square sleeves, but the order means
// something stronger here. A track can be on its original release and on three
// compilations; only one of those is the album it *belongs* to, and that one
// decides which artwork the track carries and which name its `album` shows.
// Dragging a chip to the front is how that is said, which is why the head of the
// list is marked rather than merely first.
//
// Suggestions matter more here too. Two unrelated records really are both called
// "Greatest Hits", so a typed name is genuinely ambiguous where an artist's is
// not — picking from the list is the only way to say which row was meant, and
// the track count beside each one is what tells them apart.
Item {
    id: root

    readonly property int chipCount: chips.chipCount
    readonly property int labelWidth: chips.labelWidth
    readonly property bool dragging: chips.dragging

    // `albumIds` and `albumNames` travel together, unlike the artist field's:
    // an album that does not exist yet is created by name **and lead artist**,
    // so the name is not merely the display of the id — it is the other half of
    // what identifies the row. See `editVM.setAlbums`.
    signal changed(var albumIds, var albumNames)
    signal albumOpened(int albumId)

    implicitHeight: chips.implicitHeight

    function setFromIds(ids, fallbackNames) { chips.setFromIds(ids, fallbackNames) }
    function staged() { return chips.staged() }
    function _removeAt(index) { chips._removeAt(index) }
    function _beginDrag(index) { chips._beginDrag(index) }
    function _dragTo(x, y) { chips._dragTo(x, y) }
    function _endDrag(commit) { chips._endDrag(commit) }

    // The staged set as the ref list `editVM.setAlbums` takes.
    function stagedRefs() {
        var out = []
        var s = chips.staged()
        for (var i = 0; i < s.ids.length; i++)
            out.push({ id: s.ids[i], name: s.names[i] })
        return out
    }

    EntityChips {
        id: chips
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top

        label: "Albums"
        placeholderEmpty: "Add an album — start typing to search"
        placeholderMore: "Add another…"
        // Square, where the artist's is a circle. The same one property that
        // tells a record from a person everywhere else in the interface.
        avatarRadiusRatio: Theme.radiusSm / 24
        imageKey: "coverHash"
        markFirstAsPrimary: true

        lookup: function (id) { return albumsVM.album(id) }
        suggestFn: function (text, limit) { return albumsVM.suggest(text, limit) }
        idForName: function (name) { return albumsVM.idForName(name) }

        onChanged: function (ids, names) { root.changed(ids, names) }
        onEntityOpened: function (id) { root.albumOpened(id) }
    }
}
