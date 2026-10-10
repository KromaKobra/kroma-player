pragma Singleton

import QtQuick

// Reading the clipboard.
//
// QML has no clipboard API. The way to reach one is to give a text item the
// call and read what lands — `TrackTable` already does the outbound half that
// way, with a hidden read-only `TextInput` whose own `copy()` is the whole
// mechanism. This is the same trick pointed inwards.
//
// The proxy is a **TextEdit** rather than a TextInput, and that is the entire
// reason this exists as a singleton rather than as two lines wherever it is
// needed: a single-line input is exactly what cannot be trusted to carry
// newlines, and newlines are the only thing anybody asks this for. Nothing is
// ever drawn — `paste()` works whether or not the item is on screen, the same
// note `clipboardProxy` carries.
QtObject {
    id: root

    property TextEdit _proxy: TextEdit { visible: false }

    // The clipboard's text, newlines and all.
    function text() {
        root._proxy.text = ""
        root._proxy.selectAll()
        root._proxy.paste()
        return root._proxy.text
    }

    // The clipboard as lines. Every newline flavour a clipboard can carry —
    // CRLF from a browser on Windows, a bare CR from something very old — has
    // to break in the same places as the LF the rest of the app assumes.
    function lines() {
        return root.text().split(/\r\n|\r|\n/)
    }

    // Whether the clipboard holds more than one line. What a single-line field
    // checks before deciding it cannot handle this paste by itself.
    function hasMultipleLines() {
        return /[\r\n]/.test(root.text())
    }
}
