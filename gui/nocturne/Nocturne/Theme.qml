pragma Singleton

import QtQuick
import Kroma.Settings 1.0

// Design tokens for Nocturne. One place for every colour, radius, and duration
// so the whole interface stays coherent.
//
// Every colour comes in two values, one per mode, and the interface reads only
// the resolved token — nothing outside this file knows which mode is on. That
// is the rule that keeps light mode working: a hex literal anywhere else is a
// colour that cannot follow the switch, so there are none.
//
// The accent, the mode and the sort orders are user-selectable and persist
// through the backend's settings store — `Kroma.Settings`, the same object as
// the `settingsVM` context property, registered as a QML singleton so that a
// `pragma Singleton` like this one can reach it.
QtObject {
    id: root

    // Guards the writeback below. Every `onXChanged` fires while `prefs` is
    // being constructed, so without this a first launch would write the whole
    // set of defaults straight back and materialise a file for settings nobody
    // has touched. `SettingsStore`'s compare-before-store absorbs the case
    // where the value was already on disk, but not the fresh-install one.
    property bool _ready: false
    Component.onCompleted: _ready = true

    // Deliberately a plain QtObject with real properties rather than reads of
    // `SettingsStore.value()` at each call site: `value()` is a method, not a
    // notifying property, so binding to it directly would leave the interface
    // unable to repaint when the accent changes. These are seeded once and
    // written back on change, and the ~40 places that read `Theme.prefs.x`
    // neither know nor care where the value came from.
    property QtObject prefs: QtObject {
        // ---------------------------------------------------------- durable
        // How you like it. Kept in settings.json, so it survives a lost
        // session and travels in a backup.

        property string accent: SettingsStore.value("accent", "aurora")
        onAccentChanged: if (root._ready) SettingsStore.setValue("accent", accent)

        property bool followArtwork: SettingsStore.value("followArtwork", false)
        onFollowArtworkChanged: if (root._ready) SettingsStore.setValue("followArtwork", followArtwork)

        // "dark" | "light" | "system". Defaults to following the desktop:
        // an interface that opens bright on a dark desktop at midnight is the
        // one complaint a music player cannot answer for.
        property string themeMode: SettingsStore.value("themeMode", "system")
        onThemeModeChanged: if (root._ready) SettingsStore.setValue("themeMode", themeMode)

        property bool ambient: SettingsStore.value("ambient", true)
        onAmbientChanged: if (root._ready) SettingsStore.setValue("ambient", ambient)

        property bool reducedMotion: SettingsStore.value("reducedMotion", false)
        onReducedMotionChanged: if (root._ready) SettingsStore.setValue("reducedMotion", reducedMotion)

        // Library track list ordering. `librarySort` is a catalog field key —
        // see libraryVM.fields for which ones are sortable — or "" for no
        // chosen sort, which the column headers show as no arrow at all and
        // LibraryView resolves to newest first: what you just imported is what
        // you are most likely looking for.
        property string librarySort: SettingsStore.value("librarySort", "")
        onLibrarySortChanged: if (root._ready) SettingsStore.setValue("librarySort", librarySort)

        property bool librarySortAsc: SettingsStore.value("librarySortAsc", false)
        onLibrarySortAscChanged: if (root._ready) SettingsStore.setValue("librarySortAsc", librarySortAsc)

        // The artist wall's ordering, kept apart from the track list's because
        // the two faces sort by different things and neither answer is the
        // other's. "trackCount" | "name" | "dateAdded" — see artistsVM.sortField.
        // Biggest catalogue first by default: the artists a library is actually
        // about, rather than whoever happens to come first alphabetically.
        property string artistSort: SettingsStore.value("artistSort", "trackCount")
        onArtistSortChanged: if (root._ready) SettingsStore.setValue("artistSort", artistSort)

        property bool artistSortAsc: SettingsStore.value("artistSortAsc", false)
        onArtistSortAscChanged: if (root._ready) SettingsStore.setValue("artistSortAsc", artistSortAsc)

        // The album wall's ordering, kept apart from both of the above for the
        // same reason they are kept apart from each other. Same three keys —
        // see albumsVM.sortField — and the same default, because the record you
        // have most of is the one you are most likely looking for.
        property string albumSort: SettingsStore.value("albumSort", "trackCount")
        onAlbumSortChanged: if (root._ready) SettingsStore.setValue("albumSort", albumSort)

        property bool albumSortAsc: SettingsStore.value("albumSortAsc", false)
        onAlbumSortAscChanged: if (root._ready) SettingsStore.setValue("albumSortAsc", albumSortAsc)

        // How wide a tile on the artist wall wants to be. A target rather than
        // a measurement: the wall fills its row, so the size actually drawn is
        // this rounded to whatever column count fits. Stored as a width rather
        // than as the column count the slider actually steps through, because a
        // column count only means something next to the window it was chosen
        // in — a width survives a resize and travels to another machine.
        // Defaults to the 170 the wall was hard-coded to, so an existing
        // install opens unchanged. Kept apart from the album wall's for the
        // same reason `artistSort` and `albumSort` are: two walls of different
        // things, and neither one's answer is the other's.
        property int artistTileSize: SettingsStore.value("artistTileSize", 170)
        onArtistTileSizeChanged: if (root._ready) SettingsStore.setValue("artistTileSize", artistTileSize)

        // The album wall's, defaulting to the 200 it was hard-coded to — a
        // sleeve is worth seeing larger than a portrait, and that gap is
        // preserved rather than flattened onto one shared size.
        property int albumTileSize: SettingsStore.value("albumTileSize", 200)
        onAlbumTileSizeChanged: if (root._ready) SettingsStore.setValue("albumTileSize", albumTileSize)

        // Display preferences belong to each collection, independently of the
        // sidebar destination and sort order. Preserve existing opening layouts.
        property bool songGrid: SettingsStore.value("songGrid", false)
        onSongGridChanged: if (root._ready) SettingsStore.setValue("songGrid", songGrid)
        property bool artistGrid: SettingsStore.value("artistGrid", true)
        onArtistGridChanged: if (root._ready) SettingsStore.setValue("artistGrid", artistGrid)
        property bool albumGrid: SettingsStore.value("albumGrid", true)
        onAlbumGridChanged: if (root._ready) SettingsStore.setValue("albumGrid", albumGrid)
        property bool playlistGrid: SettingsStore.value("playlistGrid", true)
        onPlaylistGridChanged: if (root._ready) SettingsStore.setValue("playlistGrid", playlistGrid)

        property bool songCompact: SettingsStore.value("songCompact", false)
        onSongCompactChanged: if (root._ready) SettingsStore.setValue("songCompact", songCompact)
        property bool artistCompact: SettingsStore.value("artistCompact", false)
        onArtistCompactChanged: if (root._ready) SettingsStore.setValue("artistCompact", artistCompact)
        property bool albumCompact: SettingsStore.value("albumCompact", false)
        onAlbumCompactChanged: if (root._ready) SettingsStore.setValue("albumCompact", albumCompact)
        property bool playlistCompact: SettingsStore.value("playlistCompact", false)
        onPlaylistCompactChanged: if (root._ready) SettingsStore.setValue("playlistCompact", playlistCompact)

        property int songTileSize: SettingsStore.value("songTileSize", 200)
        onSongTileSizeChanged: if (root._ready) SettingsStore.setValue("songTileSize", songTileSize)
        property int playlistTileSize: SettingsStore.value("playlistTileSize", 200)
        onPlaylistTileSizeChanged: if (root._ready) SettingsStore.setValue("playlistTileSize", playlistTileSize)

        // Whether the album page repeats the sleeve down its rows. Off by
        // default: the hero is already showing the cover at 152px, and an album
        // is the one list where every row would carry the same one. The row that
        // would have held it holds a play button instead — see TrackTable's
        // `numberSlot`. Durable rather than session, because it is how you like
        // the page rather than where you happened to be.
        property bool albumArtwork: SettingsStore.value("albumArtwork", false)
        onAlbumArtworkChanged: if (root._ready) SettingsStore.setValue("albumArtwork", albumArtwork)

        // ------------------------------------------------------- disposable
        // Where you were. Kept in session.json, so deleting it resets the app
        // to a fresh-looking launch without touching a single preference.

        property int navIndex: SettingsStore.sessionValue("navIndex", 0)
        onNavIndexChanged: if (root._ready) SettingsStore.setSessionValue("navIndex", navIndex)

        // Which of the Settings page's tabs was last open. Restored for the
        // same reason `navIndex` is: the app reopens where you left it, and a
        // sub-page is no different.
        property int settingsTab: SettingsStore.sessionValue("settingsTab", 0)
        onSettingsTabChanged: if (root._ready) SettingsStore.setSessionValue("settingsTab", settingsTab)

        // Which face the Library page is showing: 0 = tracks, 1 = artists,
        // 2 = albums. Albums were added last and took the next index, so a
        // stored 1 still means artists and no preference had to be migrated. An
        // index rather than a name because it is a Pill index at both ends, and
        // a string would need a lookup table in each direction to say the same
        // thing. Same reasoning as `navIndex` and `settingsTab`.
        property int libraryMode: SettingsStore.sessionValue("libraryMode", 0)
        onLibraryModeChanged: if (root._ready) SettingsStore.setSessionValue("libraryMode", libraryMode)

        // Which half of Now Playing has the page: "" for the ordinary split,
        // "art" or "words". Session rather than durable for the reason
        // navIndex is — it is where you were looking, not a standing
        // preference about the page.
        property string nowPlayingFocus: SettingsStore.sessionValue("nowPlayingFocus", "")
        onNowPlayingFocusChanged: if (root._ready) SettingsStore.setSessionValue("nowPlayingFocus", nowPlayingFocus)
    }

    // ------------------------------------------------------------------ mode

    readonly property var modeKeys: ["system", "light", "dark"]
    readonly property var modeLabels: ["System", "Light", "Dark"]

    // Qt reports the desktop's scheme here and re-reports it when the desktop
    // changes, so "system" follows along live rather than only at startup.
    readonly property bool _systemLight:
        Application.styleHints.colorScheme === Qt.ColorScheme.Light

    readonly property bool isLight:
        prefs.themeMode === "light"
        || (prefs.themeMode !== "dark" && _systemLight)

    // The mode actually in force, with "system" already resolved.
    readonly property string mode: isLight ? "light" : "dark"

    function setMode(key) {
        if (modeKeys.indexOf(key) >= 0)
            prefs.themeMode = key
    }

    // Tell Qt which scheme it is drawing for. Nocturne styles its own controls,
    // but the handful that fall through to QtQuick Controls — scroll bars above
    // all — take their colours from here, and would otherwise stay dark on a
    // light interface whose owner had overridden the desktop.
    //
    // `Unknown` is Qt's own word for "no override": assigning it hands the
    // property back to the desktop, which is exactly what "system" means and is
    // why `_systemLight` above can keep reading the same property without ever
    // seeing a value of ours.
    property Binding schemeOverride: Binding {
        target: Application.styleHints
        property: "colorScheme"
        value: root.prefs.themeMode === "light" ? Qt.ColorScheme.Light
             : root.prefs.themeMode === "dark"  ? Qt.ColorScheme.Dark
             : Qt.ColorScheme.Unknown
        restoreMode: Binding.RestoreNone
    }

    // ---------------------------------------------------------------- accents

    // `a`/`b` are the pair a dark interface runs on; `la`/`lb` are the same two
    // hues taken deeper, because the bright originals are chosen to glow
    // against near-black and turn illegible the moment the page behind them is
    // white. Both pairs are used the same way — fill, hairline, and label — so
    // each has to carry text contrast on its own background, not just look like
    // the right colour.
    readonly property var accentTable: ({
        "aurora":  { label: "Aurora",  a: "#7C6CFF", b: "#22D3EE", la: "#5A45E0", lb: "#0E8FA8" },
        "ember":   { label: "Ember",   a: "#FF7A45", b: "#FFC061", la: "#D2451A", lb: "#A8680C" },
        "bloom":   { label: "Bloom",   a: "#FF5C8A", b: "#C86DD7", la: "#D62C63", lb: "#8E3FB0" },
        "verdant": { label: "Verdant", a: "#34D399", b: "#A3E635", la: "#0F8A5F", lb: "#4F7A12" },
        "ice":     { label: "Ice",     a: "#60A5FA", b: "#A78BFA", la: "#2563EB", lb: "#6D45D9" },
        "gold":    { label: "Gold",    a: "#E0B252", b: "#F5DCA4", la: "#96690F", lb: "#8A6B2E" }
    })

    readonly property var accentKeys: ["aurora", "ember", "bloom", "verdant", "ice", "gold"]
    // The window bridges the backend into this singleton. Keep the user's
    // preset intact so switching off artwork restores it immediately.
    property var artworkPalette: ({})
    readonly property var _acc: prefs.followArtwork && artworkPalette.a
        ? artworkPalette : (accentTable[prefs.accent] || accentTable["aurora"])

    readonly property color accent:    isLight ? _acc.la : _acc.a
    readonly property color accentAlt: isLight ? _acc.lb : _acc.b

    // For anything that has to paint an accent it has not selected — the
    // swatches in Settings — so a preview shows the pair that would actually
    // be used rather than the dark one in every mode.
    function accentOf(key) {
        var preset = accentTable[key] || accentTable["aurora"]
        return isLight ? preset.la : preset.a
    }

    function accentAltOf(key) {
        var preset = accentTable[key] || accentTable["aurora"]
        return isLight ? preset.lb : preset.b
    }

    function setAccent(key) {
        if (accentTable[key] !== undefined)
            prefs.accent = key
    }

    // ------------------------------------------------------------- base tones

    readonly property var _darkTones: ({
        bg:       "#08090D",   // window
        surface:  "#0F1118",   // panels
        card:     "#161A24",   // cards, rows
        raised:   "#1E2331",   // hover
        sunken:   "#0B0D13",

        text:     "#EDEFF5",
        dim:      "#9BA3B7",
        faint:    "#646C81",

        danger:   "#F87171",
        positive: "#4ADE80",
        // Amber, from the same family as the two above. For a row that is not
        // wrong, only unverified — an import that matched nothing is still
        // importable, so it must not be dressed as an error.
        warning:  "#FBBF24",

        // Hairlines. Translucent so they read correctly over the ambient
        // backdrop, and drawn in the direction that separates: light on dark,
        // dark on light.
        line:     Qt.rgba(1, 1, 1, 0.07),
        lineSoft: Qt.rgba(1, 1, 1, 0.04),
        lineHard: Qt.rgba(1, 1, 1, 0.13),

        // Chrome and floating surfaces. These sit over the ambient backdrop
        // rather than in the flow of a page, so they are values of their own
        // rather than steps on the elevation scale above.
        barBg:     "#0A0C12",   // the transport
        panelBg:   "#0C0E15",   // the details panel
        menuBg:    "#11131A",
        dialogBg:  "#12141C",
        toastBg:   "#151821",
        tooltipBg: "#0A0C12",
        scrim:     Qt.rgba(0.016, 0.020, 0.039, 0.66),

        // Ink for a fill that is not one of the tones: the accent and the amber
        // badge. Both fills keep their colour across modes, so the ink on them
        // flips instead — dark on the bright dark-mode accents, white on the
        // deep light-mode ones.
        accentInk:  "#0A0B10",
        warningInk: "#1A1206",

        /* The bed a row is pulled off, which does not follow that rule.
         *
         * `danger` is bright here because it has to carry on a dark page as a
         * word or a hairline. Filling half a row with it is a different job: at
         * this size it stops reading as a warning and starts reading as a hole
         * punched in the page, far louder than the row it is about to remove.
         * So the fill goes the other way in dark mode — deep enough to sit in
         * the elevation scale with the cards around it — and the ink on it goes
         * light, which is the pairing every other dark surface here uses.
         *
         * Light mode keeps the deep `danger` red and white on it, because there
         * the same reasoning already lands on that answer.
         */
        dangerBed:    "#3F1218",
        dangerBedInk: "#FECACA",

        // How hard the artwork behind the window is pushed away from the tone
        // of the page: down toward black here, up toward white in light mode.
        ambientBrightness: -0.34,
        ambientSaturation:  0.55
    })

    readonly property var _lightTones: ({
        bg:       "#F1F2F7",
        surface:  "#FAFBFD",
        card:     "#FFFFFF",
        raised:   "#E9EBF2",
        sunken:   "#E7E9F0",

        text:     "#111318",
        dim:      "#4C5364",
        faint:    "#7A8294",

        danger:   "#D22C2C",
        positive: "#12874A",
        warning:  "#B4690E",

        line:     Qt.rgba(0, 0, 0, 0.09),
        lineSoft: Qt.rgba(0, 0, 0, 0.05),
        lineHard: Qt.rgba(0, 0, 0, 0.16),

        barBg:     "#F7F8FC",
        panelBg:   "#F7F8FC",
        menuBg:    "#FFFFFF",
        dialogBg:  "#FFFFFF",
        toastBg:   "#FFFFFF",
        tooltipBg: "#FFFFFF",
        // Lighter than the dark scrim by as much as it is paler: a modal still
        // has to sink the page behind it, but 66% of near-black over a white
        // interface reads as the lights going out.
        scrim:     Qt.rgba(0.102, 0.114, 0.145, 0.42),

        accentInk:  "#FFFFFF",
        warningInk: "#FFFFFF",

        // Already deep, already carrying white — see the dark-mode note.
        dangerBed:    "#C62222",
        dangerBedInk: "#FFFFFF",

        ambientBrightness: 0.30,
        ambientSaturation: 0.42
    })

    readonly property var tones: isLight ? _lightTones : _darkTones

    readonly property color bg:       tones.bg
    readonly property color surface:  tones.surface
    readonly property color card:     tones.card
    readonly property color raised:   tones.raised
    readonly property color sunken:   tones.sunken

    readonly property color text:     tones.text
    readonly property color dim:      tones.dim
    readonly property color faint:    tones.faint

    readonly property color danger:   tones.danger
    readonly property color positive: tones.positive
    readonly property color warning:  tones.warning

    readonly property color line:     tones.line
    readonly property color lineSoft: tones.lineSoft
    readonly property color lineHard: tones.lineHard

    readonly property color barBg:     tones.barBg
    readonly property color panelBg:   tones.panelBg
    readonly property color menuBg:    tones.menuBg
    readonly property color dialogBg:  tones.dialogBg
    readonly property color toastBg:   tones.toastBg
    readonly property color tooltipBg: tones.tooltipBg
    readonly property color scrim:     tones.scrim

    readonly property color accentInk:  tones.accentInk
    readonly property color warningInk: tones.warningInk
    readonly property color dangerBed:    tones.dangerBed
    readonly property color dangerBedInk: tones.dangerBedInk

    readonly property real ambientBrightness: tones.ambientBrightness
    readonly property real ambientSaturation: tones.ambientSaturation

    // Album art is arbitrary imagery, so what is drawn on top of it cannot
    // follow the interface's tones: a wash over a cover has to darken it and
    // the glyph on that wash has to stay white in either mode.
    readonly property color imageScrim: Qt.rgba(0, 0, 0, 0.55)
    readonly property color imageInk:    "#FFFFFF"

    // The close button's hover state, which belongs to the shell's conventions
    // rather than to this palette: the same red under either mode, like every
    // other window on the desktop.
    readonly property color closeHover:   "#C0392B"
    readonly property color closeHoverInk: "#FFFFFF"

    // Typed parameters, so a colour written as a string literal at the call
    // site is converted rather than read for `.r` it does not have.
    function alpha(c: color, a: real): color { return Qt.rgba(c.r, c.g, c.b, a) }

    readonly property color accentGlow: alpha(accent, 0.16)
    readonly property color accentSoft: alpha(accent, 0.10)

    // -------------------------------------------------------------- geometry

    readonly property int radiusSm: 6
    readonly property int radius:   10
    readonly property int radiusLg: 16
    readonly property int radiusXl: 22

    readonly property int gapXs: 4
    readonly property int gapSm: 8
    readonly property int gap:   14
    readonly property int gapLg: 22
    readonly property int gapXl: 34

    readonly property int rowHeight:     58
    readonly property int navWidth:      74
    readonly property int playerHeight:  86
    readonly property int titleBarHeight: 64

    // ------------------------------------------------------------ typography

    readonly property string fontFamily: "Segoe UI Variable Text"
    readonly property string displayFamily: "Segoe UI Variable Display"

    readonly property int fzMicro: 10
    readonly property int fzTiny:  11
    readonly property int fzSmall: 12
    readonly property int fzBody:  13
    readonly property int fzMed:   15
    readonly property int fzLarge: 19
    readonly property int fzTitle: 27
    readonly property int fzHero:  40

    // --------------------------------------------------------------- motion

    readonly property int fast:   120
    readonly property int normal: 190
    readonly property int slow:   340
    readonly property int easing: Easing.OutCubic
}
