import QtQuick
import QtQuick.Controls
import Nocturne
import "../controls"

/*
 * A plugin's terms, asked before it is turned on.
 *
 * Deliberately not a ConfirmDialog: this one is wider, scrolls, and renders the
 * plugin's own agreement text — handed over by `pluginsVM.consent_required`
 * and read from the plugin's `consent` file — rather than a string written
 * here. That is the point of it: what the user reads has to be the same text
 * the backend records them as having accepted, so there is exactly one copy of
 * it, and it ships with the plugin.
 *
 * Cancel closes and changes nothing. Only Agree emits, and the caller answers
 * that with `pluginsVM.acceptConsent(pluginId)`, which records the agreement
 * and turns the plugin on.
 */
Popup {
    id: root

    property string pluginId: ""
    property string pluginName: ""
    property string agreementText: ""

    signal agreed(string pluginId)

    function openFor(id, name, text) {
        pluginId = id
        pluginName = name
        agreementText = text
        open()
    }

    modal: true
    dim: true
    padding: 0
    // No click-outside dismissal: this is a question, and a stray click on the
    // scrim should not count as either answer. Escape still cancels.
    closePolicy: Popup.CloseOnEscape

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

    contentItem: Column {
        spacing: Theme.gap
        topPadding: Theme.gapLg
        bottomPadding: Theme.gapLg
        leftPadding: Theme.gapLg
        rightPadding: Theme.gapLg

        Text {
            width: parent.width - Theme.gapLg * 2
            text: "Before you turn on " + (root.pluginName || "this plugin")
            color: Theme.text
            font.family: Theme.displayFamily
            font.pixelSize: Theme.fzLarge
            font.weight: Font.DemiBold
            wrapMode: Text.WordWrap
        }

        // Capped rather than free-growing, so a longer revision of the terms
        // scrolls instead of pushing the buttons off the bottom of the screen.
        //
        // Wrapped in a plain Item rather than anchoring SmoothWheel as a
        // second Column child: Column would then give it its own layout slot
        // — sized to match the Flickable — and count it twice. Inside this
        // wrapper it's just an ordinary sibling.
        Item {
            width: parent.width - Theme.gapLg * 2
            height: termsFlick.height

            Flickable {
                id: termsFlick
                width: parent.width
                height: Math.min(terms.implicitHeight, 340)
                contentHeight: terms.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds

                ScrollBar.vertical: ScrollBar { width: 7 }

                Text {
                    id: terms
                    width: parent.width - 10
                    text: root.agreementText
                    textFormat: Text.MarkdownText
                    color: Theme.dim
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzBody
                    lineHeight: 1.35
                    wrapMode: Text.WordWrap
                }
            }

            SmoothWheel { target: termsFlick }
        }

        Item { width: 1; height: Theme.gapXs }

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
                text: "Agree"
                variant: "filled"
                onClicked: {
                    root.agreed(root.pluginId)
                    root.close()
                }
            }
        }
    }
}
