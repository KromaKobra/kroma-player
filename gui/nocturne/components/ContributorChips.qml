import QtQuick
import Nocturne

// Who is on a record, as a stack of faces, in the hero beside the track count.
//
// An album has no artist of its own — the backend is emphatic about why it must
// not grow one — so the only way to answer "who is on this" was to read the
// artist column down the whole track list and hold the tally in your head. This
// is that tally, arranged the way the record itself argues for: most tracks
// first, and when two people did the same amount of work, whoever the record
// introduces first. That order is also the layering, so the artist who did most
// is the one least covered.
//
// Order alone was not enough on the records this exists for. A release with two
// leads and a dozen guests put the leads first and then drew them exactly like
// the guests, in a strip where every position past the second looks like its
// neighbour — so anyone over half the tracks now draws half again as large. The
// backend decides who that is; the size is this file's answer to it.
//
// The machinery is `ChipStack`; what belongs here is the half that is about an
// album's artists — which key holds the id, round pictures because these are
// people, and a track count in the tooltip that the chip deliberately does not
// print. A row of names with a number after each is a table nobody asked for,
// and the order already says who did most; the tooltip is for when you want to
// know by how much.
//
// `ChipStack` clips rather than wrapping, and in this hero that is the
// load-bearing half of the bargain: the band is shared with the artist and the
// playlist pages, so a strip that grew would move two pages this feature has
// nothing to do with.
//
// Read-only, and a separate file from `EntityChips` for that reason. That one is
// a field you *edit* — a text input, a suggestions dropdown, drag-to-reorder, a
// remove button on every chip — and the drag geometry alone is most of its
// length. A `readOnly` flag there would leave all of it alive behind a
// condition. What the two genuinely shared was a chip's look, and that is no
// longer duplicated either: `ChipStack` owns it, and stands in the same relation
// to this file as `EntityChips` does to `ArtistChips`.
Item {
    id: root

    // `[{ artistId, name, imageHash, trackCount, isMain }]`, most tracks first.
    // The order is the backend's and is never re-sorted here: it encodes a
    // tie-break this component cannot see.
    property var contributors: []

    // A chip was pressed. Opening the artist is a navigation the page owns, the
    // way EntityChips leaves it to its page.
    signal artistOpened(int artistId)

    // Named so a test can find it. The failure this is prone to is the strip
    // silently measuring to zero and drawing nothing, which looks exactly like
    // an album with no credits.
    objectName: "contributorChips"

    implicitHeight: stack.implicitHeight

    // Forwarded rather than reached through, the way ArtistChips forwards
    // `chipCount` and `labelWidth`: a caller — the hero, or a test pinning the
    // arithmetic at a width it chooses — talks to this component and not to its
    // insides.
    readonly property int count: stack.count
    readonly property int shown: stack.shown
    readonly property int hidden: stack.hidden
    readonly property int overflowWidth: stack.overflowWidth
    readonly property int collapsedWidth: stack.collapsedWidth
    readonly property int step: stack.step
    readonly property real prominentScale: stack.prominentScale

    ChipStack {
        id: stack
        anchors.fill: parent

        entities: root.contributors
        idKey: "artistId"
        imageKey: "imageHash"
        // Who made the record, as opposed to who visited it: the backend's
        // `isMain` is an artist on strictly more than half the tracks. A
        // compilation has none and every face is the same size; a record with
        // two leads and fourteen guests draws the two large, at the head of the
        // strip, where the order had already put them and where the overlap was
        // busy hiding that.
        prominentKey: "isMain"
        // A circle, because these are people. It is the same one number that
        // tells an artist chip from an album one everywhere else in this GUI.
        avatarRadiusRatio: 0.5

        tooltipFn: function (entity) {
            return Fmt.count(entity.trackCount, "track") + " on this album"
        }

        onEntityOpened: function (id) { root.artistOpened(id) }
    }
}
