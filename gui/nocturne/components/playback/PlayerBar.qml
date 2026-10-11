import QtQuick
import Nocturne
import "../artwork"

// Persistent transport. Three zones: what is playing, how to control it, and
// the panel toggles.
//
// There are deliberately no shuffle or repeat buttons — the Kroma backend
// exposes no such modes, and a control that silently does nothing is worse than
// no control. "Shuffle all" lives in the Library view, where it can genuinely
// shuffle the queue it builds.
Rectangle {
    id: root

    property bool infoOpen: false
    property bool lyricsAvailable: false

    signal infoToggled()
    signal artworkClicked()

    // The backing-track toggle reports upward like everything else here — this
    // bar never reaches into the window itself.
    signal backingToggled()
    signal backingGenerateRequested()

    implicitHeight: Theme.playerHeight
    color: Theme.alpha(Theme.barBg, 0.94)

    // Scrubbing shows the dragged time rather than the playing time.
    property real scrubPreview: -1
    readonly property real shownPosition: scrubPreview >= 0 ? scrubPreview : playerVM.position

    // Chrome, not background: a press that misses a control here must not drop
    // the track selection. First child, so it is behind every control in the
    // bar and only ever sees what they did not want. See the ambient catcher in
    // main.qml.
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
    }

    Rectangle {
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 1
        color: Theme.line
    }

    // ------------------------------------------------------------ now playing

    Item {
        id: nowPlaying
        anchors.left: parent.left
        anchors.leftMargin: Theme.gapLg
        anchors.verticalCenter: parent.verticalCenter
        width: Math.max(180, Math.min(320, root.width * 0.26))
        height: 56

        CoverArt {
            id: barArt
            width: 56
            height: 56
            radius: Theme.radiusSm
            tier: 128
            anchors.verticalCenter: parent.verticalCenter
            hash: playerVM.current_track_cover_hash
            placeholderText: playerVM.has_current_track ? playerVM.current_track_title : ""

            MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.artworkClicked()

                Rectangle {
                    anchors.fill: parent
                    radius: barArt.radius
                    color: Theme.imageScrim
                    opacity: parent.containsMouse ? 1 : 0
                    visible: opacity > 0
                    Behavior on opacity { NumberAnimation { duration: Theme.fast } }

                    Icon {
                        anchors.centerIn: parent
                        name: "chevronDown"
                        size: 18
                        color: Theme.imageInk
                        rotation: 180
                    }
                }
            }
        }

        Column {
            anchors.left: barArt.right
            anchors.leftMargin: Theme.gap
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: 3

            Text {
                width: parent.width
                text: playerVM.has_current_track ? playerVM.current_track_title : "Nothing playing"
                elide: Text.ElideRight
                color: playerVM.has_current_track ? Theme.text : Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzBody
                font.weight: Font.DemiBold
            }

            Text {
                width: parent.width
                text: playerVM.has_current_track ? playerVM.current_track_artist : ""
                visible: text !== ""
                elide: Text.ElideRight
                color: Theme.dim
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }
        }
    }

    // -------------------------------------------------------------- transport

    Item {
        id: centre
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.verticalCenter: parent.verticalCenter
        width: Math.min(560, root.width * 0.44)
        height: 60

        Row {
            id: buttons
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            spacing: Theme.gapSm

            IconButton {
                anchors.verticalCenter: parent.verticalCenter
                icon: "prev"
                iconSize: 17
                diameter: 34
                tooltip: "Previous"
                enabledAction: playerVM.has_current_track
                onClicked: playerVM.previous()
            }

            // Primary action: filled accent circle.
            Item {
                width: 42
                height: 42
                anchors.verticalCenter: parent.verticalCenter

                Rectangle {
                    id: playCircle
                    anchors.fill: parent
                    radius: width / 2
                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop { position: 0.0; color: Theme.accent }
                        GradientStop { position: 1.0; color: Theme.accentAlt }
                    }
                    scale: playMouse.pressed ? 0.93 : (playMouse.containsMouse ? 1.06 : 1.0)
                    Behavior on scale {
                        NumberAnimation { duration: Theme.fast; easing.type: Theme.easing }
                    }
                }

                Icon {
                    anchors.centerIn: parent
                    name: playerVM.is_playing ? "pause" : "play"
                    size: 18
                    color: Theme.accentInk
                    // The play triangle reads as off-centre when optically centred.
                    anchors.horizontalCenterOffset: playerVM.is_playing ? 0 : 1
                    scale: playCircle.scale
                }

                MouseArea {
                    id: playMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: playerVM.toggle_playback()
                }

                NocturneTooltip {
                    text: playerVM.is_playing ? "Pause" : "Play"
                    visible: playMouse.containsMouse
                }
            }

            IconButton {
                anchors.verticalCenter: parent.verticalCenter
                icon: "next"
                iconSize: 17
                diameter: 34
                tooltip: "Next"
                enabledAction: playerVM.has_current_track || queueVM.rowCount() > 0
                onClicked: playerVM.next()
            }
        }

        Row {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            spacing: Theme.gapSm

            Text {
                anchors.verticalCenter: parent.verticalCenter
                width: 38
                horizontalAlignment: Text.AlignRight
                text: Fmt.duration(root.shownPosition)
                color: Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzTiny
                font.features: ({ "tnum": 1 })
            }

            SeekBar {
                width: parent.width - 38 * 2 - Theme.gapSm * 2
                anchors.verticalCenter: parent.verticalCenter
                position: root.shownPosition
                duration: playerVM.duration
                interactive: playerVM.has_current_track
                onScrubbing: function (seconds) { root.scrubPreview = seconds }
                onSeeked: function (seconds) {
                    root.scrubPreview = -1
                    playerVM.seek(seconds)
                }
            }

            Text {
                anchors.verticalCenter: parent.verticalCenter
                width: 38
                text: Fmt.duration(playerVM.duration)
                color: Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzTiny
                font.features: ({ "tnum": 1 })
            }
        }
    }

    // ----------------------------------------------------------------- right

    Row {
        anchors.right: parent.right
        anchors.rightMargin: Theme.gapLg
        anchors.verticalCenter: parent.verticalCenter
        spacing: Theme.gapXs

        // Backing track. Lit when the instrumental is playing; pressing it with
        // nothing to switch to is not a dead end — it offers to make one.
        IconButton {
            anchors.verticalCenter: parent.verticalCenter
            icon: "mic"
            iconSize: 18
            diameter: 32
            active: playerVM.backingTrackEnabled && playerVM.currentTrackHasBacking
            enabledAction: playerVM.has_current_track
            tooltip: !playerVM.has_current_track ? "Backing track"
                   : playerVM.currentTrackHasBacking
                     ? (playerVM.backingTrackEnabled ? "Switch to the mix"
                                                     : "Switch to the backing track")
                     : "Generate a backing track"
            onClicked: {
                if (playerVM.currentTrackHasBacking)
                    root.backingToggled()
                else
                    root.backingGenerateRequested()
            }
        }

        IconButton {
            anchors.verticalCenter: parent.verticalCenter
            icon: "info"
            iconSize: 18
            diameter: 32
            active: root.infoOpen
            tooltip: "Track details"
            onClicked: root.infoToggled()
        }

        VolumeControl {
            anchors.verticalCenter: parent.verticalCenter
            volume: playerVM.volume
            onVolumeRequested: function (v) { playerVM.set_volume(v) }
        }
    }
}
