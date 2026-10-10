import QtQuick
import QtQuick.Controls
import Nocturne

// Modal confirmation card. Used before anything destructive.
Popup {
    id: root

    property string title: ""
    property string body: ""
    property string confirmText: "Confirm"
    property string cancelText: "Cancel"
    property bool danger: false

    signal confirmed()

    modal: true
    dim: true
    padding: 0
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: 420

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

    contentItem: Column {
        spacing: Theme.gap
        topPadding: Theme.gapLg
        bottomPadding: Theme.gapLg
        leftPadding: Theme.gapLg
        rightPadding: Theme.gapLg

        Text {
            width: parent.width - Theme.gapLg * 2
            text: root.title
            color: Theme.text
            font.family: Theme.displayFamily
            font.pixelSize: Theme.fzLarge
            font.weight: Font.DemiBold
            wrapMode: Text.WordWrap
        }

        Text {
            width: parent.width - Theme.gapLg * 2
            text: root.body
            visible: text !== ""
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzBody
            lineHeight: 1.35
            wrapMode: Text.WordWrap
        }

        Item { width: 1; height: Theme.gapXs }

        Row {
            anchors.right: parent.right
            anchors.rightMargin: Theme.gapLg
            spacing: Theme.gapSm

            AppButton {
                text: root.cancelText
                variant: "ghost"
                onClicked: root.close()
            }

            AppButton {
                text: root.confirmText
                variant: "filled"
                danger: root.danger
                onClicked: {
                    root.confirmed()
                    root.close()
                }
            }
        }
    }
}
