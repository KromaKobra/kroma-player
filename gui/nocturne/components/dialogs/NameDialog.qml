import QtQuick
import QtQuick.Controls
import Nocturne
import "../controls"

// Single-field dialog: a heading, one line to type into, cancel and confirm.
//
// Named for what it was first asked to do — playlists — and kept general
// enough for anything else shaped the same way, which so far means a plugin's
// import source asking for a line of text. The three openers below set every label they depend on rather
// than any of them, so a dialog reopened for a different job cannot come up
// wearing the last one's prompt.
Popup {
    id: root

    property string heading: "New playlist"
    property string actionText: "Create"
    property string iconName: "playlist"
    property string placeholder: "Playlist name"
    property int editId: -1

    // `editId` is -1 for anything that is not a rename, which is every caller
    // but one. The field is the payload; the id only says what to do with it.
    signal submitted(string name, int editId)

    modal: true
    dim: true
    padding: 0
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: 400

    // The general opener. Every label is an argument because every label is
    // stateful: leave one out and it keeps whatever the previous caller set.
    function openFor(headingText, action, icon, hint, initialText, id) {
        heading = headingText
        actionText = action
        iconName = icon
        placeholder = hint
        editId = id === undefined ? -1 : id
        field.text = initialText === undefined ? "" : initialText
        open()
        field.forceFocus()
    }

    function openBlank() {
        openFor("New playlist", "Create", "playlist", "Playlist name")
    }

    function openRename(id, currentName) {
        openFor("Rename playlist", "Rename", "playlist", "Playlist name",
                currentName, id)
    }

    function _submit() {
        var name = field.text.trim()
        if (name === "")
            return
        root.submitted(name, root.editId)
        root.close()
    }

    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.normal }
            NumberAnimation { property: "scale"; from: 0.94; to: 1
                              duration: Theme.normal; easing.type: Theme.easing }
        }
    }
    exit: Transition {
        NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Theme.fast }
    }

    Overlay.modal: Rectangle { color: Theme.scrim }

    background: Rectangle {
        color: Theme.dialogBg
        radius: Theme.radiusLg
        border.width: 1
        border.color: Theme.lineHard
    }

    contentItem: Column {
        spacing: Theme.gap
        topPadding: Theme.gapLg
        bottomPadding: Theme.gapLg
        leftPadding: Theme.gapLg
        rightPadding: Theme.gapLg

        Text {
            text: root.heading
            color: Theme.text
            font.family: Theme.displayFamily
            font.pixelSize: Theme.fzLarge
            font.weight: Font.DemiBold
        }

        NocturneField {
            id: field
            width: root.width - Theme.gapLg * 2
            iconName: root.iconName
            placeholder: root.placeholder
            clearable: false
            onAccepted: root._submit()
        }

        Row {
            anchors.right: parent.right
            anchors.rightMargin: Theme.gapLg
            spacing: Theme.gapSm

            AppButton {
                text: "Cancel"
                variant: "ghost"
                onClicked: root.close()
            }
            AppButton {
                text: root.actionText
                variant: "filled"
                enabledAction: field.text.trim() !== ""
                onClicked: root._submit()
            }
        }
    }
}
