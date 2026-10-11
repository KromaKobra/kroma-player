import QtQuick
import Nocturne
import "../../components"

// The machine Kroma is running on: whether Kroma itself is current, and whether
// the programs it and its plugins need but do not ship are installed.
SettingsPane {
    id: pane
    title: "System"
    description: "Keep Kroma and its supporting tools ready to play."

    readonly property bool hasUpdates: typeof updatesVM !== "undefined"

    // "Back up first" opens the shared backup dialog and returns to the update.
    signal backupRequested()

    // Every program worth checking, as the backend lists them: ffmpeg, which
    // Kroma needs itself, then whatever the enabled plugins need.
    readonly property var programs:
        typeof dependenciesVM !== "undefined" ? dependenciesVM.programs : []

    // The Re-check toast's list.
    function missingDependencies() {
        var missing = []
        for (var i = 0; i < pane.programs.length; ++i) {
            if (!pane.programs[i].available)
                missing.push(pane.programs[i].name)
        }
        return missing
    }

    onActiveChanged: {
        if (!active)
            return
        // Cheap, and it means the page is never showing an answer from before
        // the user went off and installed something.
        if (typeof dependenciesVM !== "undefined")
            dependenciesVM.refresh()
        // Eligibility and the last-checked stamp, both read off this machine.
        // Deliberately *not* a check: firing one here would put a network
        // request behind every visit to this tab. Automatic checks belong to
        // launch and the 24 h gate, and the button below belongs to the user.
        if (pane.hasUpdates)
            updatesVM.refresh()
    }

    Connections {
        target: pane.hasUpdates ? updatesVM : null
        function onError_occurred(message) { pane.notify(message, true) }
    }

    // One external program: what it is for, whether it was found, and how to
    // get it. Three of these, identical apart from their text, so the shape is
    // written once.
    component DependencyRow: Column {
        id: depRow

        property string program: ""
        property string purpose: ""
        property bool found: false
        property string path: ""
        property string installCommand: ""
        property string version: ""
        property string minimumVersion: ""
        // Installed but older than something that needs it says it must be —
        // which for some programs fails as surely as being missing, with an
        // error that never mentions versions.
        property bool stale: false
        // Who needs it, by name. Shown when it is not Kroma itself, so a
        // program nobody remembers installing can be traced to its plugin.
        property var neededBy: []

        width: parent.width
        spacing: 4

        Row {
            spacing: Theme.gapSm

            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: depRow.program
                color: Theme.text
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzBody
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: !depRow.found ? "Not found"
                    : depRow.stale ? "Out of date"
                    : "Installed"
                color: depRow.found && !depRow.stale ? Theme.positive : Theme.warning
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzTiny
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                visible: depRow.found && depRow.version !== ""
                text: depRow.version
                color: Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzTiny
            }
        }

        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            visible: text !== ""
            text: {
                var needs = (depRow.neededBy || []).filter(function (n) {
                    return n !== "Kroma Player"
                })
                var who = needs.length > 0 ? "Needed by " + needs.join(", ") + ". " : ""
                return who + depRow.purpose
            }
            color: Theme.faint
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzTiny
        }

        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            visible: depRow.found && depRow.stale
            text: "This version is too old to work. Update it (with its own "
                + "updater, or your package manager); "
                + depRow.minimumVersion + " or newer is needed."
            color: Theme.warning
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzTiny
        }

        // Where it was found, or the one command that installs it. Selectable
        // either way: a path is worth copying into a bug report, and a command
        // is worth pasting into a terminal.
        Rectangle {
            width: parent.width
            height: 34
            radius: Theme.radius
            color: Theme.sunken
            border.width: 1
            border.color: Theme.line

            TextInput {
                anchors.left: parent.left
                anchors.leftMargin: 12
                anchors.right: parent.right
                anchors.rightMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                text: depRow.found ? depRow.path
                    : (depRow.installCommand !== "" ? depRow.installCommand
                       : "Install " + depRow.program + " and make sure it is on PATH")
                readOnly: true
                selectByMouse: true
                color: depRow.found ? Theme.dim : Theme.text
                font.family: depRow.found ? Theme.fontFamily : "Consolas"
                font.pixelSize: Theme.fzSmall
            }
        }
    }

    // ================================================= kroma player
    // Whether this build is the current one, and one button to make it so.
    //
    // Every reference is guarded with `typeof updatesVM !== "undefined"` — the
    // property arrived in GUI API 11, and this package still has to load on a
    // backend that predates it. On such a backend the whole section is absent
    // rather than broken, which is the same bargain every other tab makes.
    SettingsGroup {
        title: "App updates"
        keywords: "version release upgrade backup"
        width: parent.width
        spacing: Theme.gapSm
        visible: pane.hasUpdates
        searchAvailable: pane.hasUpdates



        // Always, whatever else is on screen. The version is the first thing
        // any bug report needs, and once there is an updater it is the number
        // the whole section turns on.
        Text {
            width: parent.width
            text: "Version " + (pane.hasUpdates ? updatesVM.currentVersion : "")
                + (pane.hasUpdates && updatesVM.lastChecked !== ""
                   ? " — last checked " + updatesVM.lastChecked : "")
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }

        // ---------------------------------------------------- not updatable
        // A sentence rather than a button that could not work. Running from
        // source, managed by a package manager, a portable copy, or switched
        // off for this build — four situations, one of which is somebody
        // else's job and three of which the user can act on.
        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            lineHeight: 1.45
            visible: pane.hasUpdates && !updatesVM.eligible
            text: pane.hasUpdates ? updatesVM.ineligibleReason : ""
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }

        AppButton {
            text: "Releases on GitHub"
            variant: "ghost"
            visible: pane.hasUpdates && !updatesVM.eligible
            onClicked: updatesVM.openReleasePage()
        }

        // -------------------------------------------- the last update failed
        Column {
            width: parent.width
            spacing: Theme.gapXs
            visible: pane.hasUpdates && updatesVM.lastUpdateFailed

            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                lineHeight: 1.45
                text: pane.hasUpdates
                      ? "The update to " + updatesVM.failedVersion + " did not finish, "
                        + "so this is still " + updatesVM.currentVersion + ". Your "
                        + "library was not changed. The installer's log says why."
                      : ""
                color: Theme.warning
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }

            Row {
                spacing: Theme.gapSm

                AppButton {
                    text: "Show install log"
                    iconName: "folder"
                    variant: "ghost"
                    onClicked: updatesVM.revealInstallLog()
                }
                AppButton {
                    text: "Dismiss"
                    variant: "ghost"
                    onClicked: updatesVM.dismissUpdateFailure()
                }
            }
        }

        // ------------------------------------------------------- up to date
        Text {
            width: parent.width
            visible: pane.hasUpdates && updatesVM.eligible
                     && updatesVM.phase === "up_to_date"
            text: "Kroma Player is up to date."
            color: Theme.positive
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }

        // ------------------------------------------------------- an update
        Column {
            width: parent.width
            spacing: Theme.gapSm
            visible: pane.hasUpdates && updatesVM.eligible && updatesVM.updateAvailable

            Text {
                width: parent.width
                text: "Kroma Player " + (pane.hasUpdates ? updatesVM.latestVersion : "")
                    + " is available"
                    + (pane.hasUpdates && updatesVM.downloadMb > 0
                       ? " (" + Math.round(updatesVM.downloadMb) + " MB)" : "")
                color: Theme.text
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzBody
                font.weight: Font.Medium
            }

            // The highlights — the release body down to its first rule — as
            // Markdown, with the whole thing a click away under "Release notes".
            Rectangle {
                width: parent.width
                height: Math.min(180, notesText.implicitHeight + 20)
                radius: Theme.radius
                color: Theme.sunken
                border.width: 1
                border.color: Theme.line
                visible: notesText.text !== ""

                Flickable {
                    anchors.fill: parent
                    anchors.margins: 10
                    contentHeight: notesText.implicitHeight
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds

                    Text {
                        id: notesText
                        width: parent.width
                        wrapMode: Text.WordWrap
                        textFormat: Text.MarkdownText
                        text: pane.hasUpdates ? updatesVM.releaseSummary : ""
                        color: Theme.dim
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzSmall
                        onLinkActivated: function (link) { Qt.openUrlExternally(link) }
                    }
                }
            }

            // Before every update, not only a migrating one: the advice costs a
            // line, and the time it matters is not always the one the release
            // notes predicted.
            Row {
                width: parent.width
                spacing: Theme.gapSm

                Text {
                    width: parent.width - backupFirst.width - Theme.gapSm
                    anchors.verticalCenter: parent.verticalCenter
                    wrapMode: Text.WordWrap
                    text: "Back up your library before you update — it is your "
                        + "way back if anything goes wrong."
                    color: Theme.dim
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzSmall
                }
                AppButton {
                    id: backupFirst
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Back up first"
                    iconName: "folder"
                    variant: "soft"
                    fontSize: Theme.fzSmall
                    enabledAction: pane.hasUpdates && !updatesVM.isBusy
                    onClicked: pane.backupRequested()
                }
            }

            // The one-way door, said before the button rather than after it.
            // Once the library is migrated — its layout or its database — every
            // older build refuses to open it.
            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                lineHeight: 1.45
                visible: pane.hasUpdates && updatesVM.willMigrate
                text: "This update upgrades how your library is stored. Older "
                    + "versions of Kroma Player will not be able to open it "
                    + "afterwards. A copy of the library database is saved before "
                    + "the upgrade, but it does not include your music."
                color: Theme.warning
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzTiny
            }

            Row {
                spacing: Theme.gapSm

                // One button for the whole thing: download, check, install,
                // restart. Pressed during a download it is not offered — the
                // download already knows.
                AppButton {
                    objectName: "systemUpdateNowButton"
                    text: "Update and restart"
                    variant: "filled"
                    enabledAction: pane.hasUpdates && !updatesVM.isBusy
                    onClicked: updatesVM.updateNow()
                }

                AppButton {
                    text: "Skip this version"
                    variant: "ghost"
                    enabledAction: pane.hasUpdates && !updatesVM.isBusy
                    onClicked: updatesVM.skipThisVersion()
                }

                AppButton {
                    text: "Release notes"
                    variant: "ghost"
                    onClicked: updatesVM.openReleasePage()
                }
            }
        }

        // ------------------------------------------------------ downloading
        Rectangle {
            width: parent.width
            height: 46
            radius: Theme.radius
            color: Theme.alpha(Theme.card, 0.72)
            border.width: 1
            border.color: Theme.line
            visible: pane.hasUpdates && updatesVM.phase === "downloading"

            Text {
                anchors.left: parent.left
                anchors.leftMargin: 12
                anchors.right: parent.right
                anchors.rightMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                elide: Text.ElideRight
                text: (pane.hasUpdates && updatesVM.stage !== ""
                       ? updatesVM.stage : "Downloading")
                      + (pane.hasUpdates ? "  " + updatesVM.progress + "%" : "")
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
                width: (parent.width - 2)
                       * Math.min(1, (pane.hasUpdates ? updatesVM.progress : 0) / 100)

                Behavior on width { NumberAnimation { duration: Theme.normal } }
            }
        }

        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            visible: pane.hasUpdates && updatesVM.phase === "downloading"
                     && updatesVM.installWhenReady
            text: "Kroma Player will restart to finish the update as soon as the "
                + "download has been checked."
            color: Theme.faint
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzTiny
        }

        AppButton {
            text: "Cancel"
            variant: "ghost"
            visible: pane.hasUpdates && updatesVM.phase === "downloading"
            onClicked: updatesVM.cancel()
        }

        // ----------------------------------------------------------- manual
        // A verified update that could not be installed automatically. The file
        // is on disk and it is known good, so say where it is rather than
        // throwing it away.
        Column {
            width: parent.width
            spacing: Theme.gapSm
            visible: pane.hasUpdates && updatesVM.phase === "manual"

            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                lineHeight: 1.45
                text: "The update was downloaded and verified, but Kroma Player "
                    + "could not install it for you. Open the folder and run it "
                    + "yourself — everything in your library is untouched."
                color: Theme.warning
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }

            AppButton {
                text: "Show the file"
                variant: "ghost"
                onClicked: updatesVM.revealDownload()
            }
        }

        // ----------------------------------------------------- the controls
        Item { width: 1; height: Theme.gapXs }

        AppButton {
            text: "Check for updates"
            iconName: "sort"
            variant: "ghost"
            visible: pane.hasUpdates && updatesVM.eligible
            enabledAction: pane.hasUpdates && !updatesVM.isBusy
            onClicked: updatesVM.check()
        }

        ToggleSwitch {
            width: parent.width
            visible: pane.hasUpdates && updatesVM.eligible
            label: "Check for updates at launch"
            description: "Once a day at most, and never more than a few "
                       + "kilobytes. Nothing is downloaded without asking you."
            checked: pane.hasUpdates && updatesVM.checkOnLaunch
            onToggled: function (value) { updatesVM.checkOnLaunch = value }
        }
    }

    Rectangle {
        width: parent.width
        height: 1
        color: Theme.line
        visible: pane.hasUpdates
    }

    // ================================================= dependencies
    // None of these ship with Kroma Player, so on any given machine any of them
    // may be missing. Without this the news arrives as a failed import or a
    // refused export, which is a poor way to hear it.
    SettingsGroup {
        title: "Required programs"
        keywords: "ffmpeg ffprobe dependencies tools path install"
        width: parent.width
        spacing: Theme.gap

        // The tab holds two sections now, so both carry a heading — the note
        // that used to be here explained why a single-section tab has none.


        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: pane.programs.length > 1
                  ? "These free programs are installed separately from Kroma "
                    + "Player. Everything else works without them."
                  : "One free program is installed separately from Kroma "
                    + "Player. Everything else works without it."
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }

        Repeater {
            model: pane.programs
            delegate: DependencyRow {
                required property var modelData
                program: String(modelData.name || "")
                purpose: String(modelData.purpose || "")
                installCommand: String(modelData.install || "")
                found: modelData.available === true
                path: String(modelData.path || "")
                version: String(modelData.version || "")
                minimumVersion: String(modelData.minimumVersion || "")
                stale: modelData.stale === true
                neededBy: modelData.neededBy || []
            }
        }

        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            lineHeight: 1.45
            // The failure everyone hits: PATH only reaches programs started
            // afterwards, and Explorer — which launched this one — keeps the
            // old one until it restarts.
            text: "Just installed one and it still says Not found? Sign out of "
                + "Windows and back in, then re-check. If you cannot change "
                + "PATH, put the .exe next to Kroma Player's own and it will "
                + "be found there."
            color: Theme.faint
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzTiny
        }

        AppButton {
            text: "Re-check"
            iconName: "sort"
            variant: "ghost"
            onClicked: {
                dependenciesVM.refresh()
                var missing = pane.missingDependencies()
                pane.notify(missing.length === 0
                            ? (pane.programs.length > 1 ? "All installed" : "Installed")
                            : "Still missing: " + missing.join(", "),
                            false)
            }
        }
    }

    Item { width: 1; height: Theme.gapLg }
}
