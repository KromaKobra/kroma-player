import QtQuick
import Nocturne

// The glide a page makes as it is pushed onto, or popped off, its tab.
//
// Opening something inside a tab — an artist from the Artists wall, its editor
// from the artist — brings the new page in from the right; Back takes it out
// the way it came while the page underneath returns from the left. Switching
// tabs moves nothing sideways: there is no "deeper" between two tabs, so it
// stays the plain cross-fade every view already makes.
//
// The window says which way a navigation goes *before* it changes which page
// is current, so `direction` is already right when `current` flips: +1 deeper
// into the tab, -1 back up it, 0 to another tab. One page margin of travel, as
// in SettingsPane, and for the same reason: the direction is all the movement
// has to say, and a full-width slide says it as a flinch.
//
// Driven imperatively from `current` rather than bound, because where a page
// waits depends on the move that is happening, not on any state that lasts.
// A page grabbed back mid-glide — Back pressed before the push has landed —
// retargets from where it is on screen rather than jumping to its start.
Translate {
    id: root

    property bool current: false
    property int direction: 0

    readonly property real distance: Theme.prefs.reducedMotion ? 0 : 12

    onCurrentChanged: {
        var start = glide.running ? x : (current ? direction * distance : x)
        glide.stop()
        glide.from = start
        glide.to = current ? 0 : -direction * distance
        // Arriving decelerates, leaving accelerates — the rule the package's
        // other glides follow.
        glide.easing.type = current ? Theme.easing : Easing.InCubic
        glide.start()
    }

    property NumberAnimation glide: NumberAnimation {
        target: root
        property: "x"
        duration: Theme.prefs.reducedMotion ? 0 : Theme.normal
    }
}
