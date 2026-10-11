import QtQuick
import Nocturne

LibraryCard {
    required property int index
    required property var playlistId
    required property var name
    required property var coverHash
    required property var trackCount
    required property var totalDuration

    objectName: "playlistCell"
    displayTitle: String(name || "")
    subtitle: Fmt.count(Number(trackCount || 0), "track")
              + (Number(totalDuration) > 0 ? " · " + Fmt.longDuration(totalDuration) : "")
    artworkHash: String(coverHash || "")
    playable: Number(trackCount) > 0
    contextActions: true
}
