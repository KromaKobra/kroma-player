import QtQuick
import Nocturne
import "../../components"

SettingsPane {
    id: pane
    title: "Library"
    description: "Keep your music together, backed up and organised."
    signal backupRequested()
    onActiveChanged: if (active && typeof databaseVM !== "undefined") databaseVM.refresh()
    function vaultUrl() {
        var p = String(typeof databaseVM !== "undefined" ? databaseVM.vaultPath : "").replace(/\\/g, "/")
        return (p.charAt(0) === "/" ? "file://" : "file:///") + p
    }
    SettingsGroup {
        width: parent.width
        title: "Storage"
        PathSetting {
            width: parent.width
            label: "Library vault"
            description: "Your audio, database, artwork and interface packages live together in this folder."
            keywords: "location directory move files storage"
            path: typeof databaseVM !== "undefined" ? databaseVM.vaultPath : ""
            actionText: "Open folder"
            secondaryText: "Move vault…"
            onRequested: Qt.openUrlExternally(pane.vaultUrl())
            onSecondaryRequested: vaultGuide.open()
        }
        SettingRow {
            width: parent.width
            label: "Library backup"
            description: "Back up your vault to another folder. Only changes since the last backup are copied."
            keywords: "backup back up copy protect restore music database artwork"
            SettingButton {
                objectName: "libraryBackupButton"
                text: "Back up library…"
                onClicked: pane.backupRequested()
            }
        }
    }
    SettingsGroup {
        width: parent.width
        title: "Importing"
        visible: typeof importVM !== "undefined"
        searchAvailable: typeof importVM !== "undefined"
        ToggleSwitch {
            objectName: "clearImportedToggle"
            width: parent.width
            label: "Hide imported tracks in the list"
            description: "Hide tracks once they are in your library. Turn this off to show them again; the “in library” count always includes them."
            keywords: "clear completed duplicates"
            checked: typeof importVM !== "undefined" ? importVM.clearImported : true
            onToggled: function(value) { importVM.clearImported = value }
        }
    }
    VaultMoveGuide {
        id: vaultGuide
        vaultPath: typeof databaseVM !== "undefined" ? databaseVM.vaultPath : ""
        onPathCopied: pane.notify("Vault path copied", false)
    }
}
