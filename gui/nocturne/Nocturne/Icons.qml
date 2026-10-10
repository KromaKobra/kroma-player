pragma Singleton

import QtQuick

// Vector icon set, drawn inline as SVG data URIs. Keeping the geometry in QML
// means the package ships no image files and every icon takes its colour and
// stroke weight from the caller.
//
// All icons are drawn on a 24x24 grid. Stroke icons inherit `stroke-width`;
// the few solid shapes (transport controls) are listed in `filled`.
QtObject {
    id: root

    readonly property var filled: [
        "play", "pause", "prev", "next", "more", "grip", "sparkle", "dot"
    ]

    readonly property var shapes: ({
        // ------------------------------------------------------- transport
        "play":  '<path d="M5.5,2.54A 1.95,1.95 0 0 0 4.52,2.8 1.95,1.95 0 0 0 3.54,4.49V 19.51a 1.95,1.95 0 0 0 2.93,1.69l 13.01,-7.51a 1.95,1.95 0 0 0 0,-3.38L 6.48,2.8A 1.95,1.95 0 0 0 5.5,2.54Z"/>',
        "pause": '<path d="m 6.755,3.28 c -0.74,0 -1.33,0.59 -1.33,1.32 v 14.8 c 0,0.73 0.59,1.32 1.33,1.32 h 1.01 c 0.73,0 1.32,-0.59 1.32,-1.32 V 4.6 c 0,-0.73 -0.59,-1.32 -1.32,-1.32 z m 9.48,0 c -0.73,0 -1.32,0.59 -1.32,1.32 v 14.8 c 0,0.73 0.59,1.32 1.32,1.32 h 1.02 c 0.73,0 1.32,-0.59 1.32,-1.32 V 4.6 c 0,-0.73 -0.59,-1.32 -1.32,-1.32 z"/>',
        "prev":  '<path d="m 7.28,4.21 c 0.65,0 1.18,0.53 1.18,1.18 V 10.17 L 17.78,4.79 a 1.53,1.53 0 0 1 0.77,-0.2 1.53,1.53 0 0 1 0.77,0.2 1.53,1.53 0 0 1 0.77,1.32 V 17.89 a 1.53,1.53 0 0 1 -2.3,1.32 L 8.46,13.83 v 4.78 c 0,0.65 -0.53,1.18 -1.18,1.18 h -0.91 c -0.65,0 -1.18,-0.53 -1.18,-1.18 V 5.39 c 0,-0.65 0.53,-1.18 1.18,-1.18 z"/>',
        "next":  '<path d="m 17.99,4.21 c -0.65,0 -1.18,0.53 -1.18,1.18 V 10.17 L 7.49,4.79 A 1.53,1.53 0 0 0 6.72,4.58 1.53,1.53 0 0 0 5.95,4.79 1.53,1.53 0 0 0 5.19,6.11 V 17.89 a 1.53,1.53 0 0 0 2.3,1.32 l 9.32,-5.38 v 4.78 c 0,0.65 0.53,1.18 1.18,1.18 h 0.91 c 0.65,0 1.18,-0.53 1.18,-1.18 V 5.39 c 0,-0.65 -0.53,-1.18 -1.18,-1.18 z"/>',

        "shuffle": '<path d="M16 3.5h4.5V8"/><path d="M3.5 20.5L20.5 3.5"/>'
                 + '<path d="M16 20.5h4.5V16"/><path d="M14.5 14.5l6 6"/>'
                 + '<path d="M3.5 3.5l6 6"/>',
        "repeat":  '<path d="M17 2.5l3.5 3.5L17 9.5"/>'
                 + '<path d="M3.5 11.5v-1a4 4 0 014-4h13"/>'
                 + '<path d="M7 21.5L3.5 18 7 14.5"/>'
                 + '<path d="M20.5 12.5v1a4 4 0 01-4 4h-13"/>',

        "volumeHigh": '<path d="M11 4.5L6 9H2.5v6H6l5 4.5z"/>'
                    + '<path d="M15.2 8.6a4.8 4.8 0 010 6.8"/>'
                    + '<path d="M18.4 5.4a9.3 9.3 0 010 13.2"/>',
        "volumeLow":  '<path d="M11 4.5L6 9H2.5v6H6l5 4.5z"/>'
                    + '<path d="M15.2 8.6a4.8 4.8 0 010 6.8"/>',
        "volumeMute": '<path d="M11 4.5L6 9H2.5v6H6l5 4.5z"/>'
                    + '<path d="M16.5 9.5l5 5"/><path d="M21.5 9.5l-5 5"/>',

        // ---------------------------------------------------- navigation
        "disc":     '<circle cx="12" cy="12" r="8.8"/><circle cx="12" cy="12" r="2.4"/>',
        "queue":    '<path d="M 12.48,6.5H 19.87"/><path d="M 8,12H 19.88"/><path d="M 8,17.5H 19.88"/>'
                  + '<path d="m 3.57,3.44 c 0,0.39 0,6.03 0,6.03 0,0.56 0.53,0.77 0.84,0.47 0.1,-0.1 3.5,-2.77 3.72,-2.94 C 8.4,6.78 8.34,6.22 8.09,5.97 8,5.89 4.64,3.221 3.38,3.01 4.07,2.76 3.57,3.13 3.57,3.44Z"/>',
        "playlist": '<path d="M 4.34,4.35H 12.5"/><path d="M 4.34,9.45H 12.5"/><path d="M 4.34,14.58H 6.4"/>'
                  + '<circle cx="13.56" cy="16.61" r="3"/><path d="M 16.6,16.41V 6.64c 0,-1.33 1.08,-2.34 2.28,-2.34h 0.78"/>',
        "search":   '<circle cx="10.8" cy="10.8" r="6.8"/><path d="M20.5 20.5l-4.8-4.8"/>',
        "library":  '<rect x="3.5" y="3.5" width="7" height="7" rx="1.6"/>'
                  + '<rect x="13.5" y="3.5" width="7" height="7" rx="1.6"/>'
                  + '<rect x="3.5" y="13.5" width="7" height="7" rx="1.6"/>'
                  + '<rect x="13.5" y="13.5" width="7" height="7" rx="1.6"/>',
        "settings": '<path d="M3.5 6.5h9"/><path d="M17.5 6.5h3"/>'
                  + '<path d="M3.5 12h4"/><path d="M12.5 12h8"/>'
                  + '<path d="M3.5 17.5h9"/><path d="M17.5 17.5h3"/>'
                  + '<circle cx="15" cy="6.5" r="2.2"/><circle cx="10" cy="12" r="2.2"/>'
                  + '<circle cx="15" cy="17.5" r="2.2"/>',
        "clock":    '<circle cx="12" cy="12" r="8.8"/><path d="M12 6.6V12l3.8 2.4"/>',
        "mic":      '<rect x="9" y="2.6" width="6" height="11" rx="3"/>'
                  + '<path d="M5.4 11.2a6.6 6.6 0 0013.2 0"/><path d="M12 17.8v3.6"/>',
        "stage-mic": '<path d="M10.97 9.88 3.13 16.64c-.47.45-.63 1.24-.31 1.77l.03.03c.31.57 1.04.83 1.61.54 9.48-4.3 8.5-3.85 9.48-4.3.57-.25.76-.91.44-1.45l-1.93-3.13c-.31-.54-.98-.63-1.45-.22Z"/>'
                  + '<path d="M13.72 6.72c-.38.51-.25 1.45.06 1.99l2.09 3.41c.31.54 1.1 1.08 1.74.98 1.35-.25 2.59-1.16 3.09-2.59.79-2.05-.28-4.36-2.37-5.15-1.7-.63-3.57-.03-4.61 1.36Z"/>',

        // -------------------------------------------------------- actions
        "download": '<path d="M12 3.4v11.8"/><path d="M7.4 10.6L12 15.2l4.6-4.6"/>'
                  + '<path d="M4 19.6h16"/>',
        "check":    '<path d="M4.6 12.4l4.9 4.9L19.4 6.7"/>',
        "complete": '<path d="m 22.81 12 c 0 1.49 -1.98 2.45 -2.44 3.54   -0.46 1.09 0.25 3.13 -0.73 4.1   -0.98 0.98 -3.02 0.27 -4.1 0.73   -1.09 0.46 -2.04 2.44 -3.54 2.44   -1.49 0 -2.45 -1.98 -3.54 -2.44 C 7.38 19.91 5.34 20.62 4.36 19.64   3.38 18.66 4.09 16.62 3.63 15.54   3.17 14.45 1.19 13.49 1.19 12 c 0 -1.49 1.98 -2.45 2.44 -3.54 C 4.09 7.38 3.38 5.34 4.36 4.36   5.34 3.38 7.38 4.09 8.46 3.63   9.55 3.17 10.51 1.19 12 1.19 c 1.49 0 2.45 1.98 3.54 2.44   1.09 0.46 3.13 -0.25 4.1 0.73   0.98 0.98 0.27 3.02 0.73 4.1 C 20.83 9.55 22.81 10.51 22.81 12 Z"/>'
                  + '<path d="M 8.02 12.13 10.52 14.64 15.98 9.36"/>',
        // Two links of a chain: this thing is tied to an entry somewhere else.
        // What is drawn beside an identified artist or album when the plugin
        // that identified it ships no picture of its own.
        "link":     '<path d="M10 13.5a4.6 4.6 0 006.9.5l2.8-2.8a4.6 4.6 0 00-6.5-6.5l-1.6 1.6"/><path d="M14 10.5a4.6 4.6 0 00-6.9-.5l-2.8 2.8a4.6 4.6 0 006.5 6.5l1.6-1.6"/>',
        "plus":     '<path d="M12 4.8v14.4"/><path d="M4.8 12h14.4"/>',
        "minus":    '<path d="M4.8 12h14.4"/>',
        // Crosshair rings: "put the thing back in the middle and keep it
        // there". The follow toggle over the lyric timeline's playhead.
        "target":   '<circle cx="12" cy="12" r="7.2"/><circle cx="12" cy="12" r="1.8"/>',
        "x":        '<path d="M6.2 6.2l11.6 11.6"/><path d="M17.8 6.2L6.2 17.8"/>',
        "trash":    '<path d="M3.8 6.8h16.4"/><path d="M9.4 6.8V4.4h5.2v2.4"/>'
                  + '<path d="M6.4 6.8l.9 13.1h9.4l.9-13.1"/>',
        "more":     '<circle cx="12" cy="5" r="1.7"/><circle cx="12" cy="12" r="1.7"/>'
                  + '<circle cx="12" cy="19" r="1.7"/>',
        // The grab handle a row is dragged by. Deliberately "more" doubled and
        // turned on its side, same dot radius and same 7-unit pitch — the two
        // sit at either end of a track row and should read as one family.
        "grip":     '<circle cx="8.5" cy="5" r="1.7"/><circle cx="15.5" cy="5" r="1.7"/>'
                  + '<circle cx="8.5" cy="12" r="1.7"/><circle cx="15.5" cy="12" r="1.7"/>'
                  + '<circle cx="8.5" cy="19" r="1.7"/><circle cx="15.5" cy="19" r="1.7"/>',
        "dot":      '<circle cx="12" cy="12" r="4"/>',
        "external": '<path d="M14 3.6h6.4V10"/><path d="M20.4 3.6L11 13"/>'
                  + '<path d="M18 13.6v5.2a1.6 1.6 0 01-1.6 1.6H5.2a1.6 1.6 0 01-1.6-1.6V7.6A1.6 1.6 0 015.2 6h5.2"/>',
        "folder":   '<path d="M3.4 7.2A1.7 1.7 0 015.1 5.5h3.8l2.1 2.6h7.9a1.7 1.7 0 011.7 1.7v7.6a1.7 1.7 0 01-1.7 1.7H5.1a1.7 1.7 0 01-1.7-1.7z"/>',
        "file":     '<path d="M13.6 3.2H7a2 2 0 00-2 2v13.6a2 2 0 002 2h10a2 2 0 002-2V8.6z"/>'
                  + '<path d="M13.6 3.2v5.4H19"/>',
        // A file with an arrow across it: moving a local file into the library,
        // as opposed to "download", which fetches one from the web.
        "stash":    '<path d="M13.6 3.2H7a2 2 0 00-2 2v13.6a2 2 0 002 2h10a2 2 0 002-2V8.6z"/>'
                  + '<path d="M13.6 3.2v5.4H19"/>'
                  + '<path d="M7.8 15.4h7.4"/><path d="M12.6 12.8l2.6 2.6-2.6 2.6"/>',
        "note":     '<path d="M9.2 18.2V5.4l10-2v12.8"/>'
                  + '<ellipse cx="6.4" cy="18.2" rx="2.8" ry="2.4"/>'
                  + '<ellipse cx="16.4" cy="16.2" rx="2.8" ry="2.4"/>',
        "info":     '<circle cx="12" cy="12" r="8.8"/><path d="M12 11.2v5.2"/>'
                  + '<path d="M12 7.6h.01"/>',
        // The same bar-and-dot as `info`, inverted, inside a triangle — the
        // pairing readers already know, so it needs no colour to be understood.
        "warning":  '<path d="M12 3.6L21.6 20.4H2.4z"/><path d="M12 9.6v4.6"/>'
                  + '<path d="M12 17.4h.01"/>',
        "sparkle":  '<path d="M12 3l1.85 5.15L19 10l-5.15 1.85L12 17l-1.85-5.15L5 10l5.15-1.85z"/>',
        "panel":    '<rect x="3.4" y="4.4" width="17.2" height="15.2" rx="2.2"/>'
                  + '<path d="M14.6 4.4v15.2"/>',
        // The opposite action to `panel`: arrows opening outward to the edges
        // of the frame it draws, which is one half taking the whole width.
        "expand":   '<path d="M10.2 12H4.2"/><path d="M7.2 9L4.2 12l3 3"/>'
                  + '<path d="M13.8 12h6"/><path d="M16.8 9l3 3-3 3"/>',
        "sort":     '<path d="M4 6.6h16"/><path d="M7 12h10"/><path d="M10 17.4h4"/>',

        // The two ends of the tile-size slider, drawn as the walls they ask
        // for. `tilesLarge` is `library`'s 2x2 repeated deliberately rather
        // than aliased: the rail's meaning of that shape is "the Library page",
        // and a control that means "bigger tiles" should not go looking blank
        // the day the rail icon is redrawn. `tilesSmall` fills the same 3.5-20.5
        // span with three columns instead of two, so the pair differ only in
        // how many cells the same wall is cut into — which is exactly what the
        // slider between them does.
        "tilesLarge": '<rect x="3.5" y="3.5" width="7" height="7" rx="1.6"/>'
                    + '<rect x="13.5" y="3.5" width="7" height="7" rx="1.6"/>'
                    + '<rect x="3.5" y="13.5" width="7" height="7" rx="1.6"/>'
                    + '<rect x="13.5" y="13.5" width="7" height="7" rx="1.6"/>',
        "tilesSmall": '<rect x="3.5" y="3.5" width="4.4" height="4.4" rx="1.1"/>'
                    + '<rect x="9.8" y="3.5" width="4.4" height="4.4" rx="1.1"/>'
                    + '<rect x="16.1" y="3.5" width="4.4" height="4.4" rx="1.1"/>'
                    + '<rect x="3.5" y="9.8" width="4.4" height="4.4" rx="1.1"/>'
                    + '<rect x="9.8" y="9.8" width="4.4" height="4.4" rx="1.1"/>'
                    + '<rect x="16.1" y="9.8" width="4.4" height="4.4" rx="1.1"/>'
                    + '<rect x="3.5" y="16.1" width="4.4" height="4.4" rx="1.1"/>'
                    + '<rect x="9.8" y="16.1" width="4.4" height="4.4" rx="1.1"/>'
                    + '<rect x="16.1" y="16.1" width="4.4" height="4.4" rx="1.1"/>',

        // The lyrics editor's two shapes, drawn as what they are rather than
        // as an abstraction of it. `rows`: equal rows, each split into a narrow
        // timecode cell and a wide one for the words. `timeline`: an axis with
        // blocks of unequal height hung off it, which is the whole difference
        // between the two — in one, height is a row; in the other, height is
        // time.
        "rows":     '<rect x="3.4" y="4.8" width="17.2" height="5.6" rx="1.4"/>'
                  + '<rect x="3.4" y="13.6" width="17.2" height="5.6" rx="1.4"/>'
                  + '<path d="M9 4.8v5.6"/><path d="M9 13.6v5.6"/>',
        "timeline": '<path d="M3.6 3.4v17.2"/>'
                  + '<rect x="7.8" y="3.6" width="12.6" height="4.4" rx="1.4"/>'
                  + '<rect x="7.8" y="10" width="12.6" height="10.6" rx="1.4"/>',
        "copy":     '<rect x="8.6" y="8.6" width="11.8" height="11.8" rx="2"/>'
                  + '<path d="M15.4 5.6A2 2 0 0013.4 3.6H5.6a2 2 0 00-2 2v7.8a2 2 0 002 2"/>',
        // Pencil across the diagonal, with the nib cut off square so it still
        // reads as a tool at 16px rather than as a plain stroke.
        "edit":     '<path d="M16.1 3.9a2.3 2.3 0 013.2 0l.8.8a2.3 2.3 0 010 3.2L8.5 19.5l-4.6 1.1 1.1-4.6z"/>'
                  + '<path d="M14.4 5.6l4 4"/>',

        // Head and shoulders. The shoulders are an arc rather than a closed
        // shape so the icon keeps the same open, stroked weight as its
        // neighbours in the rail instead of reading as a filled silhouette.
        "person":   '<circle cx="12" cy="8" r="3.6"/>'
                  + '<path d="M4.8 20.4a7.2 7.2 0 0114.4 0"/>',

        "chevronLeft":  '<path d="M14.8 5.4L8.2 12l6.6 6.6"/>',
        "chevronRight": '<path d="M9.2 5.4L15.8 12l-6.6 6.6"/>',
        "chevronDown":  '<path d="M5.4 9.2L12 15.8l6.6-6.6"/>',

        // ------------------------------------------------ window controls
        "winMin":     '<path d="M5 12h14"/>',
        "winMax":     '<rect x="5.2" y="5.2" width="13.6" height="13.6" rx="2"/>',
        "winRestore": '<rect x="4" y="8" width="12" height="12" rx="2"/>'
                    + '<path d="M8 4.2h9.8A2 2 0 0119.8 6.2V16"/>',
        "winClose":   '<path d="M6.2 6.2l11.6 11.6"/><path d="M17.8 6.2L6.2 17.8"/>'
    })

    // Colours arrive as QML colors; #aarrggbb is not valid SVG, so strip any
    // alpha channel here and let the caller apply opacity on the item instead.
    function _hex(c) {
        var s = String(c)
        if (s.length === 9 && s.charAt(0) === "#")
            return "#" + s.substring(3)
        return s
    }

    function svg(name, color, strokeWidth) {
        var body = shapes[name]
        if (body === undefined)
            return ""
        var solid = filled.indexOf(name) >= 0
        var hex = _hex(color)
        var head = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
                 + 'fill="' + (solid ? hex : "none") + '" '
                 + 'stroke="' + (solid ? "none" : hex) + '" '
                 + 'stroke-width="' + (strokeWidth > 0 ? strokeWidth : 1.7) + '" '
                 + 'stroke-linecap="round" stroke-linejoin="round">'
        return "data:image/svg+xml;utf8," + encodeURIComponent(head + body + "</svg>")
    }

    function has(name) { return shapes[name] !== undefined }
}
