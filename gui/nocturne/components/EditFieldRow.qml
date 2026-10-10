import QtQuick
import Nocturne

// One labelled row of the track editor: a name on the left, an input on the
// right, and an optional hint under the name for fields that accept more than
// one shape of answer.
//
// It owns its NocturneField rather than taking one, so a form is a list of
// labels and handlers with no per-row plumbing. `text` is assigned into (the
// editor pushes values in on load and revert); `edited` only fires for typing,
// which is what keeps those pushes from looking like user input.
Item {
    id: root

    property string label: ""
    property string hint: ""
    property alias text: input.text
    property alias placeholder: input.placeholder
    property alias readOnly: input.readOnly

    signal edited(string text)

    readonly property int labelWidth: 116

    implicitHeight: Math.max(input.implicitHeight, labels.implicitHeight)

    Column {
        id: labels
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        width: root.labelWidth - Theme.gap
        spacing: 2

        Text {
            width: parent.width
            text: root.label
            wrapMode: Text.WordWrap
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
        }
        Text {
            width: parent.width
            text: root.hint
            visible: text !== ""
            wrapMode: Text.WordWrap
            color: Theme.alpha(Theme.text, 0.3)
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzTiny
            lineHeight: 1.2
        }
    }

    NocturneField {
        id: input
        anchors.left: parent.left
        anchors.leftMargin: root.labelWidth
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        onEdited: function (text) { root.edited(text) }
    }
}
