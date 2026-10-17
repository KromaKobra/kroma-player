import QtQuick
import QtQuick.Controls
import Nocturne

// A slim edge marker with a wider, stable mouse target along the thumb only.
// Expanding it never changes the viewport or moves the content underneath it.
ScrollBar {
    id: root
    policy: ScrollBar.AsNeeded
    // AsNeeded is normally enforced by the style's thumb. Our custom thumb
    // must hide the entire control itself, including its hover/drag target.
    readonly property bool needed: policy !== ScrollBar.AlwaysOff && size > 0 && size < 1
        && (orientation === Qt.Vertical ? height > 0 : width > 0)
    visible: needed
    hoverEnabled: true
    padding: 0
    implicitWidth: 12
    implicitHeight: 32
    z: 10

    Component.onCompleted: ScrollBarPriority.registerBar(root)
    Component.onDestruction: ScrollBarPriority.unregisterBar(root)

    function thumbContains(point) {
        var p = contentItem.mapFromItem(root, point.x, point.y)
        return p.x >= 0 && p.y >= 0 && p.x < contentItem.width && p.y < contentItem.height
    }

    // The empty track passes through to the pane divider or content below.
    // A drag started on the thumb keeps its grab when the pointer leaves it.
    containmentMask: QtObject {
        function contains(point: point): bool { return root.pressed || root.thumbContains(point) }
    }

    contentItem: Item {
        implicitWidth: 12
        implicitHeight: 32
        Rectangle {
            anchors.right: parent.right
            height: parent.height
            width: root.hovered || root.pressed ? 6 : 3
            radius: width / 2
            color: Theme.alpha(Theme.text, root.pressed ? 0.5 : root.hovered ? 0.34 : 0.18)
            Behavior on width {
                NumberAnimation { duration: Theme.prefs.reducedMotion ? 0 : Theme.fast; easing.type: Theme.easing }
            }
            Behavior on color {
                ColorAnimation { duration: Theme.prefs.reducedMotion ? 0 : Theme.fast }
            }
        }
    }
    background: Item {}
}
