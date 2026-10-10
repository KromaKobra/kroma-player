import QtQuick
import QtQuick.Dialogs
import Nocturne
import "../../components"

// Also backend settings: which version of a track plays, and what the queue
// does when there is no instrumental, are not this package's business to
// remember.
SettingsPane {
    id: pane

    onActiveChanged: {
        if (!active)
            return
        if (typeof backingVM !== "undefined") {
            backingVM.refreshModels()
            // This page is where the interpreter and the model code are chosen,
            // so it is also where "what did they turn out to be" belongs. Held
            // until the tab is opened: probing starts a subprocess, and most
            // visits to Settings are not about instrumentals.
            backingVM.probeDevicesIfNeeded()
        }
        syncPills()
    }

    // Pill owns its own index: clicking one assigns to `currentIndex` and would
    // discard a binding we put there, leaving the control showing something the
    // backend never agreed to. So both pills are pushed from the settings
    // instead, the same way BrowseView drives its source pill.
    function syncPills() {
        if (typeof playerVM !== "undefined")
            missingPill.currentIndex =
                playerVM.backingMissingBehavior === "skip" ? 1 : 0
        if (typeof backingVM !== "undefined")
            devicePill.currentIndex = Math.max(
                0, backingVM.deviceChoices.indexOf(backingVM.device))
    }

    Column {
        width: parent.width
        spacing: Theme.gap
        visible: typeof playerVM !== "undefined"

        // No SectionLabel: the tab above already says what this is. The tabs
        // holding more than one section keep theirs.
        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: "A track can have an instrumental counterpart stored beside "
                + "it. The microphone button in the player switches between "
                + "the two while the track keeps playing."
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }

        Item {
            width: parent.width
            height: Math.max(missingLabel.implicitHeight, missingPill.height)

            Column {
                id: missingLabel
                anchors.left: parent.left
                anchors.right: missingPill.left
                anchors.rightMargin: Theme.gap
                anchors.verticalCenter: parent.verticalCenter
                spacing: 2

                Text {
                    text: "When no instrumental exists"
                    color: Theme.text
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzBody
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: "Applies as the queue moves on by itself. Picking a "
                        + "track by hand always plays it."
                    color: Theme.faint
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzTiny
                }
            }

            Pill {
                id: missingPill
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                options: ["Play the mix", "Skip the track"]
                onSelected: function (index) {
                    playerVM.backingMissingBehavior = index === 1 ? "skip" : "mix"
                }
                Component.onCompleted: pane.syncPills()
            }
        }

        NumberSetting {
            width: parent.width
            label: "Instrumental level"
            description: "Vocal removal usually hands back an instrumental "
                       + "quieter than the same music was in the mix. This "
                       + "lifts every one of them by the same amount as it "
                       + "plays — the stored file is left alone."
            minimum: -12
            maximum: 12
            step: 0.5
            decimals: 1
            suffix: " dB"
            valueWidth: 76
            value: typeof playerVM !== "undefined" ? playerVM.backingGainDb : 0
            onCommitted: function (v) { playerVM.backingGainDb = v }
        }

        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            visible: typeof playerVM !== "undefined" && playerVM.backingGainDb > 0
            text: "A boost has nowhere to go above full volume, so near the "
                + "top of the volume slider the difference shrinks. Turn the "
                + "volume down a little and it comes back."
            color: Theme.faint
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzTiny
        }

        // ---- generation -------------------------------------------

        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            visible: typeof backingVM !== "undefined"
            text: "Instrumentals can also be generated from the mix. Kroma "
                + "cannot run the model itself, so point it at a Python "
                + "installation of your own that has PyTorch — that way "
                + "you choose the build that matches your hardware. It "
                + "also needs code to run the model with: either the "
                + "audio-separator package installed there, or a folder "
                + "holding a copy of Music-Source-Separation-Training."
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }

        Column {
            width: parent.width
            spacing: Theme.gapSm
            visible: typeof backingVM !== "undefined"

            // Interpreter.
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
                    text: backingVM.pythonPath !== ""
                          ? backingVM.pythonPath
                          : "No Python interpreter selected"
                    elide: Text.ElideMiddle
                    color: backingVM.pythonPath !== "" ? Theme.dim : Theme.faint
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzSmall
                }
            }

            // Separation code. Only meaningful for the checkout route — an
            // interpreter with audio-separator in it needs nothing here, which
            // is why "not set" is stated as a working default rather than as
            // something missing.
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
                    text: backingVM.msstPath !== ""
                          ? backingVM.msstPath
                          : "Separation code: audio-separator, or a checkout found next to the interpreter"
                    elide: Text.ElideMiddle
                    color: backingVM.msstPath !== "" ? Theme.dim : Theme.faint
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzSmall
                }
            }

            Row {
                spacing: Theme.gapSm

                AppButton {
                    text: "Choose Python…"
                    iconName: "folder"
                    variant: "ghost"
                    onClicked: pythonDialog.open()
                }
                AppButton {
                    text: backingVM.msstPath !== ""
                          ? "Change model code…"
                          : "Choose model code…"
                    iconName: "folder"
                    variant: "ghost"
                    onClicked: msstDialog.open()
                }
                AppButton {
                    text: "Clear model code"
                    iconName: "x"
                    variant: "ghost"
                    visible: backingVM.msstPath !== ""
                    onClicked: backingVM.msstPath = ""
                }
                AppButton {
                    text: "Import model…"
                    iconName: "download"
                    variant: "ghost"
                    onClicked: modelImportDialog.open()
                }
                AppButton {
                    // The models folder is a plain directory, so files arrive in
                    // it without the app being told.
                    text: "Rescan models"
                    iconName: "sort"
                    variant: "ghost"
                    onClicked: {
                        backingVM.refreshModels()
                        pane.notify(backingVM.models.length === 0
                                    ? "No models in the vault's .data/models folder"
                                    : Fmt.count(backingVM.models.length, "model")
                                      + " found", false)
                    }
                }
                AppButton {
                    text: "Recheck devices"
                    iconName: "sort"
                    variant: "ghost"
                    enabledAction: backingVM.pythonPath !== "" && !backingVM.isProbing
                    onClicked: backingVM.probeDevices()
                }
            }

            // Model.
            Row {
                spacing: Theme.gapSm

                AppButton {
                    text: backingVM.selectedModel !== ""
                          ? "Model: " + backingVM.selectedModel
                          : "No model installed"
                    iconName: "sparkle"
                    variant: "ghost"
                    fontSize: Theme.fzSmall
                    enabledAction: backingVM.models.length > 0
                    onClicked: settingsModelMenu.popup()
                }

                Pill {
                    id: devicePill
                    anchors.verticalCenter: parent.verticalCenter
                    options: backingVM.deviceChoices
                    onSelected: function (index) {
                        backingVM.device = backingVM.deviceChoices[index]
                    }
                    Component.onCompleted: pane.syncPills()
                }
            }

            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                color: backingVM.deviceError !== ""
                       || backingVM.backendError !== ""
                       ? Theme.danger : Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzTiny
                text: {
                    if (backingVM.isProbing)
                        return "Checking what this interpreter can run on…"
                    if (backingVM.deviceError !== "")
                        return backingVM.deviceError
                    if (backingVM.backendError !== "")
                        return backingVM.backendError
                    if (backingVM.availableDevices.length > 0)
                        return "Available here: "
                             + backingVM.availableDevices.join(", ")
                             + (backingVM.backend !== ""
                                ? " — running the model with "
                                  + backingVM.backend
                                : "")
                    return "Models go in the vault's .data/models folder. A "
                         + ".ckpt needs a .yaml config there too — it does "
                         + "not have to share the checkpoint's name."
                }
            }
        }
    }

    Item { width: 1; height: Theme.gapLg }

    // Same Instantiator caveat as everywhere else: a Repeater inside a Menu
    // never registers its items as entries.
    NocturneMenu {
        id: settingsModelMenu

        Instantiator {
            model: typeof backingVM !== "undefined" ? backingVM.models : []
            onObjectAdded: function (index, object) {
                settingsModelMenu.insertItem(index, object)
            }
            onObjectRemoved: function (index, object) {
                settingsModelMenu.removeItem(object)
            }

            delegate: MenuRow {
                required property var modelData
                // Name the config when it was not obvious from the filenames,
                // so an inferred pairing is visible rather than magic.
                text: !modelData.usable
                      ? modelData.name + " — " + modelData.reason
                      : modelData.configName
                        ? modelData.name + "  (" + modelData.configName + ")"
                        : modelData.name
                iconName: modelData.name === backingVM.selectedModel ? "check" : ""
                enabled: modelData.usable
                onTriggered: backingVM.selectedModel = modelData.name
            }
        }
    }

    FileDialog {
        id: pythonDialog
        title: "Choose the Python interpreter that runs the model"
        nameFilters: Qt.platform.os === "windows"
                     ? ["Python (python.exe)", "Executables (*.exe)", "All files (*)"]
                     : ["All files (*)"]
        onAccepted: backingVM.pythonPath = selectedFile
    }

    FolderDialog {
        id: msstDialog
        title: "Choose the Music-Source-Separation-Training folder"
        onAccepted: backingVM.msstPath = selectedFolder
    }

    FileDialog {
        id: modelImportDialog
        title: "Choose a vocal-removal model"
        nameFilters: ["Model checkpoints (*.ckpt)", "All files (*)"]
        onAccepted: backingVM.importModel(selectedFile)
    }

    // Live whether or not this tab is the one on screen: a generation started
    // from the workbench can fail while the user is reading another tab, and
    // the workbench has an identical device pill, so whichever one is used both
    // end up showing the same thing.
    Connections {
        target: typeof backingVM !== "undefined" ? backingVM : null
        function onError_occurred(message) { pane.notify(message, true) }
        function onSettings_changed() { pane.syncPills() }
    }

    Connections {
        target: typeof playerVM !== "undefined" ? playerVM : null
        function onBacking_track_changed() { pane.syncPills() }
    }
}
