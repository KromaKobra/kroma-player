import QtQuick
import Nocturne

// The mark beside a track count meaning "we know which release this is" — an
// enabled plugin has identified the artist or album, and it carries that
// plugin's id.
//
// It used to be a sentence: "12 tracks  ·  Identified on …", tacked onto the
// count line. That is a lot of width for a fact the user only needs at a
// glance, and it does not survive being put next to a count and a duration.
//
// What is drawn is the plugin's own picture for that kind of id when it ships
// one, and a chain link when it does not. Where it was identified is the
// tooltip, in the plugin's words — `label` — so this file names no source.
//
// `Theme.accentAlt`, deliberately not the `Theme.positive` that [CompleteMark]
// uses. The two sit side by side and answer different questions — one is about
// what the library holds, the other about what the library knows — and two ticks
// in the same green would read as one fact stated twice.
//
// Collapses to zero width when it is not showing, so the text beside it does not
// sit in a Row with a hole where a glyph would go.
Item {
    id: root

    // Where it was identified, as the plugin names it ("" — not identified).
    property string label: ""
    // The plugin's picture for the kind, as an `image://plugin/…` URL, or "".
    property string iconSource: ""
    property int size: 13

    readonly property bool identified: root.label !== ""

    // Named so a test can find it, for the reason CompleteMark is: a mark that
    // silently stops being drawn is one binding deep inside a page that would
    // still look entirely correct without it.
    objectName: "identifiedMark"

    visible: root.identified
    width: root.visible ? root.size : 0
    height: root.size

    Icon {
        anchors.centerIn: parent
        visible: root.iconSource === ""
        name: "link"
        size: root.size
        color: Theme.dim
    }

    Image {
        anchors.fill: parent
        visible: root.iconSource !== ""
        source: root.iconSource
        sourceSize: Qt.size(root.size * 2, root.size * 2)
        fillMode: Image.PreserveAspectFit
    }

    // Passive by construction — a HoverHandler never takes a press — so the mark
    // can sit inside a row without becoming the one spot on it that cannot be
    // clicked. Same bargain CompleteMark makes.
    HoverHandler { id: hover }

    NocturneTooltip {
        text: "Identified on " + root.label
        visible: root.identified && hover.hovered
    }
}
