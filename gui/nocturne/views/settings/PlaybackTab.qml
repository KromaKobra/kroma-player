import QtQuick
import Nocturne
import "../../components"

SettingsPane {
    id: pane
    title: "Playback"
    description: "Choose where you listen and how your queue remembers."
    SettingsGroup {
        width: parent.width
        title: "Audio output"
        visible: typeof playerVM !== "undefined"
        searchAvailable: typeof playerVM !== "undefined"
        ChoiceSetting {
            width: parent.width
            label: "Output device"
            description: "System default follows your desktop’s audio output, including connected headphones."
            keywords: "speaker headphones sound audio"
            options: typeof playerVM === "undefined" ? [] : ["System default"].concat(playerVM.audioOutputDevices.map(function(d) { return d.name }))
            currentIndex: typeof playerVM === "undefined" ? 0 : playerVM.audioOutputDevices.findIndex(function(d) { return d.id === playerVM.audioOutputDeviceId }) + 1
            onSelected: function(index) { playerVM.audioOutputDeviceId = index === 0 ? "" : playerVM.audioOutputDevices[index - 1].id }
        }
        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: typeof playerVM === "undefined" ? "" : (playerVM.audioOutputDeviceMissing ? "Selected device disconnected. Using " : "Currently playing through ") + playerVM.activeAudioOutputName + (playerVM.audioOutputDeviceMissing ? " until it returns." : ".")
            color: typeof playerVM !== "undefined" && playerVM.audioOutputDeviceMissing ? Theme.warning : Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }
    }
    SettingsGroup {
        width: parent.width
        title: "Queue & history"
        visible: typeof playerVM !== "undefined"
        searchAvailable: typeof playerVM !== "undefined"
        NumberSetting {
            width: parent.width
            label: "Counts as listened after"
            description: "Tracks played for less than this stay in the queue when you start something else."
            minimum: 0; maximum: 60; step: 1; suffix: "s"
            value: typeof playerVM !== "undefined" ? playerVM.listenedThresholdSeconds : 3
            onCommitted: function(v) { playerVM.listenedThresholdSeconds = v }
        }
        NumberSetting {
            width: parent.width
            label: "History length"
            description: "Number of played tracks to remember. The Previous button steps through this history."
            minimum: 10; maximum: 1000; step: 10; suffix: " tracks"
            value: typeof playerVM !== "undefined" ? playerVM.maxHistorySize : 100
            onCommitted: function(v) { playerVM.maxHistorySize = v }
        }
        NumberSetting {
            width: parent.width
            label: "Queue saved on exit"
            description: "Maximum tracks restored on your next launch. Set to 0 to start with an empty queue."
            minimum: 0; maximum: 5000; step: 50; suffix: " tracks"
            value: typeof playerVM !== "undefined" ? playerVM.persistedQueueLimit : 500
            onCommitted: function(v) { playerVM.persistedQueueLimit = v }
        }
    }
}
