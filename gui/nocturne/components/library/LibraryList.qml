import QtQuick
import QtQuick.Controls
import Nocturne
import "../artwork"

// The same native, pooled list is also used by TrackTable's specialized rows.
ListView {
    id: root
    property var controller: null
    property bool compact: false
    property int rowHeight: compact ? 44 : 68
    property int artworkPadding: 8
    property real artworkRadius: Theme.radius
    property bool roundArtwork: false
    property int artSize: compact ? 30 : 48
    readonly property bool listMode: true
    readonly property int columns: 1
    readonly property real cellWidth: width
    readonly property int cellHeight: rowHeight
    property alias artMask: sharedMask
    property alias wheelHandler: wheel

    clip: true
    reuseItems: true
    cacheBuffer: rowHeight * 12
    maximumFlickVelocity: 6000
    flickDeceleration: 3000

    LibrarySelectionBackground { view: root }

    CornerMask {
        id: sharedMask
        width: root.artSize
        height: width
        radius: root.roundArtwork ? width / 2 : root.artworkRadius
    }
    ScrollBar.vertical: ScrollBar { width: 7 }
    SmoothWheel { id: wheel; target: root; visible: root.visible }
}
