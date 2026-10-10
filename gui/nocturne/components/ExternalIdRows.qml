import QtQuick
import Nocturne

// The ids an enabled plugin declares for whatever an editor has open — one
// labelled row each, in the plugin's own words.
//
// `fields` is an editor's `externalIdFields`: `[{key, label, sourceLabel,
// value, saved, url, editable, iconUrl}]`. Which rows exist is the backend's
// answer and nothing here decides it; a plugin turned off takes its rows away,
// and taking a row away never clears what it held.
//
// Text is pushed in once, when a row is made, and never bound: a NocturneField
// owns its `text` and typing assigns to it, which would destroy a binding and
// take Revert with it — TrackEditView.syncFields() for the long version. The
// editor rebuilds the list on load and on revert, which is when a push is due.
//
// A kind with no parse rules cannot take typed input — there would be no saying
// what a pasted link means — so its row is read-only rather than missing.
Column {
    id: root

    property var fields: []

    // A row was typed into. The editor's `setExternalId(key, text)`.
    signal edited(string key, string text)

    spacing: Theme.gapSm

    Repeater {
        model: root.fields

        delegate: EditFieldRow {
            required property var modelData

            width: root.width
            label: String(modelData.sourceLabel || modelData.label || "")
            hint: String(modelData.label || "")
            readOnly: modelData.editable !== true
            objectName: "externalId:" + String(modelData.key || "")

            Component.onCompleted: text = String(modelData.value || "")
            onEdited: function (text) { root.edited(String(modelData.key), text) }
        }
    }
}
