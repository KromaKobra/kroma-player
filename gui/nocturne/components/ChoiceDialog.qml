import QtQuick
import QtQuick.Controls
import Nocturne

// Two-way choice. Used by the import flow, which asks a couple of questions
// before it starts matching files.
Popup {
    id: root

    property string heading: ""
    property string body: ""
    property string primaryText: "Continue"
    property string secondaryText: "Alternative"

    signal primary()
    signal secondary()

    modal: true
    dim: true
    padding: 0
    closePolicy: Popup.CloseOnEscape

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: 460

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
            width: root.width - Theme.gapLg * 2
            text: root.heading
            wrapMode: Text.WordWrap
            color: Theme.text
            font.family: Theme.displayFamily
            font.pixelSize: Theme.fzLarge
            font.weight: Font.DemiBold
        }

        Text {
            width: root.width - Theme.gapLg * 2
            text: root.body
            visible: text !== ""
            wrapMode: Text.WordWrap
            lineHeight: 1.4
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzBody
        }

        Row {
            anchors.right: parent.right
            anchors.rightMargin: Theme.gapLg
            spacing: Theme.gapSm

            AppButton {
                text: root.secondaryText
                variant: "ghost"
                onClicked: {
                    root.secondary()
                    root.close()
                }
            }
            AppButton {
                text: root.primaryText
                variant: "filled"
                onClicked: {
                    root.primary()
                    root.close()
                }
            }
        }
    }
}
