import QtQuick

// A single vector icon. Renders at 2x for crispness on any scale factor.
Image {
    id: root

    property string name
    property color color: Theme.text
    property real strokeWidth: 1.7
    property int size: 20

    width: size
    height: size
    sourceSize.width: size * 2
    sourceSize.height: size * 2
    smooth: true
    mipmap: false
    fillMode: Image.PreserveAspectFit
    cache: true

    source: Icons.svg(name, color, strokeWidth)

    Behavior on color {
        ColorAnimation { duration: Theme.fast }
    }
}
