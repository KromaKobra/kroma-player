import QtQuick
import QtQuick.Controls
import Nocturne
import "../artwork"

// Layout only. Models and delegates retain ownership of collection behavior.
GridView {
    id: root
    property int tileSize: 200
    property int artworkPadding: 15
    property int labelHeight: 56
    property real artworkRadius: Theme.radius
    property bool roundArtwork: false
    readonly property bool listMode: false
    readonly property int columns: Math.max(1, Math.floor(width / Math.max(120, Math.min(300, tileSize))))
    readonly property int artSize: Math.max(1, cellWidth - 2 * artworkPadding)
    property alias artMask: sharedMask
    signal viewportChanged()

    cellWidth: Math.max(1, Math.floor(width / columns))
    cellHeight: cellWidth + labelHeight
    clip: true
    reuseItems: true
    cacheBuffer: cellHeight * 2
    maximumFlickVelocity: 6000
    flickDeceleration: 3000
    onContentYChanged: viewportChanged()
    onHeightChanged: viewportChanged()
    onColumnsChanged: viewportChanged()
    onCellHeightChanged: viewportChanged()
    onCountChanged: viewportChanged()

    // One texture per view, never one mask layer per recycled delegate.
    CornerMask {
        id: sharedMask
        width: root.artSize
        height: width
        radius: root.roundArtwork ? width / 2 : root.artworkRadius
    }
    ScrollBar.vertical: ScrollBar { width: 7 }
    SmoothWheel { target: root; visible: root.visible }
}
