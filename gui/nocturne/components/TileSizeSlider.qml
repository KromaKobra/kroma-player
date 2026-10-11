import QtQuick
import QtQuick.Controls
import Nocturne

// Stops represent column counts, so every movement changes the visible grid.
// Store the resulting target width so the preference survives window resizing.
Row {
    id: root

    // The current target width, pushed in from the preference. Not written to
    // directly — the owner decides what a request does with it.
    property real tileSize: 170
    property real minimum: 120
    property real maximum: 300

    // The grid's width, which is what turns a width into a column count. Zero
    // until the wall has been laid out, and `available` is false until then.
    property real viewportWidth: 0

    // Allow one column in narrow windows, just like the collection grids.
    property int minColumns: 1
    property int sliderWidth: 76

    signal sizeRequested(int tileSize)

    spacing: 3
    height: 30

    // ------------------------------------------------------------- the stops

    function _colsFor(t) {
        return Math.max(minColumns, Math.floor(viewportWidth / Math.max(1, t)))
    }

    // Smaller tiles mean more columns, so the minimum tile size is the maximum
    // column count. Inverted names would read as a typo everywhere below.
    readonly property int _maxCols: _colsFor(minimum)
    readonly property int _minCols: _colsFor(maximum)
    readonly property int _span: Math.max(1, _maxCols - _minCols)

    // Which stop the current size sits on, clamped in case the size pushed in
    // came from outside the range this viewport can reach.
    readonly property int columns:
        Math.max(_minCols, Math.min(_maxCols, _colsFor(tileSize)))

    // Inverted against the column count: dragging right means fewer columns
    // and bigger tiles, which is the direction the icons promise.
    readonly property real _fraction: (_maxCols - columns) / _span

    // On a viewport too narrow to offer more than one layout there is nothing
    // to drag, so the control leaves the heading rather than sitting inert.
    readonly property bool available: _maxCols > _minCols
    visible: available

    // Ask for a column count. Public so a test can drive the control without
    // synthesising mouse events — and so the arithmetic that matters is
    // reachable in one call rather than only through a drag.
    function applyColumns(cols) {
        var c = Math.max(_minCols, Math.min(_maxCols, Math.round(cols)))
        var size = Math.floor(viewportWidth / Math.max(1, c))
        root.sizeRequested(Math.round(Math.max(minimum, Math.min(maximum, size))))
    }

    // ---------------------------------------------------------------- shape

    Icon {
        anchors.verticalCenter: parent.verticalCenter
        name: "tilesSmall"
        size: 14
        color: Theme.faint
    }

    Slider {
        id: slider
        objectName: "tileSizeControl"
        width: root.sliderWidth
        height: root.height
        anchors.verticalCenter: parent.verticalCenter
        from: 0
        // Keep a fixed range: a changing maximum can clamp the initial value
        // before the viewport has finished laying out.
        to: 1
        stepSize: 1 / root._span
        snapMode: Slider.SnapAlways
        value: root._fraction
        enabled: root.available
        Accessible.name: "Tile size"
        Accessible.description: Fmt.count(root.columns, "across", "across")
        onMoved: root.applyColumns(root._maxCols - slider.value * root._span)

        background: Rectangle {
            x: slider.leftPadding
            y: slider.topPadding + slider.availableHeight / 2 - height / 2
            width: slider.availableWidth
            height: 4
            radius: 2
            color: Theme.alpha(Theme.text, 0.13)
            Rectangle {
                width: slider.visualPosition * parent.width
                height: parent.height
                radius: 2
                color: Theme.accent
            }
        }
        handle: Rectangle {
            x: slider.leftPadding + slider.visualPosition * (slider.availableWidth - width)
            y: slider.topPadding + slider.availableHeight / 2 - height / 2
            width: 12; height: 12; radius: 6
            color: slider.pressed ? Theme.accent : Theme.text
            border.width: slider.visualFocus ? 2 : 0
            border.color: Theme.accent
            opacity: slider.enabled ? 1 : 0.4
        }
        ToolTip.text: Fmt.count(root.columns, "across", "across")
        ToolTip.visible: hovered || pressed
    }

    Icon {
        anchors.verticalCenter: parent.verticalCenter
        name: "tilesLarge"
        size: 14
        color: Theme.faint
    }
}
