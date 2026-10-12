import QtQuick
import Nocturne

LibraryCard {
    required property int index
    required property var albumId
    required property var name
    required property var coverHash
    required property var totalDuration
    contentTime: Number(totalDuration || 0)
    required property var trackCount
    required property var needsAttention
    required property var isComplete
    required property var primaryArtists
    required releaseDate

    entityId: Number(albumId)
    rowIndex: index
    playing: playerVM.currentSourceType === "album" && playerVM.currentSourceId === Number(albumId)
    count: Number(trackCount || 0)
    playable: Number(trackCount) > 0
    objectName: "albumCell"
    displayTitle: String(name || "")
    subtitle: String(primaryArtists || "")
    artworkHash: String(coverHash || "")
    centered: true
    attention: Boolean(needsAttention)
    complete: Boolean(isComplete)
}
