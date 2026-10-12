import QtQuick
import Nocturne

LibraryCard {
    required property int index
    required property var playlistId
    required property var name
    required property var coverHash
    required property var trackCount
    required property var totalDuration

    entityId: Number(playlistId)
    rowIndex: index
    playing: playerVM.currentSourceType === "playlist" && playerVM.currentSourceId === Number(playlistId)
    count: Number(trackCount || 0)
    playable: Number(trackCount) > 0
    objectName: "playlistCell"
    displayTitle: String(name || "")
    subtitle: !listMode && Number(totalDuration) > 0 ? Fmt.longDuration(totalDuration) : ""
    artworkHash: String(coverHash || "")
    contentTime: Number(totalDuration || 0)
    contextActions: true
}
