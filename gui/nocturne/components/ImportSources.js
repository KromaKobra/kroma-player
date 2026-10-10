.pragma library

/*
 * The ways music gets into the library, in the order they are offered.
 *
 * This is presentation, so it lives in the GUI package: the labels and glyphs
 * are decisions about how *this* interface reads, and another GUI would name
 * the same three sources differently or offer them somewhere else entirely.
 * What the backend supplies is the behaviour behind `kind`.
 *
 * The first entry is the button. The rest are its dropdown — see
 * ImportButton.qml, which slices this list in exactly that place. Order here is
 * menu order.
 *
 * Two kinds of source are offered: the ones built in here, and the ones the
 * enabled plugins contribute, which arrive from `pluginsVM.importSources` and
 * are folded in by `sourcesFor` below. A plugin's source is `kind`
 * "plugin:<key>", and `ImportView.chooseSource()` raises its dialog from the
 * entry itself — a line of text or a file — so a plugin adds a source without
 * this file or that one changing.
 *
 * Adding a *built-in* source is still two lines in two files: an entry here,
 * and a branch in `ImportView.chooseSource()`, which is where a kind turns into
 * an actual dialog. The second half is irreducible; only the Import page knows
 * what raising it looks like, because that is where the dialogs live.
 *
 *   kind     handed to `chooseSource(kind)`; the identity of the source, and
 *            the only part of an entry the backend ever sees
 *   label    what the button or the menu row says
 *   icon     a name from the Icons singleton
 *   iconUrl  a plugin's own picture, drawn instead of `icon` (plugin entries)
 *
 * A `.pragma library` file is shared, stateless, and cannot see QML singletons
 * — which is why `icon` is a name to be looked up rather than anything drawn,
 * and why there are no colours here. TrackColumns.js is the same shape for the
 * same reason.
 */
var SOURCES = [
    // Files first: it is both the common case and the one that needs no
    // explaining, which is what earns it the half of the button you can hit
    // without reading anything.
    { kind: "files",  label: "Import files",  icon: "note" },
    { kind: "folder", label: "Import folder", icon: "folder" }
];

// A spreadsheet of tracks to fetch. Only offered when something can fetch
// them: every row of one is a download, and with no audio source enabled every
// row would arrive unavailable.
var CSV = { kind: "csv", label: "Import CSV", icon: "download" };

/*
 * The whole menu, for what is enabled right now.
 *
 *   hasAudioSource  `pluginsVM.hasAudioSource`
 *   pluginSources   `pluginsVM.importSources` — `[{key, label, iconName,
 *                   iconUrl, ...}]`
 *
 * Either may be undefined, on a backend that predates plugins; that reads as
 * a plain install, and the menu is files and a folder.
 */
function sourcesFor(hasAudioSource, pluginSources) {
    var out = SOURCES.slice();
    var plugins = pluginSources || [];
    for (var i = 0; i < plugins.length; ++i) {
        out.push({
            kind: "plugin:" + plugins[i].key,
            label: plugins[i].label,
            icon: plugins[i].iconName || "external",
            iconUrl: plugins[i].iconUrl || ""
        });
    }
    if (hasAudioSource === true)
        out.push(CSV);
    return out;
}
