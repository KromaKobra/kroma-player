import QtQuick
import Nocturne

// The control that gives one half of Now Playing the whole page, and gives it
// back. Invisible at rest on purpose: this is a page you look at, and a button
// parked under the artwork would become part of the picture. It comes up when
// the pointer is somewhere in the half it belongs to — `revealed`, which the
// view feeds from a HoverHandler on the half itself — and only to part
// opacity, so it reads as an offer rather than a control. Pointing at it
// brings it the rest of the way.
Item {
    id: root

    // Whether this half is the focused one, and whether there is a split to
    // act on at all. Below the view's `wide` threshold there is not, so the
    // handle is not offered rather than being offered and refusing.
    property bool focused: false
    property bool offered: true
    property bool revealed: false
    signal toggled()

    implicitWidth: 28
    implicitHeight: 28

    opacity: !offered || !revealed ? 0 : (hover.hovered ? 1.0 : 0.45)
    // One gate, read off the opacity rather than repeated from the two
    // properties that decide it. Nothing to press while it is not there, and
    // nothing for a hover to catch either — an invisible item is not
    // hit-tested at all.
    visible: opacity > 0

    Behavior on opacity { NumberAnimation { duration: Theme.normal } }

    HoverHandler { id: hover }

    IconButton {
        anchors.fill: parent
        icon: root.focused ? "panel" : "expand"
        iconSize: 15
        diameter: 28
        tooltip: root.focused ? "Split back into two halves"
                              : "Give this half the whole width"
        onClicked: root.toggled()
    }
}
