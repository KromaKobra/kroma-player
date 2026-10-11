import QtQuick
import Nocturne

LibraryCard {
    required property int index
    required property var artistId
    required property var name
    required property var imageHash
    required property var trackCount
    required property var needsAttention

    objectName: "artistCell"
    displayTitle: String(name || "")
    subtitle: Fmt.count(Number(trackCount || 0), "track")
    artworkHash: String(imageHash || "")
    centered: true
    attention: Boolean(needsAttention)
    placeholderGlyph: false
}
