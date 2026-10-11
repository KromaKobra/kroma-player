import QtQuick
import QtQuick.Controls
import Nocturne
import "../artwork"
import "../library"

// TrackTable owns selection, playback and actions in either presentation.
FocusScope {
    id: cell
    required property var table
    readonly property var view: GridView.view
    objectName: "songCell"
    required property int index
    required property var uid
    required property var trackId
    required property var title
    required property var artist
    required property var coverHash
    required property var duration
    readonly property bool selected: Boolean(cell.table._selectionShapes[uid])
    readonly property bool playing: Number(trackId) > 0 && Number(trackId) === playerVM.current_track_id
    width: cell.view.cellWidth
    height: cell.view.cellHeight
    activeFocusOnTab: true
    Accessible.role: Accessible.ListItem
    Accessible.name: String(title || "") + ", " + String(artist || "")
    Accessible.selected: selected
    Accessible.onPressAction: cell.table.handleClick(uid, false, false)
    Keys.onSpacePressed: function(event) {
        cell.table.handleClick(uid, Boolean(event.modifiers & Qt.ControlModifier), Boolean(event.modifiers & Qt.ShiftModifier))
    }
    Keys.onReturnPressed: cell.table.playRow(index, Number(trackId))
    Keys.onMenuPressed: cell.table.openTrackMenu(uid, Number(trackId), index, String(title || ""))

    Rectangle {
        anchors.fill: parent
        anchors.margins: 3
        radius: Theme.radiusLg
        color: cell.selected || cellMouse.pressed ? Theme.accentSoft
               : cellMouse.containsMouse ? Theme.alpha(Theme.text, 0.06) : "transparent"
        border.width: cell.activeFocus || cell.playing ? 1 : 0
        border.color: Theme.accent
    }
    MouseArea {
        id: cellMouse
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        onClicked: function(event) {
            cell.forceActiveFocus(Qt.MouseFocusReason)
            if (event.button === Qt.RightButton)
                cell.table.openTrackMenu(cell.uid, Number(cell.trackId), cell.index, String(cell.title || ""))
            else
                cell.table.handleClick(cell.uid, Boolean(event.modifiers & Qt.ControlModifier), Boolean(event.modifiers & Qt.ShiftModifier))
        }
        onDoubleClicked: function(event) {
            if (event.button === Qt.LeftButton)
                cell.table.playRow(cell.index, Number(cell.trackId))
        }
    }
    CoverArt {
        x: 15; y: 15
        width: cell.view.artSize; height: width
        radius: Theme.radius
        tier: 512
        maskSource: cell.view.artMask
        hash: String(cell.coverHash || "")
        placeholderText: String(cell.title || "")
    }
    Button {
        width: 36; height: 36
        x: cell.view.cellWidth - width - 22
        y: cell.view.artSize - height + 8
        visible: cellMouse.containsMouse || hovered || visualFocus || cell.activeFocus || cell.playing
        Accessible.name: "Play " + String(cell.title || "song")
        contentItem: Item {
            Icon { anchors.centerIn: parent; name: "play"; size: 16; color: Theme.accentInk }
        }
        background: Rectangle {
            radius: height / 2
            color: parent.down ? Qt.darker(Theme.accent, 1.1) : Theme.accent
            border.width: parent.visualFocus ? 2 : 0
            border.color: Theme.text
        }
        onClicked: cell.table.playRow(cell.index, Number(cell.trackId))
    }
    Column {
        x: 15; y: cell.view.artSize + 25
        width: cell.view.artSize - more.width - Theme.gapXs
        spacing: 3
        Text {
            width: parent.width
            text: String(cell.title || "")
            elide: Text.ElideRight
            color: cell.playing ? Theme.accent : Theme.text
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzBody
            font.weight: Font.DemiBold
        }
        Text {
            width: parent.width
            text: String(cell.artist || "") + (Number(cell.duration) > 0 ? " · " + Fmt.duration(Number(cell.duration)) : "")
            elide: Text.ElideRight
            color: Theme.dim
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fzTiny
        }
    }
    ViewOptionsButton {
        id: more
        x: cell.view.cellWidth - width - 10
        y: cell.view.artSize + 23
        implicitWidth: 30; implicitHeight: 30
        Accessible.name: "Actions for " + String(cell.title || "song")
        onClicked: cell.table.openTrackMenu(cell.uid, Number(cell.trackId), cell.index, String(cell.title || ""))
    }
}
