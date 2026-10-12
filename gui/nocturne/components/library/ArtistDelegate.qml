import QtQuick
import Nocturne

LibraryCard {
    required property int index
    required property var artistId
    required property var name
    required property var imageHash
    required property var totalDuration
    contentTime: Number(totalDuration || 0)
    required property var trackCount
    required property var needsAttention

    entityId: Number(artistId)
    rowIndex: index
    playing: playerVM.currentSourceType === "artist" && playerVM.currentSourceId === Number(artistId)
    count: Number(trackCount || 0)
    playable: Number(trackCount) > 0
    objectName: "artistCell"
    displayTitle: String(name || "")
    subtitle: listMode ? "" : Fmt.count(Number(trackCount || 0), "track")
    artworkHash: String(imageHash || "")
    centered: true
    attention: Boolean(needsAttention)
    placeholderGlyph: false
}
