import QtQuick
import QtQuick.Controls
import Nocturne

// Popup chrome for every context menu in the interface.
Menu {
    id: root

    implicitWidth: 232
    padding: 6
    overlap: 0

    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.prefs.reducedMotion ? 0 : Theme.fast }
            NumberAnimation { property: "scale"; from: Theme.prefs.reducedMotion ? 1 : 0.96; to: 1; duration: Theme.prefs.reducedMotion ? 0 : Theme.fast
                              easing.type: Theme.easing }
        }
    }
    exit: Transition {
        NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Theme.prefs.reducedMotion ? 0 : Theme.fast }
    }

    background: Rectangle {
        color: Theme.menuBg
        radius: Theme.radius
        border.width: 1
        border.color: Theme.lineHard
    }
}
