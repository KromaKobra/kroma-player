// First-run vault picker.
//
// Unlike the rest of the interface this window is built into the application
// rather than shipped in a swappable GUI package: it runs before any GUI has
// been resolved, and the vault it picks is where GUI packages are installed.
// Keep it dependency-free — `vaultSetup` is the only context property it gets.

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Fusion
import QtQuick.Dialogs
import QtQuick.Layouts

ApplicationWindow {
    id: root

    width: 560
    height: 340
    minimumWidth: width
    maximumWidth: width
    minimumHeight: height
    maximumHeight: height
    visible: true
    title: "Kroma Player — Vault setup"
    color: "#1e1e1e"

    palette: Palette {
        buttonText: "#ffffff"
        button: "#3d3d3d"
        text: "#ffffff"
        base: "#252525"
        mid: "#3d3d3d"
        midlight: "#4d4d4d"
    }

    // Closing the window is a decision not to pick a vault, which ends the app.
    onClosing: (close) => vaultSetup.cancel()

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 28
        spacing: 12

        Label {
            text: "No music vault found"
            color: "#ffffff"
            font.pixelSize: 22
            font.bold: true
        }

        Label {
            Layout.fillWidth: true
            text: "Kroma Player keeps your music, database and settings together in "
                + "a single vault folder. Open one you already have, or create a new one."
            color: "#b0b0b0"
            font.pixelSize: 13
            wrapMode: Text.WordWrap
        }

        Label {
            Layout.fillWidth: true
            visible: text !== ""
            text: vaultSetup.errorMessage
            color: "#ff6b6b"
            font.pixelSize: 13
            wrapMode: Text.WordWrap
        }

        // A backup is a complete vault, so it opens perfectly well — which is
        // the point, after a drive failure. It is also how somebody ends up
        // playing from and downloading into their spare copy without noticing,
        // leaving two libraries that have quietly drifted apart. So it asks.
        Label {
            Layout.fillWidth: true
            visible: vaultSetup.pendingBackup !== ""
            text: "That folder is a backup.\n\nOpening it makes it your working "
                + "library — anything you download or change goes here, not to "
                + "the library it was copied from. That is what you want if the "
                + "original is gone, and not what you want otherwise."
            color: "#ffcc66"
            font.pixelSize: 13
            wrapMode: Text.WordWrap
        }

        Item { Layout.fillHeight: true }

        Label {
            Layout.fillWidth: true
            text: "A new vault is created at " + vaultSetup.defaultVaultPath + " unless you choose elsewhere."
            color: "#7a7a7a"
            font.pixelSize: 11
            wrapMode: Text.WordWrap
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            visible: vaultSetup.pendingBackup === ""

            Button {
                Layout.fillWidth: true
                Layout.preferredHeight: 40
                text: "Open existing vault…"
                onClicked: openDialog.open()
            }

            Button {
                Layout.fillWidth: true
                Layout.preferredHeight: 40
                text: "Create new vault…"
                highlighted: true
                onClicked: createDialog.open()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            visible: vaultSetup.pendingBackup !== ""

            Button {
                Layout.fillWidth: true
                Layout.preferredHeight: 40
                text: "Choose a different folder"
                onClicked: vaultSetup.dismissBackup()
            }

            Button {
                Layout.fillWidth: true
                Layout.preferredHeight: 40
                text: "Use the backup"
                onClicked: vaultSetup.confirmBackup()
            }
        }
    }

    FolderDialog {
        id: openDialog
        title: "Select your vault folder"
        currentFolder: vaultSetup.musicFolderUrl
        onAccepted: vaultSetup.openExisting(selectedFolder)
    }

    FolderDialog {
        id: createDialog
        title: "Choose where to create your vault"
        currentFolder: vaultSetup.musicFolderUrl
        acceptLabel: "Create vault here"
        onAccepted: vaultSetup.createNew(selectedFolder)
    }
}
