import QtQuick

/*
 * Nocturne's column layout profiles.
 *
 * Instantiated once in main.qml and handed down to the views, so that a layout
 * survives switching tabs and the queue and history keep sharing one.
 *
 * Not a singleton, but no longer because it could not be one: `Kroma.Settings`
 * would reach the store from a singleton perfectly well. It stays an object
 * because the store arrives as a property, which is what lets a test or a fork
 * drive a layout from something else.
 *
 * The `settingsKey`s are written with `setValue`, so they land in settings.json
 * under `gui.nocturne`, scoped there by SettingsViewModel. Durable rather than
 * session state: a column you widened is how you like the list, not where you
 * were. The backend stores the JSON and never reads it -- which columns exist
 * is the GUI's business.
 */
QtObject {
    id: layouts

    property var settings: null

    // The queue and history are the same rows with the same geometry; only
    // whether the row number shows differs, and that is not part of a layout.
    readonly property TrackColumnLayout queue: TrackColumnLayout {
        profile: "queue"
        settingsKey: "columns.queue"
        settings: layouts.settings
    }

    readonly property TrackColumnLayout library: TrackColumnLayout {
        profile: "library"
        settingsKey: "columns.library"
        settings: layouts.settings
    }

    // The album page shows the library's columns but cannot share its widths.
    // Its rows drop the artwork, which makes its column budget ~50px wider than
    // the library's — and a budget is pushed into the layout object, not held by
    // the table. Sharing one would leave whichever page rendered last deciding
    // the other's fit, so the album keeps the same `profile` and its own key.
    readonly property TrackColumnLayout album: TrackColumnLayout {
        profile: "library"
        settingsKey: "columns.album"
        settings: layouts.settings
    }

    readonly property TrackColumnLayout playlist: TrackColumnLayout {
        profile: "playlist"
        settingsKey: "columns.playlist"
        settings: layouts.settings
    }

    readonly property TrackColumnLayout search: TrackColumnLayout {
        profile: "search"
        settingsKey: "columns.search"
        settings: layouts.settings
    }

    // Online results carry title, artist, album, duration and an id, and
    // nothing else -- so this profile offers far less to switch on, plus the
    // status cell that gets a result into the library.
    readonly property TrackColumnLayout webSearch: TrackColumnLayout {
        profile: "webSearch"
        settingsKey: "columns.webSearch"
        settings: layouts.settings
    }

    // Every import kind shares this one — they are all one list. Same rows,
    // same shape, and a status cell that means "get this into the library"
    // whether that turns out to be a fetch or a file.
    readonly property TrackColumnLayout importResults: TrackColumnLayout {
        profile: "import"
        settingsKey: "columns.import"
        settings: layouts.settings
    }
}
