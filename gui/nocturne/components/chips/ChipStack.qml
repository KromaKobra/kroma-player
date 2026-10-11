import QtQuick
import Nocturne
import "../artwork"

// A row of faces packed so they overlap, each one opening into a full chip
// under the pointer. For "who or what is in here" in a space too narrow to
// print the answer.
//
// A strip of avatar-and-name pills spends nearly all of its room on text: three
// artists fill the gap in an album hero and a compilation is almost entirely
// "+N", so the names that matter most are the ones pushed out. Stacked pictures
// invert that — every entity keeps a visible crescent, and the name is one
// hover away. Leftmost is drawn on top, which is also the order the list is in,
// so the ones the caller thinks matter most are the ones least covered.
//
// Read-only and entity-agnostic. What is artist- or album-specific arrives
// through four properties with the same names and meanings `EntityChips` gives
// them, so the two generics read as one family: `idKey`, `labelKey`,
// `imageKey`, and `avatarRadiusRatio` — 0.5 for a person, a small corner for a
// record, the one number that tells the two apart everywhere in this GUI.
//
// A caller that can say which of its entities matter most says so through
// `prominentKey`, and those draw larger. The ranking is already in the order,
// but an order is exactly what an overlapping strip is worst at showing: past
// the first two, every position looks like the one beside it. Size survives
// being half covered.
//
// It clips rather than wrapping. A stack lives in a band whose height is fixed
// by whatever is beside it, and a long tail — a compilation names thirty
// people — would have had to stop somewhere regardless. It stops here and says
// how many are left rather than pretending there were none.
Item {
    id: root

    // Pre-ordered by the caller and never re-sorted here: the order encodes a
    // ranking this component cannot see, and it is also the layering.
    property var entities: []

    // Which keys on an entity hold its id, its name and its picture's content
    // hash. `artistsVM` says `imageHash`, `albumsVM` says `coverHash`; both
    // point into the same store, which is why one CoverArt serves both.
    property string idKey: "id"
    property string labelKey: "name"
    property string imageKey: "imageHash"

    // How the picture is shaped, as a fraction of its size: 0.5 is the circle a
    // person gets, a small number the square corner a record gets.
    property real avatarRadiusRatio: 0.5

    // Which key marks an entity as one of the important ones, and how much
    // larger that draws it. A stack whose order already ranks its entities says
    // so only by position, and position is the thing an overlap half hides —
    // size is the part of a face you can still read once it is a crescent.
    //
    // Empty by default, which is one uniform circle for everybody: a caller
    // with no such distinction to make gets exactly the component it had, and
    // so does one talking to a backend too old to answer the question.
    property string prominentKey: ""
    property real prominentScale: 1.5

    // `function (entity) -> string`, or null for no tooltip. Whatever the chip
    // deliberately does not print — a track count, a year — belongs here rather
    // than in the label, which has to stay short enough to be worth expanding.
    property var tooltipFn: null

    // A chip was pressed. Opening the thing it names is a navigation the page
    // owns, the way EntityChips leaves it to its page.
    signal entityOpened(int entityId)

    implicitHeight: root.tallest
    clip: true

    // ------------------------------------------------------------- geometry

    // The resting shape, and the one place the stack's proportions live. A chip
    // at rest is a circle: `collapsedWidth` equals `chipHeight`, and the 3px of
    // fill plus the 1px border ringing the 24px avatar is what makes one face
    // legible on top of the next. No new Theme token — the ring is the chip's
    // own body.
    //
    // The ring is the number written down and the face is what is left over,
    // rather than the other way round. At one size that is the same 24px face
    // either way; at two it is the difference between both sizes coming out
    // whole and a 45px chip putting a 4.5px ring around a face on a half pixel.
    // `avatarAt()` below is where the face's own size comes from.
    readonly property int chipHeight: 30
    readonly property int collapsedWidth: 30
    readonly property int avatarInset: 3
    readonly property int overlap: 11

    // How far apart two *plain* collapsed chips start. The Row's spacing is the
    // overlap's negative, so the positioner packs them itself and every chip's
    // own left edge is fixed by the ones before it — which is what stops an
    // expanding chip from moving out from under the pointer that expanded it.
    //
    // With a prominent chip in the row this is no longer one number: the
    // distance to the next chip is the left one's own width less the overlap.
    // The *overlap* stays one number regardless, because a Row's spacing is one
    // number no matter what it separates — which is the whole reason a mixed
    // row needs no new layout mechanism, only arithmetic that adds where it
    // used to multiply. It also means a large chip is covered by the same
    // 11px as a small one, which is proportionally less of it: the face being
    // emphasised is the one least hidden, twice over.
    readonly property int step: root.collapsedWidth - root.overlap

    readonly property int count: root.entities ? root.entities.length : 0

    // What chip *i* measures. Every one of these rounds, so the row is whole
    // pixels throughout: a 45px circle beside a 30px one, never a 44.5px one
    // drawing a half-pixel border against its neighbour.
    //
    // Read off the model each time rather than stored on the delegate, so that
    // the fit arithmetic above can ask about a chip that has not been built —
    // which is most of them, on the strips this component exists for. The
    // reading QML cannot see through is `entity[prominentKey]`, a plain member
    // of a plain object: replace the list and everything re-evaluates, mutate a
    // row inside it and nothing does. That is already true of `hiddenNames()`
    // and of `tooltipFn`, and the view models hand the list over whole.
    function isProminent(i) {
        return root.prominentKey !== ""
            && !!(root.entities[i] || {})[root.prominentKey]
    }

    function scaleAt(i) {
        return root.isProminent(i) ? root.prominentScale : 1
    }

    function widthAt(i) {
        return Math.round(root.collapsedWidth * root.scaleAt(i))
    }

    function heightAt(i) {
        return Math.round(root.chipHeight * root.scaleAt(i))
    }

    // The ring scales and the face takes what is left, which is what keeps a
    // large face exactly centred in a large chip: 3 and 24 in 30, 5 and 35
    // in 45. Scaling the face instead would put it on a half pixel and round
    // the ring unevenly, which shows as a portrait sitting slightly high.
    function insetAt(i) {
        return Math.round(root.avatarInset * root.scaleAt(i))
    }

    function avatarAt(i) {
        return root.heightAt(i) - 2 * root.insetAt(i)
    }

    // The tallest chip in the *list*, not in the part of it that fits. Measured
    // over everything so that narrowing the strip past its last large face
    // cannot change the strip's own height — a band that moved as a window
    // resized, for a reason that had already scrolled out of sight, would be
    // the one part of this the user could notice without being able to explain.
    readonly property int tallest: {
        for (var i = 0; i < root.count; i++)
            if (root.isProminent(i))
                return Math.round(root.chipHeight * root.prominentScale)
        return root.chipHeight
    }

    // What the overflow chip costs, and the reason the fit is arithmetic rather
    // than a fixed point: it is worked out from how many entities there are in
    // total, never from how many are hidden. "+9" is narrower than "+12", so a
    // width measured off the hidden count would be an input to the very number
    // it depends on.
    //
    // Sized for the widest it could be — everything hidden — which overshoots by
    // a few pixels when only one or two are. That is the right direction to be
    // wrong in: the alternative is a chip laid out wider than the room reserved
    // for it, which the clip would cut in half.
    readonly property int overflowWidth: 22 + 8 * String(root.count).length

    // How wide the first *n* collapsed chips lay out, overlap included. A sum
    // rather than a multiplication, because two chips need not be the same
    // width any more — and the same answer as the multiplication when they are.
    function extent(n) {
        if (n <= 0)
            return 0
        var total = 0
        for (var i = 0; i < n; i++)
            total += root.widthAt(i)
        return total - root.overlap * (n - 1)
    }

    // How many chips are drawn. A binding, and that is the whole simplification
    // this component is: a collapsed chip's width is known from the model
    // alone, so the answer never depends on measuring the delegates the same
    // pass produces. What used to be a deferred relayout — Qt.callLater, a null
    // `itemAt` on the last index, a resize showing a count decided for the old
    // width for one frame — is arithmetic that cannot be out of date.
    //
    // A loop rather than the division it used to be. Dividing the room by one
    // step assumed every chip cost the same; walking the list costs a few
    // iterations of nothing and asks each chip what it actually takes.
    readonly property int shown: {
        if (root.count === 0 || root.width <= 0)
            return 0
        // The whole list first. It would be wrong to spend room on an overflow
        // chip that is not going to be needed, which is what a strip that always
        // held its width back would do on exactly the albums where everything
        // fitted.
        if (root.extent(root.count) <= root.width)
            return root.count
        // What is left for faces once the overflow chip has taken its width,
        // less the crescent it spends sitting on the last of them.
        var room = root.width - root.overflowWidth + root.overlap
        var used = 0
        var fit = 0
        // Never the last one: reaching it would mean everything fitted, which
        // the whole-list test above has already ruled out.
        for (var i = 0; i < root.count - 1; i++) {
            var grown = used + root.widthAt(i) - (i > 0 ? root.overlap : 0)
            if (grown > room)
                break
            used = grown
            fit = i + 1
        }
        return fit
    }

    readonly property int hidden: root.count - root.shown

    // The ones that did not fit, by name. "+3" on its own tells you only that
    // you are missing something.
    function hiddenNames() {
        var names = []
        for (var i = root.shown; i < root.count; i++)
            names.push(root.entities[i][root.labelKey])
        return names.join(", ")
    }

    // ------------------------------------------------------------------ view

    Row {
        // Named so a test can walk it. A Repeater's delegates are not QObject
        // children of anything findChild reaches, so the only way to ask what
        // was actually laid out — as opposed to what `shown` claims — is to
        // enumerate this positioner's visual children.
        objectName: "contributorRow"
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter

        // The overlap, and the whole layout mechanism. A Row re-positions its
        // children whenever one of them changes width, so the push-right when a
        // chip expands falls out of that chip's own width animation for free and
        // stays exactly in step with it.
        spacing: -root.overlap

        // Deliberately no `move` Transition, unlike the Flow in EntityChips.
        // That one is displaced by model splices and needs to animate its way
        // there; here the displacement *is* an animation already, and a second
        // one on top would lag a frame behind the chip causing it.

        Repeater {
            id: chipRepeater
            model: root.entities

            // Built for everyone, drawn only while it fits. A Row skips an
            // invisible child, so hiding one also closes the gap it left.
            delegate: Rectangle {
                id: chip
                objectName: "contributorChip"
                required property int index
                required property var modelData

                visible: chip.index < root.shown

                // What this chip measures, asked once. A prominent one is the
                // same chip throughout — the picture, the ring around it and the
                // pill they sit in all take the scale, so what grows is the
                // whole face rather than a picture rattling in a border sized
                // for a smaller one.
                readonly property real chipScale: root.scaleAt(chip.index)
                readonly property int restWidth: root.widthAt(chip.index)
                readonly property int inset: root.insetAt(chip.index)
                readonly property int avatar: root.avatarAt(chip.index)

                // Heights differ in one Row now, and a Row positions in x only.
                anchors.verticalCenter: parent.verticalCenter

                // Hover is Qt's own, per chip, because Qt's hit testing is
                // already z-aware and that is exactly the behaviour wanted: chip
                // *i* is hoverable on the crescent chip *i-1* does not cover,
                // which is the part of it the user can see. One root-level
                // handler doing the arithmetic would only re-derive this, worse.
                readonly property bool expanded: chipHover.containsMouse

                // The pill this chip opens into — the same width the old
                // full-time chip had. The 7 is the room after the name; the
                // room before the picture is the ring's own, so a large chip is
                // padded in proportion at the end it grew from and identically
                // at the end the text runs out.
                readonly property real fullWidth: chipRow.implicitWidth + chip.inset + 7

                // Growing rightward from a left edge the Row has already fixed.
                // Nothing about this chip's position depends on whether it is
                // hovered, so an expansion cannot move the chip out from under
                // the cursor and there is no loop to fall into. Crossing onto a
                // neighbour collapses this one and slides that neighbour back
                // toward the cursor — a settle, and it settles on the chip the
                // pointer is over.
                width: chip.expanded ? chip.fullWidth : chip.restWidth
                Behavior on width {
                    NumberAnimation { duration: Theme.normal; easing.type: Theme.easing }
                }

                height: root.heightAt(chip.index)
                radius: height / 2
                // So a half-grown chip does not spill the name it has not made
                // room for yet. Safe over the avatar, which rounds itself
                // through MultiEffect rather than by clipping.
                clip: true

                // Leftmost on top, descending rightward, and the hovered one
                // above everything. This is the whole hit-testing story too: Qt
                // tests in reverse paint order, so a press in the overlap lands
                // on the chip drawn over it — the one being pointed at.
                z: chip.expanded ? root.count + 1 : root.count - chip.index

                color: chip.expanded ? Theme.raised : Theme.sunken
                border.width: 1
                border.color: Theme.line
                Behavior on color { ColorAnimation { duration: Theme.fast } }

                // Pushing right inside a clipped strip slides the tail under the
                // clip, which is fine — the tail is the part already being
                // hidden. The hovered chip being cut in half when it is the last
                // one is not, so it gets nudged back left.
                //
                // Capped at the growth itself, so the expanded chip always
                // strictly contains the collapsed one it grew from. That cap is
                // what makes the nudge safe: the cursor was inside the collapsed
                // chip, so it is still inside the expanded one, and a hover can
                // never be lost to the very motion it caused.
                //
                // A transform rather than an `x`, for the reason EntityChips
                // lifts a dragged chip the same way: assigning x would clobber
                // the positioner's own binding and the chip would never find its
                // way back into the row.
                readonly property real overhang:
                    Math.max(0, chip.x + chip.fullWidth - root.width)
                transform: Translate {
                    x: chip.expanded
                       ? -Math.min(chip.overhang, chip.fullWidth - chip.restWidth)
                       : 0
                    Behavior on x {
                        NumberAnimation { duration: Theme.normal; easing.type: Theme.easing }
                    }
                }

                Row {
                    id: chipRow
                    anchors.left: parent.left
                    anchors.leftMargin: chip.inset
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 6

                    CoverArt {
                        // Named so a test can measure the face rather than the
                        // chip around it: the ring is the one piece of this
                        // arithmetic with nothing else to check it.
                        objectName: "contributorAvatar"
                        anchors.verticalCenter: parent.verticalCenter
                        width: chip.avatar
                        height: chip.avatar
                        radius: width * root.avatarRadiusRatio
                        tier: 128
                        hash: chip.modelData[root.imageKey] || ""
                        placeholderText: chip.modelData[root.labelKey] || ""
                        showPlaceholderGlyph: false
                    }

                    Text {
                        objectName: "contributorLabel"
                        anchors.verticalCenter: parent.verticalCenter
                        text: chip.modelData[root.labelKey] || ""
                        color: Theme.text
                        font.family: Theme.fontFamily
                        // A step up on a prominent chip rather than a scale of
                        // its own: two sizes across the strip, both of them
                        // already in the theme, and the larger name reads as
                        // the emphasis it is instead of as a zoom of the
                        // smaller one.
                        font.pixelSize: chip.chipScale > 1 ? Theme.fzBody : Theme.fzSmall

                        // Shorter than the slide, so the name lands once there is
                        // room for it rather than smearing across the growth.
                        opacity: chip.expanded ? 1 : 0
                        Behavior on opacity { NumberAnimation { duration: Theme.fast } }
                    }
                }

                // Named so a test can hover a chip by sending this one item an
                // event, rather than by driving a pointer across a window it
                // would first have to show.
                MouseArea {
                    id: chipHover
                    objectName: "contributorHover"
                    anchors.fill: parent
                    hoverEnabled: true
                    acceptedButtons: Qt.LeftButton
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.entityOpened(chip.modelData[root.idKey])
                }

                NocturneTooltip {
                    text: root.tooltipFn ? root.tooltipFn(chip.modelData) : ""
                    visible: chipHover.containsMouse && text !== ""
                }
            }
        }

        Rectangle {
            id: overflowChip
            objectName: "contributorOverflow"
            visible: root.hidden > 0
            width: root.overflowWidth
            // Always the plain size, whatever it stands beside. It is a count
            // of what is missing rather than one of the faces, and a large "+7"
            // would be claiming the emphasis the large chips are for.
            height: root.chipHeight
            anchors.verticalCenter: parent.verticalCenter
            radius: height / 2
            // Under the last face, the way every chip is under the one before
            // it: the stack keeps reading left to right right to its end.
            z: 0
            color: overflowHover.hovered ? Theme.raised : Theme.sunken
            border.width: 1
            border.color: Theme.line
            Behavior on color { ColorAnimation { duration: Theme.fast } }

            Text {
                anchors.centerIn: parent
                // Centred in the part of the chip that is not under the last
                // face, rather than in the chip. The count is the one thing in
                // the stack that has to be read rather than recognised, and a
                // "+2" tucked against the crescent beside it reads as crowded.
                // Nothing covers it when it stands alone, so neither does this.
                anchors.horizontalCenterOffset: root.shown > 0 ? root.overlap / 2 : 0
                text: "+" + root.hidden
                // Faint, not text: this is a count of what is missing rather
                // than a name, and it should not compete with the pictures that
                // did fit.
                color: Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }

            HoverHandler { id: overflowHover }

            NocturneTooltip {
                text: root.hiddenNames()
                visible: overflowChip.visible && overflowHover.hovered
            }
        }
    }
}
