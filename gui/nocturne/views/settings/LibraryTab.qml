import QtQuick
import Nocturne
import "../../components"

// Where audio lives, and what the import list keeps once it is done. What may
// fetch more of it is a plugin's business — Settings → Plugins.
SettingsPane {
    id: pane

    onActiveChanged: {
        if (!active)
            return
        // vaultPath is only populated by a refresh, and Settings may well be
        // the first page opened.
        if (typeof databaseVM !== "undefined")
            databaseVM.refresh()
    }

    // Windows hands back backslashes, which are not URL separators.
    function vaultUrl() {
        var p = String(typeof databaseVM !== "undefined" ? databaseVM.vaultPath : "")
                    .replace(/\\/g, "/")
        return (p.charAt(0) === "/" ? "file://" : "file:///") + p
    }

    // ======================================================== vault
    Column {
        width: parent.width
        spacing: Theme.gap

        SectionLabel { text: "VAULT" }

        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: "Your audio, the library database, artwork and this interface all "
                + "live in one folder. Everything else Kroma Player writes is a "
                + "pointer to it."
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }

        Rectangle {
            width: parent.width
            height: 38
            radius: Theme.radius
            color: Theme.sunken
            border.width: 1
            border.color: Theme.line

            Text {
                anchors.left: parent.left
                anchors.leftMargin: 12
                anchors.right: parent.right
                anchors.rightMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                text: typeof databaseVM !== "undefined" ? databaseVM.vaultPath : ""
                elide: Text.ElideMiddle
                color: Theme.dim
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }
        }

        Row {
            spacing: Theme.gapSm

            AppButton {
                text: "Open folder"
                iconName: "folder"
                variant: "ghost"
                onClicked: Qt.openUrlExternally(pane.vaultUrl())
            }
            AppButton {
                text: "Move vault…"
                iconName: "external"
                variant: "ghost"
                onClicked: vaultGuide.open()
            }
        }
    }

    Rectangle { width: parent.width; height: 1; color: Theme.line }

    // ======================================================= import
    //
    // Backend settings, like playback: this decides what the import list holds,
    // not how Nocturne draws it.
    Column {
        width: parent.width
        spacing: Theme.gap
        visible: typeof importVM !== "undefined"

        SectionLabel { text: "IMPORT" }

        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: "The import list is what you still have to decide about. A track "
                + "already in your library is not — there is nothing left to press "
                + "on it — so by default it does not take up a row. It is hidden "
                + "rather than dropped: the import page still counts it, and "
                + "turning this off brings it back where it was."
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }

        ToggleSwitch {
            // Named so a test can find this switch among the others and check
            // that its binding really reaches the backend. A misspelt property
            // is not an error in QML — it evaluates to `undefined`, and the knob
            // would simply never move. The name is also what makes the test
            // survive this page being split into tabs: findChild walks the whole
            // tree, so long as nothing gates the tab behind a Loader.
            objectName: "clearImportedToggle"
            width: parent.width
            label: "Hide imported tracks in the list"
            description: "Tracks leave the import list as they finish, and ones "
                       + "already in your library arrive hidden. The \"in "
                       + "library\" count on the import page still includes them."
            checked: typeof importVM !== "undefined" ? importVM.clearImported : true
            onToggled: function (value) { importVM.clearImported = value }
        }
    }

    Item { width: 1; height: Theme.gapLg }

    VaultMoveGuide {
        id: vaultGuide
        vaultPath: typeof databaseVM !== "undefined" ? databaseVM.vaultPath : ""
        onPathCopied: pane.notify("Vault path copied", false)
    }
}
