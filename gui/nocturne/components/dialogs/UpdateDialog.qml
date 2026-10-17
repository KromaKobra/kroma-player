import QtQuick
import QtQuick.Controls
import Nocturne
import "../controls"

// A newer Kroma Player: what it is, the advice to back up first, and one button
// that downloads, installs and restarts.
//
// Opened by main.qml when the launch check finds a release, and at startup when
// the previous launch handed off an update that did not take. Everything it
// shows is updatesVM's; the only thing it owns is when it is on screen. "Back up
// first" is raised rather than handled, because the backup dialog lives on the
// Library page and the window is what can go there and come back.
//
// While the download runs it stays put — no click-outside, no Escape — because
// a card that vanishes mid-download reads as a cancel that did not happen, and
// the restart at the end of it would then arrive out of nowhere.
Popup {
    id: root
    objectName: "updateDialog"

    // Asked for from the backup card. main.qml takes the user to the backup and
    // brings this back afterwards.
    signal backupRequested()

    readonly property bool hasUpdates: typeof updatesVM !== "undefined"
    readonly property string phase: hasUpdates ? updatesVM.phase : "idle"
    readonly property bool available: hasUpdates && updatesVM.updateAvailable
    readonly property bool failed: hasUpdates && updatesVM.lastUpdateFailed
    readonly property bool downloading: phase === "downloading"
    readonly property bool applying: phase === "applying"
    readonly property bool manual: phase === "manual"
    readonly property bool busy: hasUpdates && updatesVM.isBusy

    property string _error: ""

    function openDialog() {
        _error = ""
        open()
    }

    modal: true
    dim: true
    padding: 0
    closePolicy: (downloading || applying)
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
        target: root.hasUpdates ? updatesVM : null
        // Inline while the card is up — the toast behind the dim would bury it.
        function onError_occurred(message) {
            if (root.visible)
                root._error = message
        }
        function onPhase_changed() {
            if (root.phase === "downloading")
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
            text: root.available
                  ? "Kroma Player " + updatesVM.latestVersion + " is available"
                  : (root.failed ? "The last update did not finish"
                                 : "Kroma Player is up to date")
            color: Theme.text
            font.family: Theme.displayFamily
            font.pixelSize: Theme.fzLarge
            font.weight: Font.DemiBold
            wrapMode: Text.WordWrap
        }

        Text {
            width: card.innerWidth
            visible: root.available
            text: root.hasUpdates
                  ? "You have " + updatesVM.currentVersion
                    + (updatesVM.downloadMb > 0
                       ? " · " + Math.round(updatesVM.downloadMb) + " MB download" : "")
                  : ""
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }

        // ------------------------------------------------- the last attempt

        Rectangle {
            width: card.innerWidth
            visible: root.failed
            height: failedColumn.implicitHeight + 20
            radius: Theme.radius
            color: Theme.alpha(Theme.warning, 0.10)
            border.width: 1
            border.color: Theme.alpha(Theme.warning, 0.45)

            Column {
                id: failedColumn
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                spacing: Theme.gapXs

                Text {
                    width: parent.width
                    text: root.hasUpdates
                          ? "Kroma Player tried to update to " + updatesVM.failedVersion
                            + ", but this is still " + updatesVM.currentVersion
                            + ". Your library was not changed. The installer's log "
                            + "says what stopped it."
                          : ""
                    color: Theme.text
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzSmall
                    lineHeight: 1.35
                    wrapMode: Text.WordWrap
                }
                AppButton {
                    objectName: "showInstallLogButton"
                    text: "Show install log"
                    iconName: "folder"
                    variant: "ghost"
                    fontSize: Theme.fzSmall
                    onClicked: updatesVM.revealInstallLog()
                }
            }
        }

        // --------------------------------------------------- what is in it

        // The highlights only: the release body down to its first rule, as
        // Markdown. The rest is a link away.
        Item {
            width: card.innerWidth
            height: notesFlick.height
            visible: root.available && notesText.text !== ""

            Rectangle {
                anchors.fill: parent
                radius: Theme.radius
                color: Theme.sunken
                border.width: 1
                border.color: Theme.line
            }

            Flickable {
                id: notesFlick
                x: 12
                y: 10
                width: parent.width - 24
                height: Math.min(notesText.implicitHeight, 200) + 20
                contentHeight: notesText.implicitHeight + 20
                clip: true
                boundsBehavior: Flickable.StopAtBounds

                ScrollBar.vertical: EdgeScrollBar {}

                Text {
                    id: notesText
                    width: parent.width - 10
                    text: root.hasUpdates ? updatesVM.releaseSummary : ""
                    textFormat: Text.MarkdownText
                    color: Theme.dim
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzSmall
                    lineHeight: 1.3
                    wrapMode: Text.WordWrap
                    onLinkActivated: function (link) { Qt.openUrlExternally(link) }
                }
            }

            SmoothWheel { target: notesFlick }
        }

        AppButton {
            visible: root.available
            text: "Full release notes"
            variant: "ghost"
            fontSize: Theme.fzSmall
            onClicked: updatesVM.openReleasePage()
        }

        // ---------------------------------------------------------- back up
        // Always, not only before a migrating update: the advice costs one
        // card, and the one time it matters is not always the one the release
        // notes predicted.
        Rectangle {
            width: card.innerWidth
            visible: root.available && !root.applying
            height: backupColumn.implicitHeight + 20
            radius: Theme.radius
            color: Theme.alpha(Theme.card, 0.72)
            border.width: 1
            border.color: Theme.line

            Column {
                id: backupColumn
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                spacing: Theme.gapXs

                Text {
                    width: parent.width
                    text: "Back up your library before you update. A backup is "
                          + "your way back if anything goes wrong."
                    color: Theme.text
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzSmall
                    lineHeight: 1.35
                    wrapMode: Text.WordWrap
                }
                AppButton {
                    objectName: "backupFirstButton"
                    text: "Back up first"
                    iconName: "folder"
                    variant: "soft"
                    fontSize: Theme.fzSmall
                    enabledAction: !root.busy
                    onClicked: {
                        root.close()
                        root.backupRequested()
                    }
                }
            }
        }

        // The one-way door, said before the button rather than after it.
        Text {
            width: card.innerWidth
            visible: root.available && root.hasUpdates && updatesVM.willMigrate
            text: "This update upgrades how your library is stored. Older versions "
                  + "of Kroma Player will not be able to open it afterwards. Kroma "
                  + "Player saves a copy of the library database before upgrading "
                  + "it, but that copy does not include your music."
            color: Theme.warning
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
            lineHeight: 1.35
            wrapMode: Text.WordWrap
        }

        // -------------------------------------------------------- progress

        Rectangle {
            width: card.innerWidth
            height: 46
            radius: Theme.radius
            color: Theme.alpha(Theme.card, 0.72)
            border.width: 1
            border.color: Theme.line
            visible: root.downloading || root.applying

            Text {
                anchors.left: parent.left
                anchors.leftMargin: 12
                anchors.right: parent.right
                anchors.rightMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                elide: Text.ElideRight
                text: root.applying
                      ? "Installing — Kroma Player will close and reopen in a moment"
                      : (root.hasUpdates && updatesVM.stage !== ""
                         ? updatesVM.stage : "Downloading")
                        + (root.hasUpdates ? "  " + updatesVM.progress + "%" : "")
                color: Theme.dim
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }

            Rectangle {
                anchors.left: parent.left
                anchors.leftMargin: 1
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 1
                height: 2
                radius: 1
                color: Theme.accent
                width: (parent.width - 2) * (root.applying ? 1
                       : Math.min(1, (root.hasUpdates ? updatesVM.progress : 0) / 100))

                Behavior on width { NumberAnimation { duration: Theme.normal } }
            }
        }

        Text {
            width: card.innerWidth
            visible: root.downloading && root.hasUpdates && updatesVM.installWhenReady
            text: "Kroma Player will restart to finish the update as soon as the "
                  + "download has been checked."
            color: Theme.faint
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
            wrapMode: Text.WordWrap
        }

        // ---------------------------------------------------------- manual
        Column {
            width: card.innerWidth
            visible: root.manual
            spacing: Theme.gapXs

            Text {
                width: parent.width
                text: "The update was downloaded and verified, but Kroma Player "
                      + "could not install it for you. Open the folder and run it "
                      + "yourself — your library is untouched."
                color: Theme.warning
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
                lineHeight: 1.35
                wrapMode: Text.WordWrap
            }
            AppButton {
                text: "Show the file"
                iconName: "folder"
                variant: "ghost"
                fontSize: Theme.fzSmall
                onClicked: updatesVM.revealDownload()
            }
        }

        Text {
            width: card.innerWidth
            visible: root._error !== ""
            text: root._error
            color: Theme.danger
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
            wrapMode: Text.WordWrap
        }

        Item { width: 1; height: Theme.gapXs }

        // --------------------------------------------------------- buttons
        Row {
            anchors.right: parent.right
            anchors.rightMargin: Theme.gapLg
            spacing: Theme.gapSm
            visible: !root.applying

            AppButton {
                objectName: "skipVersionButton"
                text: "Skip this version"
                variant: "ghost"
                visible: root.available && !root.downloading && !root.manual
                onClicked: {
                    updatesVM.skipThisVersion()
                    root.close()
                }
            }

            // Cancel while a download runs; otherwise put this away. "Later"
            // also stops reporting a failed update — Escape does not, so it is
            // shown again next launch to anyone who did not read it.
            AppButton {
                objectName: "laterButton"
                text: root.downloading ? "Cancel" : (root.available ? "Later" : "Close")
                variant: "ghost"
                onClicked: {
                    if (root.downloading) {
                        updatesVM.cancel()
                        return
                    }
                    if (root.failed)
                        updatesVM.dismissUpdateFailure()
                    root.close()
                }
            }

            AppButton {
                objectName: "checkAgainButton"
                text: "Check again"
                variant: "filled"
                visible: !root.available && root.failed
                enabledAction: !root.busy
                onClicked: updatesVM.check()
            }

            AppButton {
                objectName: "updateNowButton"
                text: "Update and restart"
                variant: "filled"
                visible: root.available && !root.manual
                enabledAction: !root.busy
                onClicked: {
                    root._error = ""
                    updatesVM.updateNow()
                }
            }
        }
    }
}
