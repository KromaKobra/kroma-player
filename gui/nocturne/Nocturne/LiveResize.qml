pragma Singleton
import QtQuick

// Whether a sidebar edge is moving — under the pointer, or springing to rest.
//
// Set by the window. Anything that reallocates GPU resources when its size
// changes can read this to hold its size while the edge moves, then settle
// once, exactly, when it stops. See CornerMask.qml.
QtObject {
    property bool active: false
}
