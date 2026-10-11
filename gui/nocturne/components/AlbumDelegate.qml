import QtQuick
import Nocturne

LibraryCard {
    required property int index
    required property var albumId
    required property var name
    required property var coverHash
    required property var trackCount
    required property var needsAttention
    required property var isComplete

    objectName: "albumCell"
    displayTitle: String(name || "")
    subtitle: Fmt.count(Number(trackCount || 0), "track")
    artworkHash: String(coverHash || "")
    centered: true
    attention: Boolean(needsAttention)
    complete: Boolean(isComplete)
}
