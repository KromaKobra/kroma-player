import QtQuick
import Nocturne

// A field of chips you pick, not a string you type. The machinery behind
// ArtistChips and AlbumChips, which differ only in what they look things up in.
//
// The fields these replace were text boxes holding "A, B, C", and every problem
// they caused came from the same place — the names were resolved by *matching
// the text*. Type "daft punk" beside an existing "Daft Punk" and the library
// gains a second artist with half the catalogue, silently, and nothing in the
// app could afterwards tell they were the same.
//
// A chip carries the row's **id**, so committing one says which row was meant
// and no matching is involved. Typing a name that matches nothing still creates
// the row, because sometimes that is genuinely what you want — but you have to
// mean it, and the suggestions make it obvious when you did not.
//
// Everything that is not the drag geometry is a property, because the two
// callers are two questions about a library and not two kinds of widget:
// ArtistChips asks artistsVM, AlbumChips asks albumsVM, the chips are round for
// people and square for records, and the first album means something the first
// artist does not.
Item {
    id: root

    // -------------------------------------------------------- what this field is

    property string label: ""
    property string placeholderEmpty: "Start typing to search"
    property string placeholderMore: "Add another…"

    // How the chip's picture is shaped, as a fraction of its size: 0.5 is the
    // circle a person gets, a small number is the square corner a record gets.
    property real avatarRadiusRatio: 0.5

    // Which key on a view model's object holds the picture's content hash.
    // `artistsVM` says `imageHash`, `albumsVM` says `coverHash`; both point into
    // the same store, which is why one CoverArt serves both.
    property string imageKey: "imageHash"

    // Whether the first chip carries a marker. It does for albums, where the
    // order is not a preference: the head of the list is the record the track
    // belongs to and whose artwork it carries, and that has to be visible while
    // the list is being dragged into shape.
    property bool markFirstAsPrimary: false

    // The three view-model calls, injected. `lookup` and `suggestFn` answer with
    // `{id, name, trackCount, <imageKey>}`; `idForName` with an id or 0.
    property var lookup: function (id) { return null }
    property var suggestFn: function (text, limit) { return [] }
    property var idForName: function (name) { return 0 }

    // Chips currently shown, in order. `entityId` is 0 for a name typed that
    // matched nothing — that row does not exist yet.
    //
    // A ListModel rather than a JS array, and the reason is `move`: reassigning
    // an array makes a Repeater throw every delegate away and build them again,
    // which during a drag destroys the very chip the pointer is holding. A
    // ListModel move is one item changing places, so the delegate survives the
    // gesture and the Flow can animate the rest of the row out of its way.
    ListModel { id: chipModel }

    readonly property int chipCount: chipModel.count
    readonly property int labelWidth: 116

    // The staged set changed. The page forwards it to its view model.
    signal changed(var entityIds, var entityNames)
    // A chip was pressed. Opening the thing it names is a navigation the page
    // owns, not this component.
    signal entityOpened(int entityId)

    implicitHeight: Math.max(40, layout.implicitHeight)

    // ------------------------------------------------------------------ API

    // Fill from the view model. Called on load and after a save, never bound —
    // this component owns its chips once it has them, and a binding would fight
    // every edit.
    function setFromIds(ids, fallbackNames) {
        var next = []
        for (var i = 0; i < ids.length; i++) {
            var info = root.lookup(ids[i])
            if (info)
                next.push(info)
        }
        // A track whose rows could not be resolved still has names. Show them
        // rather than an empty field.
        if (next.length === 0 && fallbackNames) {
            var parts = String(fallbackNames).split(",")
            for (var j = 0; j < parts.length; j++) {
                var name = parts[j].trim()
                if (name !== "")
                    next.push({ id: 0, name: name, trackCount: 0 })
            }
        }
        chipModel.clear()
        for (var k = 0; k < next.length; k++)
            chipModel.append(_row(next[k]))
        suggestions = []
        entry.text = ""
    }

    // A view model's object as a model row. Renamed on the way in: `id` is what
    // a delegate calls itself, so a role of that name cannot be read there
    // without shadowing.
    function _row(info) {
        return {
            entityId: Number(info.id || 0),
            name: String(info.name || ""),
            imageHash: String(info[root.imageKey] || ""),
            trackCount: Number(info.trackCount || 0)
        }
    }

    // The staged set, in order: `{ ids, names }`. The one place the model is
    // read out, so anything asking what is in this field gets the same answer
    // the page is about to be told.
    function staged() {
        var ids = [], names = []
        for (var i = 0; i < chipModel.count; i++) {
            ids.push(chipModel.get(i).entityId)
            names.push(chipModel.get(i).name)
        }
        return { ids: ids, names: names }
    }

    function _emit() {
        var out = staged()
        root.changed(out.ids, out.names)
    }

    function _addChip(chip) {
        for (var i = 0; i < chipModel.count; i++) {
            // Same row, or the same name typed twice in different capitals.
            // Either way it is already here.
            var held = chipModel.get(i)
            if ((chip.id > 0 && held.entityId === chip.id)
                || held.name.toLowerCase() === String(chip.name).toLowerCase())
                return
        }
        chipModel.append(_row(chip))
        entry.text = ""
        suggestions = []
        _emit()
    }

    function _removeAt(index) {
        if (index < 0 || index >= chipModel.count)
            return
        chipModel.remove(index)
        _emit()
    }

    // ------------------------------------------------------------- reordering
    //
    // The order of the chips is the order the field means — a track's credit
    // order for artists, and for albums which record it belongs to — so it is a
    // thing the user arranges, not a thing the library decides. Dragging is the
    // gesture, the same as a playlist row's.
    //
    // TrackTable's drag is not reusable here and neither is its arithmetic: that
    // one slides rows of one height along one axis, and these wrap. What carries
    // over is the rule that makes it feel right — nothing is committed until the
    // gesture ends. The model is reordered live so the row can show where the
    // chip would land, but `changed` fires exactly once, on release.

    // Index of the chip being dragged, or -1. It keeps its slot in the Flow and
    // is drawn offset from it, so the positioner is never fought for control of
    // x and y — see the Translate on the delegate.
    property int _dragIndex: -1
    property real _dragDX: 0
    property real _dragDY: 0
    // Where the chip started, to put it back if the gesture is cancelled rather
    // than released. One number is the whole undo: a drag moves one chip, and
    // moving it back restores the order it came out of.
    property int _dragFrom: -1
    readonly property bool dragging: _dragIndex >= 0

    function _beginDrag(index) {
        _dragFrom = index
        _dragIndex = index
        _dragDX = 0
        _dragDY = 0
    }

    // Where the pointer is now says which slot the chip belongs in. Asked of the
    // laid-out items rather than computed, because a Flow decides for itself
    // where the lines break and only it knows what it did.
    function _dragTo(pointX, pointY) {
        if (_dragIndex < 0)
            return
        // Keep stepping until the chip is where the pointer says. One step per
        // call would be enough for a slow drag and wrong for a quick one: a
        // pointer can cross two chips between move events, and the chip would
        // then trail a slot behind the hand carrying it. Bounded by the number
        // of chips, since each pass either moves it once or stops.
        for (var guard = chipModel.count; guard > 0; guard--)
            if (!_dragStep(pointX, pointY))
                return
    }

    // One place, or false when the chip is already where it belongs.
    function _dragStep(pointX, pointY) {
        for (var i = 0; i < chipModel.count; i++) {
            if (i === _dragIndex)
                continue
            var item = chipRepeater.itemAt(i)
            if (!item)
                continue
            // Same line as the pointer, and the pointer is past its middle on the
            // side the chip would have to move across. Testing the line first is
            // what makes a wrapped row behave: a chip two lines down is nearer in
            // raw distance than the one beside it, and landing there is never
            // what the gesture meant.
            //
            // The item's own y is where the Flow put it, which is where it is
            // going rather than where it is drawn mid-animation — so the target
            // does not slide out from under a pointer that has stopped moving.
            if (pointY < item.y || pointY > item.y + item.height)
                continue
            var middle = item.x + item.width / 2
            if ((i < _dragIndex && pointX < middle)
                || (i > _dragIndex && pointX > middle)) {
                chipModel.move(_dragIndex, i, 1)
                _dragIndex = i
                return true
            }
        }
        return false
    }

    function _endDrag(commit) {
        if (_dragIndex < 0)
            return
        var from = _dragFrom
        var to = _dragIndex
        _dragIndex = -1
        _dragFrom = -1
        _dragDX = 0
        _dragDY = 0
        if (!commit) {
            chipModel.move(to, from, 1)
            return
        }
        if (from !== to)
            _emit()
    }

    // Committing typed text. An exact existing name — whatever its capitals —
    // becomes that row rather than a new one, which is the single most common
    // way a duplicate used to be created.
    function _commitTyped() {
        var name = entry.text.trim()
        if (name === "")
            return
        var existingId = root.idForName(name)
        if (existingId > 0) {
            var info = root.lookup(existingId)
            if (info) {
                _addChip(info)
                return
            }
        }
        _addChip({ id: 0, name: name, trackCount: 0 })
    }

    // -------------------------------------------------------------- internals

    property var suggestions: []

    function _refreshSuggestions() {
        var text = entry.text.trim()
        if (text === "") {
            suggestions = []
            return
        }
        var found = root.suggestFn(text, 6)
        var filtered = []
        for (var i = 0; i < found.length; i++) {
            var taken = false
            for (var j = 0; j < chipModel.count; j++)
                if (chipModel.get(j).entityId === found[i].id)
                    taken = true
            if (!taken)
                filtered.push(found[i])
        }
        suggestions = filtered
    }

    // ------------------------------------------------------------------ view

    Text {
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.topMargin: 12
        width: root.labelWidth - Theme.gapSm
        text: root.label
        color: Theme.faint
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fzSmall
    }

    Column {
        id: layout
        anchors.left: parent.left
        anchors.leftMargin: root.labelWidth
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Theme.gapSm

        // The chips themselves. A Flow rather than a Row so a track with six
        // artists wraps instead of eliding them out of existence.
        Flow {
            id: chipFlow
            objectName: "chipFlow"
            width: parent.width
            spacing: 6
            visible: chipModel.count > 0

            // The reorder animation, and the reason the model can be spliced on
            // every pointer move: the positioner already knows how to slide a
            // child from the slot it had to the one it now has, so a live
            // preview costs nothing to draw.
            move: Transition {
                NumberAnimation { properties: "x,y"
                                  duration: Theme.normal
                                  easing.type: Theme.easing }
            }

            Repeater {
                id: chipRepeater
                model: chipModel

                delegate: Rectangle {
                    id: chip
                    required property int index
                    required property int entityId
                    required property string name
                    required property string imageHash

                    readonly property bool lifted: root._dragIndex === chip.index
                    // Bound to the live index rather than read once, so the
                    // marker follows the chip the moment a drag puts another one
                    // in front of it.
                    readonly property bool isPrimary:
                        root.markFirstAsPrimary && chip.index === 0

                    height: 30
                    width: chipRow.implicitWidth + 10
                    radius: height / 2
                    color: chip.lifted || chipHover.containsMouse
                           ? Theme.raised : Theme.sunken
                    border.width: 1

                    // Offset from the slot the Flow gave it rather than moved to
                    // a position of its own: assigning x or y would break the
                    // positioner's binding and the chip would never find its way
                    // back into the row.
                    transform: Translate {
                        x: chip.lifted ? root._dragDX : 0
                        y: chip.lifted ? root._dragDY : 0
                    }

                    // Above the chips it is passing over, and lifted off the page
                    // the way a dragged playlist row is.
                    z: chip.lifted ? 2 : 0
                    scale: chip.lifted ? 1.04 : 1.0
                    opacity: chip.lifted ? 0.92 : 1.0
                    Behavior on scale {
                        NumberAnimation { duration: Theme.fast; easing.type: Theme.easing }
                    }
                    Behavior on opacity { NumberAnimation { duration: Theme.fast } }
                    // A row that does not exist yet is marked, because "this will
                    // create a new one" is worth knowing *before* saving rather
                    // than discovering in the library afterwards. The primary
                    // marker wins the border when both apply: which record a
                    // track belongs to is the more consequential of the two.
                    border.color: chip.isPrimary
                                  ? Theme.alpha(Theme.accent, 0.65)
                                  : (chip.entityId > 0
                                     ? Theme.line
                                     : Theme.alpha(Theme.accentAlt, 0.55))

                    Behavior on color { ColorAnimation { duration: Theme.fast } }

                    Row {
                        id: chipRow
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                        anchors.leftMargin: 3
                        spacing: 6

                        CoverArt {
                            width: 24
                            height: 24
                            radius: width * root.avatarRadiusRatio
                            tier: 128
                            hash: chip.imageHash
                            placeholderText: chip.name
                            showPlaceholderGlyph: false
                            anchors.verticalCenter: parent.verticalCenter
                        }

                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: chip.name
                            color: Theme.text
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fzSmall
                        }

                        // The whole visual answer to "which album's artwork does
                        // this track carry". Drawn inside the chip so it travels
                        // with a drag rather than staying behind at slot zero.
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            visible: chip.isPrimary
                            text: "PRIMARY"
                            color: Theme.accent
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fzTiny
                            font.letterSpacing: 0.6
                            font.weight: Font.DemiBold
                        }

                        IconButton {
                            anchors.verticalCenter: parent.verticalCenter
                            icon: "x"
                            iconSize: 12
                            diameter: 22
                            onClicked: root._removeAt(chip.index)
                        }
                    }

                    // One gesture split by how far it travels: a press that stays
                    // put opens the row, a press that moves drags the chip. The
                    // same 5px threshold TrackTable's grip uses, so the two drags
                    // in the interface arm at the same distance.
                    MouseArea {
                        id: chipHover
                        anchors.fill: parent
                        anchors.rightMargin: 26   // leave the remove button its clicks
                        hoverEnabled: true
                        preventStealing: true
                        cursorShape: chip.lifted
                                     ? Qt.ClosedHandCursor
                                     : (chip.entityId > 0
                                        ? Qt.PointingHandCursor : Qt.ArrowCursor)

                        property real pressX: 0
                        property real pressY: 0

                        onPressed: function (mouse) {
                            var g = mapToGlobal(mouse.x, mouse.y)
                            pressX = g.x
                            pressY = g.y
                        }

                        onPositionChanged: function (mouse) {
                            if (!pressed)
                                return
                            var g = mapToGlobal(mouse.x, mouse.y)
                            if (!chip.lifted) {
                                if (Math.abs(g.x - pressX) < 5 && Math.abs(g.y - pressY) < 5)
                                    return
                                if (chipModel.count < 2)
                                    return
                                root._beginDrag(chip.index)
                            }
                            root._dragDX = g.x - pressX
                            root._dragDY = g.y - pressY
                            // Asked in the Flow's coordinates, which is where the
                            // slots the chip could land in are measured.
                            var p = mapToItem(chipFlow, mouse.x, mouse.y)
                            root._dragTo(p.x, p.y)
                        }

                        onReleased: {
                            if (root.dragging)
                                root._endDrag(true)
                            else if (chip.entityId > 0 && containsMouse)
                                root.entityOpened(chip.entityId)
                        }

                        onCanceled: root._endDrag(false)
                    }
                }
            }
        }

        NocturneField {
            id: entry
            width: parent.width
            placeholder: chipModel.count === 0
                         ? root.placeholderEmpty : root.placeholderMore
            clearable: false

            onEdited: root._refreshSuggestions()
            onAccepted: root._commitTyped()

            // A comma is how the old field separated names, so people will type
            // one out of habit. Treat it as "done with this name" rather than
            // letting it into one.
            Keys.onPressed: function (event) {
                if (event.key === Qt.Key_Comma) {
                    root._commitTyped()
                    event.accepted = true
                } else if (event.key === Qt.Key_Backspace && entry.text === ""
                           && chipModel.count > 0) {
                    // Backspace on an empty box removes the last chip, which is
                    // what every other chip input does and what the hand expects.
                    root._removeAt(chipModel.count - 1)
                    event.accepted = true
                }
            }
        }

        // Matching rows already in the library. Shown under the box rather than
        // in a popup so it cannot cover the chips it is about to join.
        Column {
            width: parent.width
            spacing: 1
            visible: root.suggestions.length > 0

            Repeater {
                model: root.suggestions

                delegate: Rectangle {
                    id: suggestion
                    required property var modelData

                    width: parent.width
                    height: 38
                    radius: Theme.radiusSm
                    color: sugHover.containsMouse ? Theme.raised : "transparent"
                    Behavior on color { ColorAnimation { duration: Theme.fast } }

                    Row {
                        anchors.left: parent.left
                        anchors.leftMargin: 8
                        anchors.right: parent.right
                        anchors.rightMargin: 10
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: Theme.gapSm

                        CoverArt {
                            width: 24
                            height: 24
                            radius: width * root.avatarRadiusRatio
                            tier: 128
                            hash: suggestion.modelData[root.imageKey] || ""
                            placeholderText: suggestion.modelData.name
                            showPlaceholderGlyph: false
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: suggestion.modelData.name
                            color: Theme.text
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fzSmall
                        }
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: Fmt.count(suggestion.modelData.trackCount, "track")
                            color: Theme.faint
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fzTiny
                        }
                    }

                    MouseArea {
                        id: sugHover
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root._addChip(suggestion.modelData)
                    }
                }
            }
        }
    }
}
