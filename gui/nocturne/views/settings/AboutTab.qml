import QtQuick
import Nocturne
import "../../components"

// The reference card: what the keys do, and what this package is.
SettingsPane {
    id: pane

    onActiveChanged: {
        if (!active)
            return
        // Prints the API version and the install path, both of which come from
        // a refresh.
        if (typeof guiVM !== "undefined")
            guiVM.refresh()
    }

    // ==================================================== shortcuts
    Column {
        id: keyboard
        width: parent.width
        spacing: Theme.gapSm

        SectionLabel { text: "KEYBOARD" }

        Grid {
            width: parent.width
            columns: 2
            rowSpacing: 2
            columnSpacing: Theme.gapLg

            Repeater {
                model: [
                    { keys: "Space",      what: "Play / pause" },
                    { keys: "Ctrl + →",   what: "Next track" },
                    { keys: "Ctrl + ←",   what: "Previous track" },
                    { keys: "→ / ←",      what: "Seek ±5 seconds" },
                    { keys: "↑ / ↓",      what: "Volume" },
                    { keys: "Ctrl + F",   what: "Search" },
                    { keys: "Ctrl + 1…6", what: "Jump to a section" },
                    { keys: "Esc",        what: "Close dialog / clear search" }
                ]

                delegate: Item {
                    required property var modelData
                    width: (keyboard.width - Theme.gapLg) / 2
                    height: 30

                    Text {
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        width: 96
                        text: modelData.keys
                        color: Theme.text
                        font.family: "Consolas"
                        font.pixelSize: Theme.fzSmall
                    }
                    Text {
                        anchors.left: parent.left
                        anchors.leftMargin: 104
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        text: modelData.what
                        elide: Text.ElideRight
                        color: Theme.faint
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzSmall
                    }
                }
            }
        }
    }

    Rectangle { width: parent.width; height: 1; color: Theme.line }

    // ======================================================== about
    Column {
        width: parent.width
        spacing: 4

        SectionLabel { text: "ABOUT" }

        // The application's own version, which this card never named. It is the
        // first thing any bug report needs — the GUI API version below says
        // which contract the interface was written against, and says nothing
        // about which build of Kroma is running it.
        Text {
            width: parent.width
            text: "Kroma Player "
                + (typeof updatesVM !== "undefined" ? updatesVM.currentVersion : "")
            visible: typeof updatesVM !== "undefined"
            color: Theme.text
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzBody
            font.weight: Font.Medium
        }

        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            lineHeight: 1.45
            color: Theme.faint
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzSmall
            text: "Nocturne is the official interface for Kroma Player, built against "
                + "GUI API version "
                + (typeof guiVM !== "undefined" ? guiVM.apiVersion : "?")
                + ".\nLoaded from "
                + (typeof guiVM !== "undefined" ? guiVM.currentBundle : "")
                + (typeof guiVM !== "undefined" && guiVM.currentBuildId
                   ? " (build " + guiVM.currentBuildId + ")" : "")
        }
    }

    Item { width: 1; height: Theme.gapLg }
}
