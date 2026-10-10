import QtQuick
import Nocturne
import "../../components"

// Backend settings, not Nocturne's: they change what the queue does, so they
// bind to playerVM rather than settingsVM (which only ever holds this package's
// own presentation state).
SettingsPane {
    id: pane

    // Nothing to refresh: every property here is live-bound to playerVM.

    Column {
        width: parent.width
        spacing: Theme.gap
        visible: typeof playerVM !== "undefined"

        // No SectionLabel: the tab above already says what this is. The tabs
        // holding more than one section keep theirs.
        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: "The queue is meant to be resilient — anything still in it will "
                + "actually be heard unless you say otherwise. A track you barely "
                + "started never enters history; if you play something else it just "
                + "drops down to play next."
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }

        NumberSetting {
            width: parent.width
            label: "Counts as listened after"
            description: "How long a track has to play before it is recorded in "
                       + "history. Below this, starting something else moves it "
                       + "down the queue instead."
            minimum: 0
            maximum: 60
            step: 1
            decimals: 0
            suffix: "s"
            value: typeof playerVM !== "undefined" ? playerVM.listenedThresholdSeconds : 3
            onCommitted: function (v) { playerVM.listenedThresholdSeconds = v }
        }

        NumberSetting {
            width: parent.width
            label: "History length"
            description: "How many played tracks to keep. History is also what the "
                       + "previous button steps back through."
            minimum: 10
            maximum: 1000
            step: 10
            value: typeof playerVM !== "undefined" ? playerVM.maxHistorySize : 100
            onCommitted: function (v) { playerVM.maxHistorySize = v }
        }

        NumberSetting {
            width: parent.width
            label: "Queue saved on exit"
            description: "How much of the queue to restore next time you open "
                       + "Kroma Player."
            minimum: 0
            maximum: 5000
            step: 50
            value: typeof playerVM !== "undefined" ? playerVM.persistedQueueLimit : 500
            onCommitted: function (v) { playerVM.persistedQueueLimit = v }
        }

        // Output device. A menu rather than a Pill because the list is whatever
        // this machine happens to have — it is not a fixed set of choices, and
        // it changes while the page is open.
        Column {
            width: parent.width
            spacing: Theme.gapSm

            Text {
                text: "Output device"
                color: Theme.text
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzBody
            }

            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                text: "Where playback comes out. Left on the system default it "
                    + "follows whatever your machine is using, so connecting "
                    + "headphones moves the audio to them."
                color: Theme.dim
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }

            AppButton {
                text: typeof playerVM === "undefined" ? "System default"
                      : playerVM.audioOutputDeviceId === ""
                        ? "System default  (" + playerVM.activeAudioOutputName + ")"
                        : playerVM.activeAudioOutputName
                iconName: "volumeHigh"
                variant: "ghost"
                fontSize: Theme.fzSmall
                onClicked: outputDeviceMenu.popup()
            }

            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                visible: typeof playerVM !== "undefined"
                         && playerVM.audioOutputDeviceMissing
                text: "The device you picked is not connected. Playing on "
                    + (typeof playerVM !== "undefined"
                       ? playerVM.activeAudioOutputName : "") + " until it comes back."
                color: Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzTiny
            }
        }
    }

    Item { width: 1; height: Theme.gapLg }

    NocturneMenu {
        id: outputDeviceMenu

        // Fixed first entry, so "follow the system" is a choice in the same list
        // rather than a separate control. The Instantiator below inserts from
        // index 1 to keep it at the top.
        MenuRow {
            text: "System default"
            iconName: typeof playerVM !== "undefined"
                      && playerVM.audioOutputDeviceId === "" ? "check" : ""
            onTriggered: playerVM.audioOutputDeviceId = ""
        }

        // Same Instantiator caveat as everywhere else: a Repeater inside a Menu
        // never registers its items as entries.
        Instantiator {
            model: typeof playerVM !== "undefined" ? playerVM.audioOutputDevices : []
            onObjectAdded: function (index, object) {
                outputDeviceMenu.insertItem(index + 1, object)
            }
            onObjectRemoved: function (index, object) {
                outputDeviceMenu.removeItem(object)
            }

            delegate: MenuRow {
                required property var modelData
                text: modelData.name
                iconName: modelData.id === playerVM.audioOutputDeviceId ? "check" : ""
                onTriggered: playerVM.audioOutputDeviceId = modelData.id
            }
        }
    }
}
