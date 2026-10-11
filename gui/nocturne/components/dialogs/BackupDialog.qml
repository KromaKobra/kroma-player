import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import Nocturne
import "../controls"
import "../settings"

// Copying the whole vault to a second location, incrementally.
//
// Two screens in one card, because they are two halves of one decision: pick a
// folder and what to include, then read what that would actually do before any
// of it happens. The pause between them is the point — a vault names its audio
// by track id alone, so pointing a sync at somebody else's backup would
// overwrite it file for file without either side looking wrong.
Popup {
    id: root

    // The parent owns the Toast — a Popup that outlives its own dismissal has
    // nowhere sensible to put one.
    signal notify(string message, bool isError)

    property string _error: ""
    property string _folder: backupVM.destination

    function openDialog() {
        _error = ""
        _folder = backupVM.destination
        backupVM.reset()
        open()
    }

    readonly property bool _reviewing: backupVM.phase === "review"
    readonly property bool _finished: backupVM.phase === "done"

    modal: true
    dim: true
    padding: 0
    // No click-outside while running: the backup keeps going either way, and a
    // card that vanishes mid-write reads as a cancel that did not happen.
    closePolicy: backupVM.isRunning
                 ? Popup.NoAutoClose
                 : (Popup.CloseOnEscape | Popup.CloseOnPressOutside)

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: 540

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
        target: backupVM

        function onError_occurred(message) {
            // Inline while the card is up: a toast belongs to the view behind
            // it, so the modal dim would bury the one message that matters.
            if (root.visible)
                root._error = message
            else
                root.notify(message, true)
        }
        function onBackup_finished(written, deleted, destination) {
            var text = Fmt.count(written, "file") + " backed up"
            if (deleted > 0)
                text += " · " + Fmt.count(deleted, "removed", "removed")
            root.notify(text, false)
        }
        function onSettings_changed() {
            root._folder = backupVM.destination
        }
    }

    FolderDialog {
        id: folderDialog
        title: "Choose a backup folder"
        onAccepted: {
            // The view model resolves this: an existing backup inside the
            // chosen folder is selected rather than a second one proposed
            // beside it.
            root._folder = backupVM.chooseFolder(selectedFolder)
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
            text: root._finished ? "Backup complete" : "Back up your library"
            color: Theme.text
            font.family: Theme.displayFamily
            font.pixelSize: Theme.fzLarge
            font.weight: Font.DemiBold
            elide: Text.ElideRight
        }

        Text {
            width: card.innerWidth
            visible: !root._reviewing && !root._finished
            text: "Only what has changed since the last backup gets written."
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzBody
            wrapMode: Text.WordWrap
        }

        // ----------------------------------------------------- destination

        SectionLabel {
            text: "BACKUP FOLDER"
            visible: !root._finished
        }

        Rectangle {
            width: card.innerWidth
            height: 38
            radius: Theme.radius
            color: Theme.sunken
            border.width: 1
            border.color: Theme.line
            visible: !root._finished

            Text {
                anchors.left: parent.left
                anchors.leftMargin: 12
                anchors.right: chooseButton.left
                anchors.rightMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                text: root._folder !== "" ? root._folder : "No folder chosen"
                elide: Text.ElideMiddle
                color: root._folder !== "" ? Theme.dim : Theme.faint
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
                enabledAction: !backupVM.isRunning
                onClicked: folderDialog.open()
            }
        }

        // --------------------------------------------------------- options

        SectionLabel {
            text: "INCLUDE"
            visible: backupVM.phase === "idle"
        }

        Column {
            width: card.innerWidth
            visible: backupVM.phase === "idle"
            spacing: 0

            // ToggleSwitch never moves its own knob — the caller decides
            // whether the state changes. Here it always should, so each row
            // just pushes the new value straight back.
            ToggleSwitch {
                width: parent.width
                label: "Tracks"
                description: "Your music"
                checked: backupVM.includeTracks
                onToggled: function (value) { backupVM.includeTracks = value }
            }
            ToggleSwitch {
                width: parent.width
                label: "Instrumentals"
                description: "Generated backing tracks — roughly doubles the size"
                checked: backupVM.includeBacking
                onToggled: function (value) { backupVM.includeBacking = value }
            }
            ToggleSwitch {
                width: parent.width
                label: "Interfaces"
                description: "Installed GUI packages"
                checked: backupVM.includeGuis
                onToggled: function (value) { backupVM.includeGuis = value }
            }
            ToggleSwitch {
                width: parent.width
                label: "Separation models"
                description: "Large, and downloadable again from where they came from"
                checked: backupVM.includeModels
                onToggled: function (value) { backupVM.includeModels = value }
            }
        }

        Text {
            width: card.innerWidth
            visible: backupVM.phase === "idle"
            text: "The library database, artwork cache and settings are always included."
            color: Theme.faint
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
            wrapMode: Text.WordWrap
        }

        // ---------------------------------------------------------- review

        SectionLabel {
            text: "WHAT WILL CHANGE"
            visible: root._reviewing
        }

        Rectangle {
            width: card.innerWidth
            visible: root._reviewing
            height: reviewColumn.implicitHeight + 20
            radius: Theme.radius
            color: Theme.alpha(Theme.card, 0.72)
            border.width: 1
            border.color: Theme.line

            Column {
                id: reviewColumn
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                spacing: 3

                // InfoRow binds its own width to its parent, and hides itself
                // when the value is empty — which none of these ever are, so
                // the review always shows the same six lines.
                InfoRow {
                    label: "Add"
                    value: Fmt.count(backupVM.addedCount, "file")
                }
                InfoRow {
                    label: "Update"
                    value: Fmt.count(backupVM.updatedCount, "file")
                }
                InfoRow {
                    label: "Unchanged"
                    value: Fmt.count(backupVM.unchangedCount, "file")
                }
                InfoRow {
                    label: "To write"
                    value: Fmt.mb(backupVM.writeMb)
                }
                InfoRow {
                    label: "Backup"
                    value: Fmt.mb(backupVM.backupBeforeMb) + "  →  "
                           + Fmt.mb(backupVM.backupAfterMb)
                }
                InfoRow {
                    label: "Free"
                    value: Fmt.mb(backupVM.freeMb)
                }
            }
        }

        // A backup that cannot prove which library it came from. Allowed —
        // refusing would strand anyone whose backup predates vault ids — but
        // never silently.
        Text {
            width: card.innerWidth
            visible: root._reviewing && backupVM.identityUnverified
            text: "This folder does not record which library it belongs to. "
                  + "Check the numbers above before continuing; confirming will "
                  + "mark it as belonging to this library."
            color: Theme.warning
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
            wrapMode: Text.WordWrap
        }

        // The one case that is refused outright.
        Rectangle {
            width: card.innerWidth
            visible: root._reviewing && backupVM.identityMismatch
            height: mismatchColumn.implicitHeight + 20
            radius: Theme.radius
            color: Theme.alpha(Theme.danger, 0.10)
            border.width: 1
            border.color: Theme.danger

            Column {
                id: mismatchColumn
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                spacing: Theme.gapXs

                Text {
                    width: parent.width
                    text: "This is a backup of a different library"
                    color: Theme.danger
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzBody
                    font.weight: Font.DemiBold
                    wrapMode: Text.WordWrap
                }
                Text {
                    width: parent.width
                    text: "Continuing would overwrite it with yours. If this "
                          + "folder holds someone else's music, or your only "
                          + "copy of another library, close this now."
                    color: Theme.dim
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzSmall
                    wrapMode: Text.WordWrap
                }
                Text {
                    width: parent.width
                    text: "This library " + backupVM.sourceVaultId.substring(0, 12)
                          + " · that backup " + backupVM.backupVaultId.substring(0, 12)
                          + (backupVM.backupCreatedAt !== ""
                             ? ", created " + backupVM.backupCreatedAt.substring(0, 10) : "")
                    color: Theme.faint
                    // Same choice InfoRow makes for its own mono values: ids
                    // are compared character by character, so they want even
                    // widths rather than the UI face.
                    font.family: "Consolas"
                    font.pixelSize: Theme.fzTiny
                    wrapMode: Text.WordWrap
                }
                // A separate deliberate act, not the Confirm button.
                ToggleSwitch {
                    width: parent.width
                    label: "Overwrite this backup anyway"
                    checked: backupVM.overrideMismatch
                    onToggled: function (value) { backupVM.overrideMismatch = value }
                }
            }
        }

        // Files in the backup the library no longer has. Reported always,
        // removed only if the user says so on this run.
        Rectangle {
            width: card.innerWidth
            visible: root._reviewing && backupVM.extraCount > 0
            height: extrasColumn.implicitHeight + 20
            radius: Theme.radius
            color: Theme.alpha(Theme.warning, 0.10)
            border.width: 1
            border.color: Theme.alpha(Theme.warning, 0.45)

            Column {
                id: extrasColumn
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                spacing: Theme.gapXs

                Text {
                    width: parent.width
                    text: Fmt.count(backupVM.extraCount, "file") + " in this backup "
                          + (backupVM.extraCount === 1 ? "is" : "are")
                          + " no longer in your library · " + Fmt.mb(backupVM.extraMb)
                    color: Theme.text
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzSmall
                    wrapMode: Text.WordWrap
                }
                ToggleSwitch {
                    width: parent.width
                    label: "Remove them from the backup"
                    description: "Off by default — a backup is also somewhere deleted tracks survive"
                    checked: backupVM.deleteExtras
                    onToggled: function (value) { backupVM.deleteExtras = value }
                }
            }
        }

        // Whatever else the scan wants to say: not enough room, a run that was
        // interrupted, an unusually large rewrite.
        Column {
            width: card.innerWidth
            visible: root._reviewing && backupVM.warnings.length > 0
            spacing: Theme.gapXs

            Repeater {
                model: backupVM.warnings
                Text {
                    width: card.innerWidth
                    text: modelData
                    color: Theme.warning
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzSmall
                    wrapMode: Text.WordWrap
                }
            }
        }

        Text {
            width: card.innerWidth
            visible: backupVM.phase === "scanning" && backupVM.fullVerify
            text: "First sync to this folder — checking every file."
            color: Theme.faint
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
            wrapMode: Text.WordWrap
        }

        // ------------------------------------------------------------ done

        Column {
            width: card.innerWidth
            visible: root._finished
            spacing: Theme.gapXs

            Text {
                width: parent.width
                text: Fmt.count(backupVM.writtenCount, "file") + " written"
                      + (backupVM.deletedCount > 0
                         ? " · " + backupVM.deletedCount + " removed" : "")
                color: Theme.text
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzBody
                wrapMode: Text.WordWrap
            }
            Text {
                width: parent.width
                text: root._folder
                color: Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
                elide: Text.ElideMiddle
            }
            Repeater {
                model: backupVM.resultWarnings
                Text {
                    width: card.innerWidth
                    text: modelData
                    color: Theme.warning
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzSmall
                    wrapMode: Text.WordWrap
                }
            }
        }

        // -------------------------------------------------------- progress

        Rectangle {
            width: card.innerWidth
            height: 46
            radius: Theme.radius
            color: Theme.alpha(Theme.card, 0.72)
            border.width: 1
            border.color: Theme.line
            visible: backupVM.isRunning

            Text {
                anchors.left: parent.left
                anchors.leftMargin: 12
                anchors.right: parent.right
                anchors.rightMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                text: (backupVM.stage !== "" ? backupVM.stage : "Working")
                      + (backupVM.total > 0
                         ? " · " + backupVM.processed + " of " + backupVM.total : "")
                elide: Text.ElideMiddle
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
                width: (parent.width - 2) * Math.min(1, backupVM.progress / 100)

                Behavior on width { NumberAnimation { duration: Theme.normal } }
            }
        }

        Text {
            width: card.innerWidth
            text: root._error
            visible: text !== ""
            color: Theme.danger
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
            wrapMode: Text.WordWrap
        }

        Item { width: 1; height: Theme.gapXs }

        Row {
            anchors.right: parent.right
            anchors.rightMargin: Theme.gapLg
            spacing: Theme.gapSm

            AppButton {
                text: backupVM.isRunning ? "Stop" : "Close"
                variant: "ghost"
                onClicked: {
                    if (backupVM.isRunning)
                        backupVM.cancel()
                    else
                        root.close()
                }
            }

            AppButton {
                text: "Back"
                variant: "ghost"
                visible: root._reviewing
                onClicked: {
                    root._error = ""
                    backupVM.reset()
                }
            }

            AppButton {
                text: backupVM.actionLabel
                iconName: "folder"
                variant: "filled"
                visible: backupVM.phase === "idle"
                enabledAction: backupVM.canScan
                onClicked: {
                    root._error = ""
                    backupVM.scan()
                }
            }

            AppButton {
                text: "Confirm backup"
                iconName: "download"
                variant: "filled"
                danger: backupVM.identityMismatch
                visible: root._reviewing
                enabledAction: backupVM.canConfirm
                onClicked: {
                    root._error = ""
                    backupVM.confirm()
                }
            }
        }
    }
}
