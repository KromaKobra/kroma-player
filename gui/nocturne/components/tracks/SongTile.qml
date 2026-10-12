import QtQuick
import Nocturne
import "../library"

// Songs adapt track roles and actions to the same card as the other library tabs.
LibraryCard {
    id: cell
    required property var table
    required property int index
    required property var uid
    required property var trackId
    required property var title
    required property var artist
    required property var coverHash
    required property var duration
    objectName: "songCell"
    controller: table
    entityId: Number(uid)
    rowIndex: index
    displayTitle: String(title || "")
    subtitle: String(artist || "") + (Number(duration) > 0 ? " · " + Fmt.duration(Number(duration)) : "")
    artworkHash: String(coverHash || "")
    playable: Number(trackId) > 0
    playing: Number(trackId) > 0 && Number(trackId) === playerVM.current_track_id
    onActivated: table.activateRow(uid, index, Number(trackId))
    onPlayRequested: table.playRow(index, Number(trackId))
    onContextRequested: table.openTrackMenu(uid, Number(trackId), index, String(title || ""))
    ViewOptionsButton {
        anchors.right: parent.right; anchors.bottom: parent.bottom
        anchors.rightMargin: 10; anchors.bottomMargin: 10
        implicitWidth: 30; implicitHeight: 30
        Accessible.name: "Actions for " + cell.displayTitle
        onClicked: cell.menu()
    }
}
