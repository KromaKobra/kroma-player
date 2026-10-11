import QtQuick
import QtQuick.Controls
import Nocturne
import "../controls"

// Pick a playlist for the given tracks, or type a name to create one.
// Preferred over a nested submenu: it scrolls, and it can create inline.
Popup {
    id: root

    property var trackIds: []

    modal: true
    dim: true
    padding: 0
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: 400
    height: Math.min(520, 200 + listView.contentHeight)

    property var _playlists: []

    function openFor(ids) {
        trackIds = ids || []
        _playlists = playlistsVM.playlistList()
        nameField.text = ""
        open()
    }

    function _add(playlistId) {
        if (trackIds.length === 0)
            return
        playlistsVM.addTracksToPlaylist(playlistId, trackIds)
        close()
    }

    function _createAndAdd() {
        var name = nameField.text.trim()
        if (name === "")
            return
        var id = playlistsVM.createPlaylist(name)
        if (id > 0)
            _add(id)
        else
            close()
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

    contentItem: Item {

        Column {
            id: header
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.margins: Theme.gapLg
            spacing: 4

            Text {
                text: "Add to playlist"
                color: Theme.text
                font.family: Theme.displayFamily
                font.pixelSize: Theme.fzLarge
                font.weight: Font.DemiBold
            }
            Text {
                text: Fmt.count(root.trackIds.length, "track") + " selected"
                color: Theme.dim
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzSmall
            }
        }

        ListView {
            id: listView
            anchors.top: header.bottom
            anchors.topMargin: Theme.gap
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: footer.top
            anchors.bottomMargin: Theme.gapSm
            anchors.leftMargin: Theme.gapSm
            anchors.rightMargin: Theme.gapSm
            clip: true
            model: root._playlists
            spacing: 2

            ScrollBar.vertical: ScrollBar { width: 6 }

            delegate: Rectangle {
                id: plRow
                required property var modelData

                width: ListView.view.width
                height: 42
                radius: Theme.radiusSm
                color: plMouse.containsMouse ? Theme.raised : "transparent"
                Behavior on color { ColorAnimation { duration: Theme.fast } }

                Icon {
                    id: plIcon
                    anchors.left: parent.left
                    anchors.leftMargin: 12
                    anchors.verticalCenter: parent.verticalCenter
                    name: "playlist"
                    size: 17
                    color: plMouse.containsMouse ? Theme.accent : Theme.faint
                }

                Text {
                    anchors.left: plIcon.right
                    anchors.leftMargin: 11
                    anchors.right: parent.right
                    anchors.rightMargin: 12
                    anchors.verticalCenter: parent.verticalCenter
                    text: plRow.modelData.name
                    elide: Text.ElideRight
                    color: Theme.text
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fzBody
                }

                MouseArea {
                    id: plMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root._add(plRow.modelData.playlistId)
                }
            }

            Text {
                anchors.centerIn: parent
                visible: listView.count === 0
                text: "No playlists yet"
                color: Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fzBody
            }
        }

        SmoothWheel { target: listView }

        Item {
            id: footer
            anchors.bottom: parent.bottom
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.margins: Theme.gapLg
            height: 40

            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.topMargin: -Theme.gap
                height: 1
                color: Theme.line
            }

            NocturneField {
                id: nameField
                anchors.left: parent.left
                anchors.right: createButton.left
                anchors.rightMargin: Theme.gapSm
                height: parent.height
                iconName: "plus"
                placeholder: "New playlist name…"
                clearable: false
                onAccepted: root._createAndAdd()
            }

            AppButton {
                id: createButton
                anchors.right: parent.right
                height: parent.height
                text: "Create"
                variant: "filled"
                enabledAction: nameField.text.trim() !== ""
                onClicked: root._createAndAdd()
            }
        }
    }
}
