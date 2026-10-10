import QtQuick
import QtQuick.Controls
import Nocturne

// How to move the vault to another drive.
//
// Deliberately instructions rather than a button that does it. The vault holds
// the open track and cover databases and whatever is playing, so relocating it
// safely means closing the app first — at which point there is nothing left for
// the app to do but explain the steps.
Popup {
    id: root

    property string vaultPath: ""

    signal pathCopied()

    modal: true
    dim: true
    padding: 0
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: 540

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

    Overlay.modal: Rectangle {
        color: Theme.scrim
    }

    background: Rectangle {
        color: Theme.dialogBg
        radius: Theme.radiusLg
        border.width: 1
        border.color: Theme.lineHard
    }

    // One numbered step. `body` is the explanation; `extra` is optional content
    // shown underneath it, which is how step 2 gets its path field.
    component Step: Item {
        id: step

        property int number: 0
        property string title: ""
        property string body: ""
        property Component extra: null

        width: parent ? parent.width : 0
        implicitHeight: content.implicitHeight

        Rectangle {
            id: bullet
            anchors.top: parent.top
            anchors.topMargin: 1
            anchors.left: parent.left
            width: 22
            height: 22
            radius: 11
            color: Theme.accentSoft
            border.width: 1
            border.color: Theme.alpha(Theme.accent, 0.32)

            Text {
                anchors.centerIn: parent
                text: step.number
                color: Theme.accent
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzTiny
                font.weight: Font.DemiBold
                font.features: ({ "tnum": 1 })
            }
        }

        Column {
            id: content
            anchors.left: bullet.right
            anchors.leftMargin: Theme.gapSm
            anchors.right: parent.right
            spacing: 3

            Text {
                width: parent.width
                text: step.title
                wrapMode: Text.WordWrap
                color: Theme.text
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzBody
                font.weight: Font.DemiBold
            }

            Text {
                width: parent.width
                text: step.body
                visible: text !== ""
                wrapMode: Text.WordWrap
                lineHeight: 1.35
                color: Theme.dim
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }

            Loader {
                width: parent.width
                active: step.extra !== null
                visible: active
                sourceComponent: step.extra
            }
        }
    }

    // The vault path, selectable and copyable. Sits in step 2.
    Component {
        id: pathRow

        Rectangle {
            // implicit, not explicit: it is what the Loader sizes itself from.
            implicitHeight: 34
            radius: Theme.radius
            color: Theme.sunken
            border.width: 1
            border.color: Theme.line

            TextInput {
                id: pathField
                anchors.left: parent.left
                anchors.leftMargin: 11
                anchors.right: copyButton.left
                anchors.rightMargin: 4
                anchors.verticalCenter: parent.verticalCenter
                text: root.vaultPath
                readOnly: true
                selectByMouse: true
                clip: true
                color: Theme.dim
                selectionColor: Theme.accentSoft
                selectedTextColor: Theme.text
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }

            IconButton {
                id: copyButton
                anchors.right: parent.right
                anchors.rightMargin: 2
                anchors.verticalCenter: parent.verticalCenter
                icon: "copy"
                iconSize: 15
                diameter: 28
                tooltip: "Copy path"
                // QML has no clipboard API; a read-only TextInput's own copy()
                // is the standard way to reach one.
                onClicked: {
                    pathField.selectAll()
                    pathField.copy()
                    pathField.deselect()
                    root.pathCopied()
                }
            }
        }
    }

    contentItem: Column {
        spacing: Theme.gap
        topPadding: Theme.gapLg
        bottomPadding: Theme.gapLg
        leftPadding: Theme.gapLg
        rightPadding: Theme.gapLg

        Text {
            width: parent.width - Theme.gapLg * 2
            text: "Moving your vault"
            color: Theme.text
            font.family: Theme.displayFamily
            font.pixelSize: Theme.fzLarge
            font.weight: Font.DemiBold
            wrapMode: Text.WordWrap
        }

        Text {
            width: parent.width - Theme.gapLg * 2
            text: "The vault is one self-contained folder — your audio, the library "
                + "database, artwork and settings all live inside it. Moving it is a "
                + "plain file copy, so Kroma leaves it to you and your file manager."
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzBody
            lineHeight: 1.35
            wrapMode: Text.WordWrap
        }

        Column {
            width: parent.width - Theme.gapLg * 2
            spacing: Theme.gap

            Step {
                number: 1
                title: "Close Kroma"
                body: "The library databases stay open while the app is running, so "
                    + "nothing should be copied out from under it."
            }

            Step {
                number: 2
                title: "Find your vault"
                extra: pathRow
            }

            Step {
                number: 3
                title: "Copy the whole folder to its new home"
                body: "A USB drive, another disk, anywhere you like. Copy rather than "
                    + "move, so an interrupted transfer cannot lose anything."
            }

            Step {
                number: 4
                title: "Check the copy finished"
                body: "The new folder should match the original in size and file count "
                    + "before you go any further."
            }

            Step {
                number: 5
                title: "Move the original out of the way"
                body: "Rename it, or drag it somewhere as a backup. Kroma remembers "
                    + "where the vault was and will happily reopen the old one if it is "
                    + "still sitting there — this step is what makes it ask."
            }

            Step {
                number: 6
                title: "Reopen Kroma and choose the new location"
                body: "With the old folder gone Kroma cannot find the vault, so it shows "
                    + "the vault picker on startup. Point it at the copy."
            }

            Step {
                number: 7
                title: "Delete the backup"
                body: "Once your tracks, artwork and playlists all look right, the old "
                    + "copy is safe to remove."
            }
        }

        Item { width: 1; height: Theme.gapXs }

        Row {
            anchors.right: parent.right
            anchors.rightMargin: Theme.gapLg
            spacing: Theme.gapSm

            AppButton {
                text: "Got it"
                variant: "filled"
                onClicked: root.close()
            }
        }
    }
}
