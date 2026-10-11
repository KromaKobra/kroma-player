import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import Nocturne
import "../controls"

// Copying a collection's audio out to a folder the user owns. Format,
// destination, progress and cancel all in one card, because the whole
// interaction is short enough that sending it through a wizard would be worse
// than the task.
//
// Two ways in, because there are two kinds of subject. A playlist is exported by
// id, so the backend reads its running order itself and the copy stays correct
// even if the list is edited between opening this card and pressing Export. An
// album has no such slot, so it hands over the ids it has — and its name, which
// is what the folder is called either way.
Popup {
    id: root

    // Still `playlistName` after the album learned to export: it is what the
    // card says out loud ("Export “X”", "Files go into a “X” folder"), and both
    // sentences are true of whatever is being copied. Renaming it would touch
    // five bindings to say the same thing.
    property string playlistName: ""
    property int trackCount: 0

    // The parent owns the Toast — a Popup that outlives its own dismissal has
    // nowhere sensible to put one.
    signal notify(string message, bool isError)

    property int _playlistId: -1
    // Null means "this is a playlist, export it by id". A list means these
    // tracks, in this order.
    property var _trackIds: null
    property string _error: ""

    function openFor(playlistId, name, count) {
        _playlistId = playlistId
        _trackIds = null
        playlistName = String(name || "")
        trackCount = count
        _error = ""
        syncFormat()
        open()
    }

    // For an album, or anything else that is a set of tracks rather than a row
    // in the playlists table.
    function openForTracks(ids, name) {
        _playlistId = -1
        _trackIds = ids
        playlistName = String(name || "")
        trackCount = ids ? ids.length : 0
        _error = ""
        syncFormat()
        open()
    }

    // Pill assigns to its own currentIndex when clicked, which would destroy a
    // binding, so the selection is pushed in rather than bound. Same rule the
    // backing-track view documents.
    function syncFormat() {
        var i = exportVM.formats.indexOf(exportVM.format)
        formatPill.currentIndex = i < 0 ? 0 : i
    }

    modal: true
    dim: true
    padding: 0
    // No click-outside while running: the export keeps going either way, and a
    // card that vanishes mid-progress reads as a cancel that did not happen.
    closePolicy: exportVM.isRunning
                 ? Popup.NoAutoClose
                 : (Popup.CloseOnEscape | Popup.CloseOnPressOutside)

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: 480

    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.normal }
            NumberAnimation { property: "scale"; from: 0.94; to: 1
                              duration: Theme.normal; easing.type: Theme.easing }
        }
    }
    exit: Transition {
        NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Theme.fast }
    }

    Overlay.modal: Rectangle {
        color: Theme.scrim
    }

    background: Rectangle {
        color: Theme.dialogBg
        radius: Theme.radiusLg
        border.width: 1
        border.color: Theme.lineHard
    }

    Connections {
        target: exportVM

        function onSettings_changed() {
            root.syncFormat()
        }
        function onError_occurred(message) {
            // Inline while the card is up: a toast belongs to the view behind
            // it, so the modal dim would bury the one message that matters.
            if (root.visible)
                root._error = message
            else
                root.notify(message, true)
        }
        function onExport_finished(exported, skipped, destination) {
            var text = Fmt.count(exported, "track") + " exported"
            if (skipped > 0)
                text += " · " + skipped + " skipped"
            root.notify(text, false)
            root.close()
        }
    }

    FolderDialog {
        id: folderDialog
        title: "Choose where to export"
        onAccepted: {
            // The view model strips the file:// scheme.
            exportVM.destination = selectedFolder
            // Picking a folder is an attempt to fix whatever the last message
            // complained about. Cleared here rather than on settings_changed,
            // which also fires when a run ends — and would wipe its failure.
            root._error = ""
        }
    }

    contentItem: Column {
        id: card
        spacing: Theme.gap
        topPadding: Theme.gapLg
        bottomPadding: Theme.gapLg
        leftPadding: Theme.gapLg
        rightPadding: Theme.gapLg

        // Column padding does not shrink its children, so anything that wants
        // to fill the card has to subtract it by hand.
        property real innerWidth: width - Theme.gapLg * 2

        Text {
            width: card.innerWidth
            text: "Export “" + root.playlistName + "”"
            color: Theme.text
            font.family: Theme.displayFamily
            font.pixelSize: Theme.fzLarge
            font.weight: Font.DemiBold
            elide: Text.ElideRight
        }

        Text {
            width: card.innerWidth
            text: Fmt.count(root.trackCount, "track") + " will be copied out of the library"
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzBody
        }

        // ---------------------------------------------------------- format

        SectionLabel { text: "FORMAT" }

        Row {
            spacing: Theme.gapSm

            Pill {
                id: formatPill
                options: exportVM.formatLabels
                onSelected: function (index) {
                    exportVM.format = exportVM.formats[index]
                    root._error = ""
                }
                Component.onCompleted: root.syncFormat()
            }

            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: exportVM.format === "opus"
                      ? "Copied as-is — no quality lost"
                      : "Transcoded, then re-tagged"
                color: Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }
        }

        Text {
            width: card.innerWidth
            text: root._error !== "" ? root._error : exportVM.unavailableReason
            visible: text !== ""
            color: Theme.danger
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
            wrapMode: Text.WordWrap
        }

        // ----------------------------------------------------- destination

        SectionLabel { text: "DESTINATION" }

        Rectangle {
            width: card.innerWidth
            height: 38
            radius: Theme.radius
            color: Theme.sunken
            border.width: 1
            border.color: Theme.line

            Text {
                anchors.left: parent.left
                anchors.leftMargin: 12
                anchors.right: chooseButton.left
                anchors.rightMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                text: exportVM.destination !== "" ? exportVM.destination : "No folder chosen"
                elide: Text.ElideMiddle
                color: exportVM.destination !== "" ? Theme.dim : Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }

            AppButton {
                id: chooseButton
                anchors.right: parent.right
                anchors.rightMargin: 4
                anchors.verticalCenter: parent.verticalCenter
                text: "Choose…"
                iconName: "folder"
                variant: "ghost"
                fontSize: Theme.fzSmall
                enabledAction: !exportVM.isRunning
                onClicked: folderDialog.open()
            }
        }

        Text {
            width: card.innerWidth
            visible: exportVM.destination !== ""
            text: "Files go into a “" + root.playlistName + "” folder inside it"
            color: Theme.faint
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
            elide: Text.ElideMiddle
        }

        // -------------------------------------------------------- progress

        Rectangle {
            width: card.innerWidth
            height: 46
            radius: Theme.radius
            color: Theme.alpha(Theme.card, 0.72)
            border.width: 1
            border.color: Theme.line
            visible: exportVM.isRunning || exportVM.processed > 0

            Text {
                anchors.left: parent.left
                anchors.leftMargin: 12
                anchors.right: parent.right
                anchors.rightMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                text: (exportVM.stage !== "" ? exportVM.stage + " · " : "")
                      + exportVM.processed + " of " + exportVM.total
                elide: Text.ElideRight
                color: Theme.dim
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }

            Rectangle {
                anchors.left: parent.left
                anchors.bottom: parent.bottom
                anchors.margins: 1
                height: 2
                radius: 1
                color: Theme.accent
                width: (parent.width - 2) * Math.min(1, exportVM.progress / 100)

                Behavior on width { NumberAnimation { duration: Theme.normal } }
            }
        }

        Item { width: 1; height: Theme.gapXs }

        Row {
            anchors.right: parent.right
            anchors.rightMargin: Theme.gapLg
            spacing: Theme.gapSm

            AppButton {
                text: exportVM.isRunning ? "Stop" : "Close"
                variant: "ghost"
                onClicked: {
                    if (exportVM.isRunning)
                        exportVM.cancel()
                    else
                        root.close()
                }
            }

            AppButton {
                text: "Export"
                iconName: "download"
                variant: "filled"
                visible: !exportVM.isRunning
                enabledAction: exportVM.canExport && root.trackCount > 0
                onClicked: {
                    root._error = ""
                    if (root._trackIds)
                        exportVM.exportTracks(root._trackIds, root.playlistName)
                    else
                        exportVM.exportPlaylist(root._playlistId)
                }
            }
        }
    }
}
