.pragma library

/*
 * Column catalog for nocturne's track lists.
 *
 * This is presentation, so it lives in the GUI package. Labels, pixel widths,
 * alignment and renderer names are decisions about how *this* interface looks;
 * another GUI would make different ones, or have no columns at all.
 *
 * What the backend supplies is the field catalog (`libraryVM.fields`). `field`
 * below is a key from it, and it is the role a delegate reads. Because
 * TrackListModel is demand-driven — it records the roles something actually
 * read and fetches them on the next turn — adding a column here is the whole
 * change. No Python moves.
 *
 * A `.pragma library` file is shared, stateless, and cannot see QML singletons.
 * That is why there are no colours here and no `Fmt` calls: `renderer` names a
 * format that TrackTable's delegate applies, and Theme does the colouring.
 */

// Geometry shared with TrackTable.qml and TrackColumnHeader.qml. The row and
// the header both derive their layout from these, so they cannot drift apart.
var ART_W = 42;    // album art
var NUM_W = 26;    // row number / playing meter
var MORE_W = 28;   // trailing overflow button
// The status cell draws a 30px button, but the column's floor comes from its
// own header instead: at 34 the label elided to "STA…", which reads as
// breakage rather than as a narrow column.
var STATUS_W = 58;

// What `max: 0` means. Also the ceiling `clampWidth` applies when a column
// declares none, so "unbounded" is one number rather than two.
var UNBOUNDED = 100000;

// The title/artist column is elastic: it has no stored width and absorbs
// whatever the data columns leave. This floor is what everything else is
// squeezed against — and it is deliberately far above NUM_W, so the playing
// row can give up NUM_W + a gap for its meter and still have room to read.
var TITLE_MIN = 150;

// A dropped column only comes back with this much headroom to spare, so a
// window drag that lands on the threshold cannot flap it on and off.
var RESTORE_MARGIN = 16;


/* Every column that is not pinned. Declaration order is the right-click menu
 * order.
 *
 *   field      role the cell reads (absent for cells that read no single field)
 *   pref/min   width bounds; `max` 0 means unbounded
 *   renderer   "text" | "duration" | "date" | "status"
 *   numeric    render with tabular figures, so digits stay in column
 *   resizable  false = fixed width, and no drag handle on its left edge
 *   absorbs    false = never gives up width to a *neighbour's* resize
 *   droppable  false = never auto-dropped when the window runs out of room
 */
var COLUMNS = {
    album: {
        key: "album", field: "album", label: "ALBUM",
        pref: 220, min: 90, max: 0,
        renderer: "text", align: "left"
    },
    duration: {
        key: "duration", field: "duration", label: "TIME",
        pref: 64, min: 52, max: 0,
        renderer: "duration", align: "right", numeric: true
    },
    bpm: {
        key: "bpm", field: "bpm", label: "BPM",
        pref: 62, min: 48, max: 0,
        renderer: "text", align: "right", numeric: true
    },
    key: {
        key: "key", field: "key", label: "KEY",
        pref: 62, min: 44, max: 0,
        renderer: "text", align: "left"
    },
    date: {
        key: "date", field: "date", label: "RELEASED",
        pref: 110, min: 76, max: 0,
        renderer: "date", align: "left"
    },
    stashedDate: {
        key: "stashedDate", field: "stashedDate", label: "ADDED",
        pref: 110, min: 76, max: 0,
        renderer: "date", align: "left"
    },
    genres: {
        key: "genres", field: "genres", label: "GENRES",
        pref: 150, min: 80, max: 0,
        renderer: "text", align: "left"
    },
    isrc: {
        key: "isrc", field: "isrc", label: "ISRC",
        pref: 130, min: 100, max: 0,
        renderer: "text", align: "left"
    },
    // Where an import row's audio comes from, and what taking it in costs.
    //
    // Import only: every other list shows tracks that are already in the vault
    // as Opus, so the answer there would be the same word on every row.
    //
    // The renderer composes rather than prints. The backend supplies the bare
    // container format — "opus", "flac", "" — because the field catalog holds no
    // presentation; whether that reads as "Opus", "FLAC → Opus" or the name a
    // plugin gives its source is decided in TrackTable.cellText, which is also
    // the only thing that knows the row is a download.
    //
    // Wide enough for a short source name and for the longest "X → Opus" the
    // importer accepts, which is FLAC.
    source: {
        key: "source", field: "sourceFormat", label: "SOURCE",
        pref: 110, min: 76, max: 0,
        renderer: "source", align: "left"
    },
    // Whether the row is in the library, and the button to put it there.
    //
    // Narrow, because it draws one centred icon, but not fixed: a column with
    // no width of its own has no divider on its left edge either, and in the
    // import and web-result lists — the only two that show it — that left the
    // gap between TIME and STATUS looking like a resize bar that did nothing.
    //
    // `absorbs: false` is what keeps it out of everyone else's way. It sits a
    // few pixels above its own floor, so standing in as the elastic partner
    // for the column to its right would clamp that column's handle almost
    // immediately; the resize chain skips it and reaches the real space
    // further left. Never auto-dropped, because a web result list that has
    // hidden its download button is useless.
    status: {
        key: "status", label: "STATUS",
        pref: STATUS_W, min: STATUS_W, max: 0,
        renderer: "status", align: "centre",
        absorbs: false, droppable: false
    }
};

/* Which columns each list offers, and what it shows before the user has said
 * otherwise.
 *
 * The reduced sets are not a style choice. Web results and import candidates
 * are not library rows: both arrive as flat dicts carrying title, artist,
 * album, duration, external ids and cover_url and nothing else, so `bpm` or
 * `genres` there would be a permanently blank cell with no error to explain it.
 */
var PROFILES = {
    queue: {
        available: ["album", "duration", "bpm", "key", "date", "stashedDate", "genres", "isrc"],
        defaults: ["album", "duration"]
    },
    library: {
        available: ["album", "duration", "bpm", "key", "date", "stashedDate", "genres", "isrc"],
        defaults: ["album", "stashedDate", "duration"]
    },
    playlist: {
        available: ["album", "duration", "bpm", "key", "date", "stashedDate", "genres", "isrc"],
        defaults: ["album", "duration"]
    },
    search: {
        available: ["album", "duration", "bpm", "key", "date", "stashedDate", "genres", "isrc"],
        defaults: ["album", "duration"]
    },
    webSearch: {
        available: ["album", "duration", "status"],
        defaults: ["album", "duration", "status"]
    },
    // Every import kind shares one layout: the same rows, the same geometry,
    // and a status cell that means "get this into the library" whether that
    // turns out to be a fetch or a file.
    //
    // SOURCE is the one column no other profile has, and the only place a row
    // says what importing it will cost. Which files are about to be transcoded
    // was decided at stash time and mentioned nowhere the user could see it. On
    // by default, and droppable like any other column for anyone who would
    // rather have the width.
    "import": {
        available: ["album", "duration", "source", "status"],
        defaults: ["album", "duration", "source", "status"]
    }
};


function entry(key) {
    var col = COLUMNS[key];
    // Copied, not returned by reference: callers hand these to QML as model
    // data and a shared mutable object would leak edits across every list.
    return col ? JSON.parse(JSON.stringify(col)) : null;
}

function available(profile) {
    var p = PROFILES[profile];
    return p ? p.available.slice() : [];
}

function defaultOrder(profile) {
    var p = PROFILES[profile];
    return p ? p.defaults.slice() : [];
}

function isAvailable(profile, key) {
    return available(profile).indexOf(key) !== -1;
}

function minWidth(col) { return col.min !== undefined ? col.min : 40; }

function maxWidth(col) { return col.max ? col.max : UNBOUNDED; }

function clampWidth(col, width) {
    return Math.round(Math.max(minWidth(col), Math.min(maxWidth(col), width)));
}

function isResizable(col) { return col.resizable !== false; }
function absorbs(col) { return col.absorbs !== false; }
function isDroppable(col) { return col.droppable !== false; }

/* Clean a restored order: drop unknown keys, duplicates, and anything this
 * profile does not offer.
 *
 * The last clause is the one that matters in practice. Layout blobs outlive the
 * catalog — a saved `library` layout can be pointed at `webSearch` by a future
 * edit, and a column removed from COLUMNS leaves its key in everyone's session
 * file. Both must degrade to "that column is off", never to a column with no
 * definition behind it.
 */
function sanitize(profile, order) {
    if (!Array.isArray(order))
        return defaultOrder(profile);
    var seen = {}, clean = [];
    for (var i = 0; i < order.length; ++i) {
        var k = String(order[i]);
        if (seen[k] || !COLUMNS[k] || !isAvailable(profile, k))
            continue;
        seen[k] = true;
        clean.push(k);
    }
    return clean;
}
