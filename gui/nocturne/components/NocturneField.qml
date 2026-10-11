import QtQuick
import QtQuick.Controls
import Nocturne

// Text input with an optional leading icon and a clear button.
Item {
    id: root

    property alias text: field.text
    property alias placeholder: field.placeholderText
    property string iconName: ""
    property string accessibleLabel: placeholder
    property bool clearable: true
    property alias inputFocus: field.activeFocus
    property alias cursorPosition: field.cursorPosition
    // Shown and selectable, never typed into — and so never cleared either.
    property alias readOnly: field.readOnly

    // A single line has nowhere to put a multi-line paste. Qt's own answer is
    // to take the newlines in anyway, which renders as one unreadable run — so
    // set this and take `pastedLines` instead, and the paste never reaches the
    // field. The lyrics editor turns them into separate entries.
    //
    // Off by default, so no other field in the app changes behaviour.
    property bool forwardMultilinePaste: false
    signal pastedLines(var lines)

    signal accepted(string text)
    signal edited(string text)

    implicitHeight: 40
    implicitWidth: 260

    function clear() {
        field.text = ""
        root.edited("")
    }
    function forceFocus() { field.forceActiveFocus() }
    function selectAll() { field.selectAll() }

    Rectangle {
        anchors.fill: parent
        radius: Theme.radius
        color: Theme.sunken
        border.width: 1
        border.color: field.activeFocus ? Theme.alpha(Theme.accent, 0.55) : Theme.line

        Behavior on border.color { ColorAnimation { duration: Theme.fast } }
    }

    Icon {
        id: leading
        visible: root.iconName !== ""
        anchors.left: parent.left
        anchors.leftMargin: 12
        anchors.verticalCenter: parent.verticalCenter
        name: root.iconName
        size: 17
        color: field.activeFocus ? Theme.accent : Theme.faint
    }

    TextField {
        id: field
        Accessible.name: root.accessibleLabel
        anchors.fill: parent
        anchors.leftMargin: leading.visible ? 38 : 13
        anchors.rightMargin: clearButton.visible ? 36 : 13
        verticalAlignment: TextInput.AlignVCenter
        color: readOnly ? Theme.dim : Theme.text
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fzBody
        selectByMouse: true
        selectionColor: Theme.alpha(Theme.accent, 0.45)
        selectedTextColor: Theme.text
        placeholderTextColor: Theme.faint
        background: Item {}

        onTextEdited: root.edited(text)
        onAccepted: root.accepted(text)

        // Caught before Qt's own paste, because Qt's own paste would take the
        // newlines. Only when there is more than one line and somebody has
        // asked for them — a single-line paste is ordinary and falls straight
        // through.
        Keys.onPressed: function (event) {
            if (!root.forwardMultilinePaste || !event.matches(StandardKey.Paste))
                return
            var lines = Clipboard.lines()
            if (lines.length < 2)
                return
            root.pastedLines(lines)
            event.accepted = true
        }
        // Escape clears the field. Once it is empty there is nothing here to
        // clear, so it goes back to the page instead, where it drops the track
        // selection.
        Keys.onEscapePressed: function (event) {
            if (field.text === "")
                event.accepted = false
            else
                root.clear()
        }
    }

    IconButton {
        id: clearButton
        anchors.right: parent.right
        anchors.rightMargin: 5
        anchors.verticalCenter: parent.verticalCenter
        visible: root.clearable && !field.readOnly && field.text !== ""
        icon: "x"
        iconSize: 14
        diameter: 26
        onClicked: root.clear()
    }
}
