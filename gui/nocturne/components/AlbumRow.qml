import QtQuick
import Nocturne

// One album, drawn as a row in a track list.
//
// The sibling of ArtistRow, and the same bargain: search can return albums, a
// result list is one model, so this is another face of TrackTable's delegate
// rather than a component with a list of its own. It draws only the line — the
// card behind it, the hover tint and the context menu belong to the delegate.
//
// Square where the artist's is round. That one difference is how the interface
// tells a record from a person everywhere else, and it is doing the same work
// here: three kinds of row scroll past in one list, and the shape is what
// separates them before the badge is read.
Item {
    id: root

    property string albumName: ""
    property string coverHash: ""
    property int trackCount: 0
    property bool isComplete: false

    // The shared square mask, supplied by the list — the same one every track
    // row's cover uses, because an album sleeve *is* a cover. Never left null in
    // a scrolling view: a mask per row is a texture per row, and some are not
    // ready when the effect first draws, which leaves a scattering of rows with
    // no artwork at all. See CoverArt.qml.
    property Item artMask: null

    Row {
        anchors.left: parent.left
        anchors.right: badge.left
        anchors.rightMargin: Theme.gapSm
        anchors.verticalCenter: parent.verticalCenter
        spacing: Theme.gap

        // Exactly the track row's artwork slot — same width, same 42px art
        // centred in it — so sleeves, portraits and covers share one left edge
        // and the titles beside them line up down the list.
        Item {
            width: 42
            height: root.height

            CoverArt {
                anchors.centerIn: parent
                width: 42
                height: 42
                radius: 7
                tier: 128
                maskSource: root.artMask
                hash: root.coverHash
                placeholderText: root.albumName
                showPlaceholderGlyph: false
            }
        }

        Column {
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width - parent.spacing - 42
            spacing: 3

            Text {
                width: parent.width
                text: root.albumName
                elide: Text.ElideRight
                color: Theme.text
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzMed
                font.weight: Font.DemiBold
            }
            Row {
                id: countLine
                width: parent.width
                spacing: 5

                CompleteMark {
                    id: rowMark
                    anchors.verticalCenter: parent.verticalCenter
                    complete: root.isComplete
                    size: 12
                }

                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    // What the Row has left. A Row lays an invisible child out
                    // as nothing at all, so the mark's width and the spacing
                    // beside it only come off when there is a mark.
                    width: countLine.width
                           - (rowMark.visible ? rowMark.width + countLine.spacing : 0)
                    text: Fmt.count(root.trackCount, "track")
                    elide: Text.ElideRight
                    color: Theme.dim
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzSmall
                }
            }
        }
    }

    // Says what this row is, because in a mixed list that is the one thing the
    // shape alone cannot settle — a track with square art and a long title looks
    // much the same from the corner of the eye.
    //
    // Drawn rather than borrowed from Pill: Pill carries a MouseArea per
    // segment, and a decoration that eats the click meant for the row it labels
    // would make the badge the one part of an album row you cannot press.
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
            text: "Album"
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzTiny
            font.weight: Font.DemiBold
        }
    }
}
