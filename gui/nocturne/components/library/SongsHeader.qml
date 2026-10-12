import QtQuick
import Nocturne

// TrackQuery supplies cached totals; IDs are requested only for playback.
LibraryHeader {
    property int songCount: 0
    property int totalDuration: 0
    title: "Songs"
    summary: Fmt.count(songCount, "song") + " · " + Fmt.longDuration(totalDuration)
    actionPrefix: "songs"
    collectionName: "Song"
    showPlayback: true
    canPlay: songCount > 0
    canShuffle: songCount > 1
}
