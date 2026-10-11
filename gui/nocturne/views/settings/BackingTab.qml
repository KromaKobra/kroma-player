import QtQuick
import QtQuick.Dialogs
import Nocturne
import "../../components"

SettingsPane {
    id: pane
    title: "Backing tracks"
    description: "Fine-tune instrumentals and set up vocal removal."
    onActiveChanged: {
        if (active && typeof backingVM !== "undefined") {
            backingVM.refreshModels()
            backingVM.probeDevicesIfNeeded()
        }
    }
    SettingsGroup {
        width: parent.width
        title: "Instrumental playback"
        visible: typeof playerVM !== "undefined"
        searchAvailable: typeof playerVM !== "undefined"
        ChoiceSetting {
            width: parent.width
            label: "When no instrumental exists"
            description: "Choose what happens as the queue advances in instrumental mode. Tracks you pick manually always play."
            segmented: true
            options: ["Play the mix", "Skip the track"]
            currentIndex: typeof playerVM !== "undefined" && playerVM.backingMissingBehavior === "skip" ? 1 : 0
            onSelected: function(index) { playerVM.backingMissingBehavior = index === 1 ? "skip" : "mix" }
        }
        SliderSetting {
            width: parent.width
            label: "Instrumental level"
            description: "Adjust every instrumental during playback without changing its file. For more boost, leave some room below full player volume."
            keywords: "gain loudness decibels volume"
            minimum: -12; maximum: 12; step: 0.5; decimals: 1; suffix: " dB"
            value: typeof playerVM !== "undefined" ? playerVM.backingGainDb : 0
            onCommitted: function(v) { playerVM.backingGainDb = v }
        }
    }
    SettingsGroup {
        width: parent.width
        title: "Vocal removal setup"
        description: "Generation uses your own Python installation with PyTorch and audio-separator, or a Music-Source-Separation-Training checkout."
        visible: typeof backingVM !== "undefined"
        searchAvailable: typeof backingVM !== "undefined"
        PathSetting {
            width: parent.width
            label: "Python interpreter"
            description: "Select the executable in the environment where you installed PyTorch."
            path: typeof backingVM !== "undefined" ? backingVM.pythonPath : ""
            placeholder: "No Python interpreter selected"
            actionText: "Choose Python…"
            onRequested: pythonDialog.open()
        }
        PathSetting {
            width: parent.width
            label: "Model code folder"
            description: "Optional: select a Music-Source-Separation-Training checkout. Leave empty to use audio-separator or an automatically discovered checkout."
            path: typeof backingVM !== "undefined" ? backingVM.msstPath : ""
            placeholder: "Automatic discovery / audio-separator"
            actionText: "Choose folder…"
            secondaryText: path ? "Use automatic discovery" : ""
            onRequested: msstDialog.open()
            onSecondaryRequested: backingVM.msstPath = ""
        }
        SettingRow {
            width: parent.width
            label: "Separation model"
            description: "Import a checkpoint to the vault. A .ckpt model also needs a compatible .yaml configuration."
            keywords: "download model import checkpoint yaml"
            SettingButton {
                width: parent.width
                text: typeof backingVM !== "undefined" && backingVM.selectedModel ? backingVM.selectedModel : "No model selected"
                enabled: typeof backingVM !== "undefined" && backingVM.models.length > 0
                onClicked: settingsModelMenu.popup()
            }
        }
        Flow {
            width: parent.width
            spacing: 8
            SettingButton { text: "Import model…"; onClicked: modelImportDialog.open() }
            SettingButton {
                text: "Rescan models"
                onClicked: {
                    backingVM.refreshModels()
                    pane.notify(Fmt.count(backingVM.models.length, "model") + " found", false)
                }
            }
        }
        ChoiceSetting {
            width: parent.width
            label: "Processing device"
            description: "Choose which processor generates instrumentals. Available choices depend on your Python environment."
            keywords: "gpu cpu cuda accelerator"
            options: typeof backingVM !== "undefined" ? backingVM.deviceChoices : []
            currentIndex: typeof backingVM !== "undefined" ? backingVM.deviceChoices.indexOf(backingVM.device) : -1
            onSelected: function(index) { backingVM.device = backingVM.deviceChoices[index] }
        }
        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            color: typeof backingVM !== "undefined" && (backingVM.deviceError || backingVM.backendError) ? Theme.danger : Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
            text: {
                if (typeof backingVM === "undefined") return ""
                if (backingVM.isProbing) return "Checking your Python environment…"
                return backingVM.deviceError || backingVM.backendError
                    || (backingVM.availableDevices.length ? "Available: " + backingVM.availableDevices.join(", ") + " · " + backingVM.backend : "Choose Python, then check available devices.")
            }
        }
        SettingButton {
            text: "Recheck devices"
            enabled: typeof backingVM !== "undefined" && backingVM.pythonPath !== "" && !backingVM.isProbing
            onClicked: backingVM.probeDevices()
        }
    }

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

    }

}
