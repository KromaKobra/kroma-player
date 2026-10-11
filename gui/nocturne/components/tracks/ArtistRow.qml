import QtQuick
import Nocturne
import "../artwork"

// One artist, drawn as a row in a track list.
//
// Search can return artists interleaved with tracks, and both arrive through one
// model — so this is the other face of TrackTable's delegate rather than a
// component with a list of its own. It draws only the line: the card behind it,
// the hover tint, the selection wash and the context menu all belong to the
// delegate and work the same whichever face is showing.
//
// Deliberately sparse. An artist has no album, no duration, no key and no
// tempo, so rather than a row of blanked-out columns it gets a round portrait,
// a name and a count — which is also what makes it legible as a different kind
// of result while scrolling past at speed.
Item {
    id: root

    property string artName: ""
    property string imageHash: ""
    property int trackCount: 0

    // The shared round mask, supplied by the list. Never left null in a
    // scrolling view: a mask per row is a texture per row, and some of them are
    // not ready when the effect first draws, which leaves a scattering of rows
    // with no artwork at all. See CoverArt.qml.
    property Item artMask: null

    Row {
        anchors.left: parent.left
        anchors.right: badge.left
        anchors.rightMargin: Theme.gapSm
        anchors.verticalCenter: parent.verticalCenter
        spacing: Theme.gap

        // Exactly the track row's artwork slot — same width, same 42px art
        // centred in it — so portraits and covers share one left edge and the
        // titles beside them line up down the list. A track row's number slot
        // is invisible except on the playing row, and an invisible child of a
        // Row takes no space, so matching that slot too would push every artist
        // row 40px right of every track row.
        Item {
            width: 42
            height: root.height

            CoverArt {
                anchors.centerIn: parent
                width: 42
                height: 42
                radius: width / 2
                tier: 128
                maskSource: root.artMask
                hash: root.imageHash
                placeholderText: root.artName
                showPlaceholderGlyph: false
            }
        }

        Column {
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width - parent.spacing - 42
            spacing: 3

            Text {
                width: parent.width
                text: root.artName
                elide: Text.ElideRight
                color: Theme.text
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzMed
                font.weight: Font.DemiBold
            }
            Text {
                width: parent.width
                text: Fmt.count(root.trackCount, "track")
                elide: Text.ElideRight
                color: Theme.dim
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }
        }
    }

    // Says what this row is, because in a mixed list that is the one thing the
    // shape alone cannot settle — a track with no album art and a long title
    // looks much the same from the corner of the eye.
    //
    // Drawn rather than borrowed from Pill: Pill carries a MouseArea per
    // segment, and a decoration that eats the click meant for the row it labels
    // would make the badge the one part of an artist row you cannot press.
    Rectangle {
        id: badge
        anchors.right: parent.right
        anchors.rightMargin: Theme.gapSm
        anchors.verticalCenter: parent.verticalCenter
        width: badgeLabel.implicitWidth + 18
        height: 22
        radius: height / 2
        color: Theme.accentGlow
        border.width: 1
        border.color: Theme.alpha(Theme.accent, 0.35)

        Text {
            id: badgeLabel
            anchors.centerIn: parent
            text: "Artist"
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzTiny
            font.weight: Font.DemiBold
        }
    }
}
