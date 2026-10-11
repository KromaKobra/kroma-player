import QtQuick
import Nocturne

// The tick beside an album's track count: this is the whole record.
//
// One component rather than three inline icons, because the three places it
// appears — the album page, the library wall, an album row in a search — are
// asserting the same thing about the same album, and a mark that meant something
// slightly different in each would be worse than no mark at all.
//
// `Theme.positive` and not the accent. It is the colour this interface already
// uses for "the library has this" — TrackStatusCell paints an `inLibrary` row
// with it, and the import legend the same — so the glyph reads as that same
// answer at album scale rather than as a decoration that follows whatever accent
// the user picked this week.
//
// Collapses to zero width when it is not showing, so the text beside it does not
// sit in a Row with a hole where a checkmark would go.
Item {
    id: root

    property bool complete: false
    property int size: 13

    // Named so a test can find it. A mark that silently stops being drawn is
    // exactly the failure this feature is prone to — it is one binding deep
    // inside three lazily-instantiated delegates — and nothing about the page
    // would look wrong.
    objectName: "completeMark"

    visible: root.complete
    width: root.visible ? root.size : 0
    height: root.size

    Icon {
        anchors.centerIn: parent
        name: "complete"
        size: root.size
        // The weight CheckRow's tick uses. A hairline check at 13px reads as a
        // smudge next to text; this is the same glyph at the same scale.
        strokeWidth: 2.4
        color: Theme.dim
    }

    // Passive by construction: a HoverHandler never takes a press, so the mark
    // can sit inside a grid cell or a list row without becoming the one spot on
    // it that cannot be clicked. The sentence is worth carrying — a bare tick
    // beside a number reads as "verified" to one user and "selected" to another.
    HoverHandler { id: hover }

    NocturneTooltip {
        text: "Every track from this release is here"
        visible: root.complete && hover.hovered
    }
}
