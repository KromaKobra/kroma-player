import QtQuick
import Nocturne
import "../artwork"
import "../chips"
import "../controls"

// The banner at the top of a collection's own page — a playlist, an artist, an
// album. One band, one set of controls, three callers.
//
// It began as the playlist page's hero and stayed there for a while, which left
// the artist and album pages on an older, flatter arrangement: a ViewHeader full
// of text buttons and a small portrait that scrolled away with the list. Three
// pages that are the same kind of page read as three different products. This is
// that hero, lifted out, so there is exactly one of it.
//
// Purely presentational by construction: it takes values and emits intentions,
// and never names a view model. That is what lets the album page bind
// `albumTracksVM` and the artist page bind a TrackQuery into the same component
// without either of them knowing about the other.
Item {
    id: root

    // What it is, in small caps above the name — "PLAYLIST", "ARTIST", "ALBUM".
    property string kicker: ""
    property string title: ""
    property string coverHash: ""

    // A square sleeve for a record, a circle for a person. That is the whole
    // visual difference between the three pages, and it is one number.
    property real artRadius: Theme.radiusLg
    // The placeholder's disc glyph belongs to artwork, not to a face; the
    // artist page turns it off and keeps only the initial.
    property bool artGlyph: true

    property int trackCount: 0
    property int totalDuration: 0

    // Who is on it, as chips beside the count: `[{artistId, name, imageHash,
    // trackCount, isMain}]`. Empty for the artist and playlist pages, which
    // have no such question to answer — a playlist is not a thing anybody is
    // *on*, and an artist page would be naming the artist you are already
    // looking at.
    property var contributors: []
    signal contributorClicked(int artistId)

    // The two marks that ride beside the count. Both collapse when off.
    property bool complete: false
    // Where the page's artist or album is identified, in its plugin's words,
    // and that plugin's picture for it. "" — not identified, and no mark.
    property string identifiedLabel: ""
    property string identifiedIcon: ""

    property bool canPlay: false
    property bool canShuffle: false

    property bool showExport: false
    property bool canExport: false

    // An amber pip on the overflow button. Set when something on this page
    // needs the user's attention and the thing that fixes it is in the menu —
    // a menu is a place you have to think to open, so it has to ask.
    property bool menuAlert: false
    property string menuAlertTooltip: ""

    property string backTooltip: "Back"

    signal backRequested()
    signal playRequested()
    signal shuffleRequested()
    signal exportRequested()
    signal menuRequested()

    // Shuffle is a GUI-side shuffle — the backend has no shuffle mode, only a
    // queue — so the three pages each had their own copy of this. One copy, on
    // the component whose button asks for it.
    function shuffled(ids) {
        for (var i = ids.length - 1; i > 0; i--) {
            var j = Math.floor(Math.random() * (i + 1))
            var t = ids[i]; ids[i] = ids[j]; ids[j] = t
        }
        return ids
    }

    height: 232
    clip: true

    AmbientBackdrop {
        anchors.fill: parent
        hash: root.coverHash
        intensity: 0.6
    }

    // Right-click anywhere on the band gets the same menu the overflow button
    // gives. Taking only the right button leaves every left-click to the
    // controls stacked above this, and their own MouseAreas take only the left
    // button, so a right-click over one falls through to here.
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.RightButton
        onClicked: root.menuRequested()
    }

    IconButton {
        anchors.left: parent.left
        anchors.leftMargin: Theme.gapLg
        anchors.top: parent.top
        anchors.topMargin: Theme.gap
        icon: "chevronLeft"
        iconSize: 19
        diameter: 34
        tooltip: root.backTooltip
        onClicked: root.backRequested()
    }

    Row {
        anchors.left: parent.left
        anchors.leftMargin: Theme.gapXl
        anchors.right: parent.right
        anchors.rightMargin: Theme.gapXl
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Theme.gapLg
        spacing: Theme.gapLg

        CoverArt {
            id: heroArt
            width: 152
            height: 152
            radius: root.artRadius
            tier: 512
            hash: root.coverHash
            placeholderText: root.title
            showPlaceholderGlyph: root.artGlyph
        }

        Column {
            anchors.bottom: parent.bottom
            width: parent.width - heroArt.width - Theme.gapLg
            spacing: 8

            Text {
                text: root.kicker
                color: Theme.accentAlt
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzMicro
                font.weight: Font.DemiBold
                font.letterSpacing: 1.6
            }

            Text {
                width: parent.width
                text: root.title
                elide: Text.ElideRight
                color: Theme.text
                font.family: Theme.displayFamily
                font.pixelSize: Theme.fzHero
                font.weight: Font.DemiBold
                font.letterSpacing: -1
            }

            // Transport on the left, export and the overflow menu on the right.
            // The wrapper exists only so those can anchor to the far edge
            // without the Row stretching the buttons apart.
            Item {
                width: parent.width
                height: transportRow.height

                Row {
                    id: transportRow
                    spacing: Theme.gapSm

                    AppButton {
                        text: "Play"
                        iconName: "play"
                        variant: "filled"
                        enabledAction: root.canPlay
                        onClicked: root.playRequested()
                    }
                    AppButton {
                        text: "Shuffle"
                        iconName: "shuffle"
                        variant: "soft"
                        enabledAction: root.canShuffle
                        onClicked: root.shuffleRequested()
                    }

                    // The count, and the two things that can be said about it.
                    // Tighter than the row's own spacing so the marks read as
                    // belonging to the number rather than as two more controls.
                    Row {
                        spacing: 6

                        AppButton {
                            text: Fmt.count(root.trackCount, "track")
                                  + (root.trackCount > 0
                                     ? " · " + Fmt.longDuration(root.totalDuration) : "")
                            variant: "ghost"
                            enabledAction: false
                        }

                        CompleteMark {
                            anchors.verticalCenter: parent.verticalCenter
                            complete: root.complete
                        }

                        IdentifiedMark {
                            anchors.verticalCenter: parent.verticalCenter
                            label: root.identifiedLabel
                            iconSource: root.identifiedIcon
                        }
                    }
                }

                // The credits, in whatever room is left between the transport
                // and the buttons on the far edge.
                //
                // Anchored between the two rows rather than added as a fourth
                // child of `transportRow`: that Row sizes itself to its
                // children, so a child whose width came from the leftover space
                // would be contributing to the width it was measuring. Anchoring
                // left-to-right gives the strip exactly the gap without either
                // row knowing it is there, which is also why the band's height
                // never moves.
                ContributorChips {
                    anchors.left: transportRow.right
                    anchors.leftMargin: Theme.gapLg
                    anchors.right: rightRow.left
                    anchors.rightMargin: Theme.gapLg
                    anchors.verticalCenter: transportRow.verticalCenter

                    // A narrow window leaves a gap that is nearly nothing, and a
                    // strip showing only "+7" is worse than no strip.
                    visible: width > 40

                    contributors: root.contributors
                    onArtistOpened: function (id) { root.contributorClicked(id) }
                }

                Row {
                    id: rightRow
                    anchors.right: parent.right
                    anchors.verticalCenter: transportRow.verticalCenter
                    spacing: Theme.gapXs

                    AppButton {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Export"
                        iconName: "download"
                        variant: "soft"
                        visible: root.showExport
                        enabledAction: root.canExport
                        onClicked: root.exportRequested()
                    }

                    Item {
                        anchors.verticalCenter: parent.verticalCenter
                        width: 32
                        height: 32

                        IconButton {
                            anchors.fill: parent
                            icon: "more"
                            iconSize: 18
                            diameter: 32
                            tooltip: "More"
                            onClicked: root.menuRequested()
                        }

                        // Straddles the button's corner rather than sitting
                        // beside it: the point being made is that *this button*
                        // is where the answer is, and a pip floating next to it
                        // would be a second thing to read. The bed is the page
                        // background, which is what the hero's scrim is already
                        // pushing the artwork toward, so the glyph keeps its
                        // contrast over a bright cover.
                        Rectangle {
                            id: alertPip
                            objectName: "menuAlertPip"
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.rightMargin: -2
                            anchors.topMargin: -2
                            width: 16
                            height: 16
                            radius: width / 2
                            color: Theme.bg
                            visible: root.menuAlert

                            Icon {
                                anchors.centerIn: parent
                                name: "warning"
                                size: 11
                                strokeWidth: 2.0
                                color: Theme.warning
                            }

                            HoverHandler { id: alertHover }

                            NocturneTooltip {
                                text: root.menuAlertTooltip
                                visible: alertPip.visible && alertHover.hovered
                                        && root.menuAlertTooltip !== ""
                            }
                        }
                    }
                }
            }
        }
    }
}
