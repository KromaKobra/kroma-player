import QtQuick
import QtQuick.Controls
import Nocturne

// Right-hand details panel driven by sidebarVM, which hands over metadata
// already formatted for display. It reports its own visibility to the backend
// so no database or file reads happen while it is closed.
Item {
    id: root

    property bool open: false
    property bool drawSurface: true

    // Raised by the Edit button. The window owns navigation to the editor.
    signal editRequested(int trackId)

    SidebarSurface { anchors.fill: parent; visible: root.drawSurface }

    Binding {
        target: typeof sidebarVM !== "undefined" ? sidebarVM : null
        property: "active"
        value: root.open
    }

    // Chrome, not background: a press on the panel must not drop the track
    // selection it is showing. Behind the panel's own
    // content. See the ambient catcher in main.qml.
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
    }

    Rectangle {
        visible: root.drawSurface
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: 1
        color: Theme.prefs.ambient ? Theme.line : Theme.lineSoft
    }

    Flickable {
        id: flick
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.leftMargin: 1
        // Keep readable geometry during collapse. The outer panel clips and
        // carries this surface to the right instead of crushing its contents.
        width: Math.max(240, root.width - 1)
        contentHeight: content.implicitHeight + Theme.gapLg * 2
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        ScrollBar.vertical: ScrollBar { width: 6 }

        Column {
            id: content
            x: Theme.gapLg
            y: Theme.gapLg
            width: parent.width - Theme.gapLg * 2
            spacing: Theme.gap

            // ------------------------------------------------ empty state
            Column {
                width: parent.width
                spacing: Theme.gapSm
                visible: sidebarVM.mode === "empty"

                Item { width: 1; height: 40 }

                Icon {
                    anchors.horizontalCenter: parent.horizontalCenter
                    name: "info"
                    size: 30
                    color: Theme.alpha(Theme.text, 0.18)
                }
                Text {
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    text: "Select a track to see its details"
                    wrapMode: Text.WordWrap
                    color: Theme.faint
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzSmall
                }
            }

            // ------------------------------------------ multiple selection
            Column {
                width: parent.width
                spacing: Theme.gapSm
                visible: sidebarVM.mode === "multi"

                Text {
                    text: sidebarVM.selectionCount + " tracks selected"
                    color: Theme.text
                    font.family: Theme.displayFamily
                    font.pixelSize: Theme.fzMed
                    font.weight: Font.DemiBold
                }

                InfoRow { label: "Total time"; value: sidebarVM.combinedDuration }
                InfoRow { label: "Total size"; value: sidebarVM.combinedSize }
            }

            // ------------------------------------------------ single track
            Column {
                width: parent.width
                spacing: Theme.gap
                visible: sidebarVM.mode === "single"

                CoverArt {
                    width: parent.width
                    height: width
                    radius: Theme.radiusLg
                    tier: 512
                    hash: sidebarVM.coverHash
                    placeholderText: sidebarVM.title
                }

                Column {
                    width: parent.width
                    spacing: 4

                    Text {
                        width: parent.width
                        text: sidebarVM.title
                        wrapMode: Text.WordWrap
                        maximumLineCount: 3
                        elide: Text.ElideRight
                        color: Theme.text
                        font.family: Theme.displayFamily
                        font.pixelSize: Theme.fzMed
                        font.weight: Font.DemiBold
                    }
                    Text {
                        width: parent.width
                        text: sidebarVM.artists
                        wrapMode: Text.WordWrap
                        color: Theme.dim
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fzBody
                    }
                }

                // The panel's action row. Edit is always here — the panel only
                // ever describes one track, so there is never a question of
                // which one it means.
                Row {
                    spacing: Theme.gapSm

                    AppButton {
                        text: "Edit"
                        iconName: "edit"
                        variant: "ghost"
                        enabledAction: sidebarVM.trackId > 0
                        fontSize: Theme.fzSmall
                        hPadding: 12
                        implicitHeight: 32
                        onClicked: root.editRequested(sidebarVM.trackId)
                    }
                    // One per id an enabled plugin declares with a link, named
                    // the way that plugin names it. None with every plugin off:
                    // the ids are still on the track, and come back with it.
                    Repeater {
                        model: (sidebarVM.externalLinks || []).filter(function (link) {
                            return String(link.url || "") !== ""
                        })
                        delegate: AppButton {
                            required property var modelData
                            text: String(modelData.label || "")
                            iconName: "external"
                            variant: "ghost"
                            fontSize: Theme.fzSmall
                            hPadding: 12
                            implicitHeight: 32
                            onClicked: Qt.openUrlExternally(String(modelData.url))
                        }
                    }
                }

                Rectangle { width: parent.width; height: 1; color: Theme.line }

                Column {
                    width: parent.width
                    spacing: 1

                    InfoRow { label: "Album";    value: sidebarVM.album }
                    InfoRow { label: "Released"; value: sidebarVM.releaseDate }
                    InfoRow { label: "Genres";   value: sidebarVM.genres }
                    InfoRow { label: "Key";      value: sidebarVM.key }
                    InfoRow { label: "Tempo";    value: sidebarVM.tempo }
                    InfoRow { label: "Length";   value: sidebarVM.duration }
                }

                Rectangle { width: parent.width; height: 1; color: Theme.line }

                Column {
                    width: parent.width
                    spacing: 1

                    InfoRow { label: "Added";   value: sidebarVM.dateAdded }
                    InfoRow { label: "Size";    value: sidebarVM.fileSize }
                    InfoRow { label: "Local ID"; value: sidebarVM.localIdHex; mono: true }
                    InfoRow { label: "ISRC";    value: sidebarVM.isrc; mono: true }
                }

                Text {
                    width: parent.width
                    text: sidebarVM.copyright
                    visible: text !== ""
                    wrapMode: Text.WordWrap
                    color: Theme.faint
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzTiny
                    lineHeight: 1.3
                }
            }
        }
    }

    SmoothWheel { target: flick }
}
