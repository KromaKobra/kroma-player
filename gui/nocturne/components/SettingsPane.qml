import QtQuick
import QtQuick.Controls
import Nocturne

// One tab of the Settings page: the scroll surface its controls sit on, and the
// glide that carries it on and off screen.
//
// Every tab is the same shape — a column of sections capped at a readable
// width — so the shape is written once here and each tab file declares only
// what is in it. Anything declared inside a SettingsPane lands in that column,
// visual or not, which is what lets a tab keep its own menus and dialogs beside
// the controls that open them.
Item {
    id: root

    // Which tab this is, and which one is up. `index < currentIndex` is the
    // whole of the direction logic: a tab to the left of the current one waits
    // off the left edge and one to the right waits off the right, so moving to
    // a higher index brings the new page in from the right and takes the old
    // one out to the left without anything having to remember which way the
    // last move went. It is still right when the tab was restored from
    // preferences rather than clicked.
    property int index: 0
    property int currentIndex: 0
    readonly property bool current: index === currentIndex

    // True while the Settings page itself is up. Combined with `current` below
    // so each tab can hang its own backend refresh off being looked at —
    // opening Settings no longer probes audio devices nobody asked about.
    property bool viewActive: false
    readonly property bool active: viewActive && current

    // The parent owns the Toast, for the reason ExportDialog gives: a page that
    // slides off screen has nowhere sensible to put one, and a message raised
    // on the tab you just left should not leave with it.
    signal notify(string message, bool isError)

    default property alias content: column.data

    // Short, not a screen's width. A full-width slide at Theme.normal across a
    // thousand-pixel page is around five thousand pixels a second, which reads
    // as a flinch rather than a movement — and the direction, which is the only
    // thing the gesture has to say, is lost in the blur. A hint of offset under
    // a cross-fade is what the eye actually reads it from. One page margin.
    property real slide: Theme.gapXl

    x: current ? 0 : (index < currentIndex ? -slide : slide)
    opacity: current ? 1 : 0
    visible: opacity > 0

    // `current` has already flipped by the time a Behavior it triggered runs,
    // so the travel knows which half of the move it is: arriving decelerates,
    // leaving accelerates, the rule the rest of the package follows.
    Behavior on x {
        NumberAnimation {
            duration: Theme.normal
            easing.type: root.current ? Theme.easing : Easing.InCubic
        }
    }

    // The fade, by contrast, is symmetric and unshaped — the same bare
    // `duration` main.qml uses to cross-fade the views. It has to be: two pages
    // whose opacities do not sum to one are both half-there at once, and the
    // overlap reads as a smear rather than as one page replacing another. Eased
    // or given different durations per direction, that is exactly what happens.
    // Quicker than the travel, so the arriving page is solid well before it
    // finishes moving.
    Behavior on opacity { NumberAnimation { duration: Theme.fast } }

    Flickable {
        id: flick
        anchors.fill: parent
        anchors.leftMargin: Theme.gapXl
        anchors.rightMargin: Theme.gapXl
        contentHeight: column.implicitHeight + Theme.gapXl
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        ScrollBar.vertical: ScrollBar { width: 7 }

        Column {
            id: column
            width: Math.min(flick.width, 720)
            spacing: Theme.gapXl
        }
    }

    // Gated on `current`, not on `active`, so the wheel keeps working while the
    // page is on screen. SmoothWheel installs a MouseArea in front of its
    // target and reparents itself to that target's parent — this Item — so
    // `enabled` reaches it. Without the gate, the tab being left is still on
    // screen for the length of the cross-fade and both panes would claim the
    // wheel, with declaration order deciding which won.
    //
    // Nothing else here reads `enabled`: AppButton dims on its own
    // `enabledAction`, and ToggleSwitch, NumberSetting and Pill never look at
    // it. So a leaving page does not flash grey on the way out.
    enabled: current

    SmoothWheel { target: flick }
}
