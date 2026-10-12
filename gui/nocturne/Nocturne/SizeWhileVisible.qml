import QtQuick

// Gives a page the size of the space it fills, but only while it is visible.
//
// Pages stay alive while hidden so each keeps its scroll position and state.
// A hidden page has no reason to follow the space it would fill, though, and
// following it is not free: an invisible ListView or GridView still lays out
// on every geometry change, and every Text in it re-elides. Anchored, every
// page in the window relaid out on every frame of a sidebar drag or a window
// resize, to draw the one that is on screen.
//
// Hidden, the page keeps the size it last had. It catches up synchronously
// when it is shown — `visible` and the new size arrive in the same frame — so
// it is never drawn at a stale size. `visible` is effective visibility, so a
// sub-page also holds while the page it sits on is hidden.
QtObject {
    id: hold

    required property Item target
    required property real width
    required property real height

    // Pages that start hidden are still laid out once at startup, as they were
    // when anchored, so the first visit does not pay for it.
    Component.onCompleted: {
        target.width = width
        target.height = height
    }

    readonly property Binding _width: Binding {
        target: hold.target
        property: "width"
        value: hold.width
        when: hold.target.visible
        restoreMode: Binding.RestoreNone
    }
    readonly property Binding _height: Binding {
        target: hold.target
        property: "height"
        value: hold.height
        when: hold.target.visible
        restoreMode: Binding.RestoreNone
    }
}
