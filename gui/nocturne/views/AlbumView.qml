import QtQuick
import QtQuick.Controls
import Nocturne
import "../components"

// One album, in its own running order.
//
// Not a destination in the rail: you arrive from the Albums grid, from the album
// cell on a track row, or from the details panel. So the page opens with the
// album already loaded and has no empty state worth designing beyond "nothing is
// open".
//
// Unlike ArtistView, the track list is NOT a declarative TrackQuery. An artist's
// tracks are a set with no order of their own, so any sort in the catalog will
// do; an album's running order is a fact about the album, stored in
// `track_albums.position`, and a TrackQuery cannot express it — sorting goes
// through the field catalog and a running order is not a field. Hence
// `albumTracksVM`, which is also what makes the rows draggable: TrackTable looks
// up `moveRows` and `removeAtRows` on its list model by name before it will arm
// the gesture.
Item {
    id: root

    property bool active: false
    property var columnLayouts: null

    signal done()
    signal openArtistRequested(int artistId)
    signal editAlbumRequested(int albumId)
    signal editTrackRequested(int trackId)
    signal generateBackingRequested(var trackIds)
    signal importPageRequested()

    readonly property int albumId: albumVM.albumId

    /* Whether the running order is currently editable.
     *
     * A drag handle on every row, all the time, is the wrong default for this
     * page: an album's order is a fact about the record, set once and then left
     * alone, and the number column being permanently grabbable means a stray
     * drag rewrites it in silence. So the handle is a mode you ask for.
     *
     * It never survives leaving the page — switching tabs is as good as saying
     * you are done, and coming back to a page still armed from last time is how
     * the accident happens anyway. Each drop has already been written by then:
     * TrackTable commits through `albumTracksVM.moveRows`, which persists on the
     * spot, so there is no unsaved work for this to strand.
     */
    property bool reorderMode: false
    onAlbumIdChanged: reorderMode = false

    /* Who is credited on this album, for the chips in the hero.
     *
     * Refilled rather than bound: the backend answers this with a slot, because
     * it is a join over every track on the record rather than a value sitting on
     * an album row — there is nothing to bind to.
     */
    property var contributors: []

    function refreshContributors() {
        contributors = albumVM.hasAlbum ? albumVM.contributingArtists() : []
    }

    onActiveChanged: {
        if (active)
            refreshContributors()
        else
            reorderMode = false
    }

    Connections {
        target: albumTracksVM
        /* Both things that can change the answer, on one signal.
         *
         * Tracks arriving or leaving changes a count, and the model emits this
         * on load and on every bus event that moves its membership. A drag
         * changes no count at all — but it changes the running order, which is
         * the tie-break between two artists on the same number of tracks, and
         * the model emits this again once the new order is written down.
         *
         * Not `rowsMoved`, which is Qt's own and goes out per move while the
         * database still holds the order the drag started from.
         */
        function onAlbum_changed() { root.refreshContributors() }
    }

    Connections {
        target: albumVM
        function onError_occurred(message) { toast.show(message, true) }
        function onRefreshed(id) { toast.show("Updated from " + albumVM.refreshLabel, false) }
        function onUnify_completed(count) {
            toast.show(Fmt.count(count, "track") + " now share one cover", false)
        }
        function onSplit_completed(id) {
            toast.show("Split into a second album of the same name", false)
        }
        // Named rather than counted: "2 tracks moved to the end" leaves you
        // scrolling to work out which two, and the signal carries the titles
        // precisely so it does not have to.
        function onReorder_completed(matched, moved, unmatched) {
            var source = albumVM.refreshLabel
            var msg = moved ? "Reordered to the " + source + " track list"
                            : "Already in the " + source + " order"
            if (unmatched.length > 0) {
                var shown = unmatched.slice(0, 2).join(", ")
                if (unmatched.length > 2)
                    shown += " and " + (unmatched.length - 2) + " more"
                msg += "  ·  " + shown + " not on it, moved to the end"
            }
            toast.show(msg, false)
        }
    }

    // ------------------------------------------------------------------ hero

    // Pinned, and the same band the playlist and artist pages wear. The sleeve
    // is square where the artist's portrait is a circle — that is the entire
    // visual difference between a person and a record.
    DetailHero {
        id: hero
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right

        // The one place the reorder mode is visible with the menu shut. Hovering
        // a row shows a grip and says the same thing, but only once you are
        // already reaching for it.
        kicker: root.reorderMode ? "ALBUM · REORDERING" : "ALBUM"
        title: albumVM.hasAlbum ? albumVM.name : "Album"
        coverHash: albumVM.coverHash
        artRadius: Theme.radiusLg
        trackCount: albumTracksVM.trackCount
        totalDuration: albumTracksVM.totalDuration
        complete: albumVM.isComplete
        identifiedLabel: albumVM.identifiedLabel
        identifiedIcon: albumVM.identifiedIcon
        canPlay: albumTracksVM.trackCount > 0
        canShuffle: albumTracksVM.trackCount > 1
        showExport: true
        canExport: albumTracksVM.trackCount > 0
        // Both repairs live in the menu now, which is a place you have to think
        // to open — so the pip is what tells you there is something in there.
        menuAlert: albumVM.needsAttention
        menuAlertTooltip: "This album's tracks and cover do not all agree"
        contributors: root.contributors

        onBackRequested: root.done()
        onContributorClicked: function (id) { root.openArtistRequested(id) }
        onPlayRequested: playerVM.replace_queue_and_play(albumTracksVM.trackIds())
        onShuffleRequested: playerVM.replace_queue_and_play(
                                hero.shuffled(albumTracksVM.trackIds()))
        onExportRequested: exportDialog.openForTracks(albumTracksVM.trackIds(), albumVM.name)
        onMenuRequested: albumMenu.popup()
    }

    // Everything an album page can do to an album that is not playing it.
    //
    // Three groups, and the separators are the argument: editing the record,
    // repairing it, and completing it from the release it was matched to. No
    // rename — the name is a field in the editor with a Save behind it — and no
    // delete, which the backend does not offer and an album does not need: it
    // stops existing when its last track does.
    NocturneMenu {
        id: albumMenu
        objectName: "albumMenu"

        MenuRow {
            objectName: "albumEdit"
            text: "Edit album…"
            iconName: "edit"
            visible: albumVM.hasAlbum
            height: visible ? implicitHeight : 0
            onTriggered: root.editAlbumRequested(root.albumId)
        }

        MenuSeparator {
            visible: albumVM.needsAttention
            height: visible ? implicitHeight : 0
            padding: 0; topPadding: 4; bottomPadding: 4
            contentItem: Rectangle { implicitHeight: 1; color: Theme.line }
        }

        // Two rows rather than one, because the two answers are genuinely
        // different questions about the world: Unify says "this is one record
        // and the files disagree", Split says "these are two records filed as
        // one". Nothing can work out which from the data, so neither is the
        // default.
        MenuRow {
            objectName: "albumUnify"
            text: "Unify artwork…"
            iconName: "disc"
            visible: albumVM.needsAttention
            height: visible ? implicitHeight : 0
            enabled: !albumVM.isBusy && albumVM.trackCount > 0
            onTriggered: unifyDialog.openForAlbum()
        }
        MenuRow {
            objectName: "albumSplit"
            text: "Split album…"
            iconName: "target"
            danger: true
            visible: albumVM.needsAttention
            height: visible ? implicitHeight : 0
            enabled: !albumVM.isBusy && albumVM.trackCount > 1
            onTriggered: splitDialog.openForAlbum()
        }

        MenuSeparator {
            visible: albumVM.canFindTracks || albumVM.canReorder
            height: visible ? implicitHeight : 0
            padding: 0; topPadding: 4; bottomPadding: 4
            contentItem: Rectangle { implicitHeight: 1; color: Theme.line }
        }

        // What the album's id is worth beyond a name and a sleeve: what the
        // release contains, and what order it goes in. Both need a plugin that
        // can list a release's tracks, and are not drawn without one.
        MenuRow {
            objectName: "albumFindTracks"
            text: "Find tracks"
            // `search`, not the `download` this used to carry, because Export
            // is in this same menu and two download glyphs in one list is a
            // question about which one you meant.
            iconName: "search"
            visible: albumVM.canFindTracks
            height: visible ? implicitHeight : 0
            enabled: !albumVM.isBusy
            // The page goes up first, the way the pairing flow does it: the
            // import tab is where the answer lands, so it should already be in
            // front of you when it gets there.
            onTriggered: {
                importVM.findAlbumTracks(albumVM.albumId)
                root.importPageRequested()
            }
        }
        // Named for where the order comes from, not for what it does — the row
        // below is the other kind of reordering, and "Reorder" on its own would
        // no longer say which.
        MenuRow {
            objectName: "albumReorder"
            text: "Reorder from " + albumVM.refreshLabel
            iconName: "sort"
            visible: albumVM.canReorder
            height: visible ? implicitHeight : 0
            enabled: !albumVM.isBusy && albumTracksVM.trackCount > 1
            onTriggered: albumVM.reorderFromSource()
        }

        // Its own separator rather than sharing the one below: the group above
        // is gated on an id and the group under this one is not, so a
        // shared rule would vanish on an album that was never matched.
        MenuSeparator {
            padding: 0; topPadding: 4; bottomPadding: 4
            contentItem: Rectangle { implicitHeight: 1; color: Theme.line }
        }

        // How the page is laid out, rather than what is on it — the two rows
        // that change the album view without changing the album.
        MenuRow {
            objectName: "albumArtworkToggle"
            text: Theme.prefs.albumArtwork ? "Hide artwork" : "Show artwork"
            // `rows`, not the `disc` this obviously wants: "Unify artwork…" is
            // three rows up wearing `disc` already, and on an album that needs
            // attention both are on screen at once. This one is about the shape
            // of the list, which is what `rows` says.
            iconName: "rows"
            onTriggered: Theme.prefs.albumArtwork = !Theme.prefs.albumArtwork
        }
        MenuRow {
            objectName: "albumReorderMode"
            text: root.reorderMode ? "Done reordering" : "Reorder"
            iconName: root.reorderMode ? "check" : "grip"
            // Nothing to arrange on a single track, and nothing to arrange on an
            // empty album either — the same floor the other reorder uses.
            enabled: albumTracksVM.trackCount > 1
            onTriggered: root.reorderMode = !root.reorderMode
        }

        MenuSeparator {
            padding: 0; topPadding: 4; bottomPadding: 4
            contentItem: Rectangle { implicitHeight: 1; color: Theme.line }
        }

        MenuRow {
            objectName: "albumExport"
            text: "Export…"
            iconName: "download"
            enabled: albumTracksVM.trackCount > 0
            onTriggered: exportDialog.openForTracks(albumTracksVM.trackIds(), albumVM.name)
        }
    }

    // --------------------------------------------------------------- tracks

    TrackTable {
        id: table
        // Named for the tests. Six tables live in the one window and only this
        // one carries `showArt`/`reorderable` off the page's own state, so the
        // artwork and reorder rules have to be read off this table specifically.
        objectName: "albumTable"
        anchors.top: hero.bottom
        anchors.topMargin: Theme.gapSm
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        sideMargin: Theme.gapXl

        mode: "album"
        listVM: albumTracksVM
        active: root.active
        // Not `columnLayouts.library`, though these are the library's columns:
        // hiding the artwork widens this page's column budget, and a budget is
        // held by the layout object rather than by the table. See ColumnLayouts.
        columnLayout: root.columnLayouts ? root.columnLayouts.album : null

        // The three lines the page's whole track-list behaviour comes down to.
        // `showIndex` puts the number column in; `showArt` decides whether the
        // rows repeat the sleeve, and when they do not, that number slot grows
        // the play button the artwork was carrying; `reorderable` turns the
        // number into a grip on hover, which here is a mode rather than the
        // permanent state it used to be. TrackTable drives the drag itself and
        // commits it by calling moveRows/removeAtRows on the model above.
        showIndex: true
        showArt: Theme.prefs.albumArtwork
        reorderable: root.reorderMode

        emptyText: albumVM.hasAlbum
                   ? "Nothing on this album is in the library"
                   : "No album is open"
        onGenerateBackingRequested: function (ids) { root.generateBackingRequested(ids) }
        onEditTrackRequested: function (id) { root.editTrackRequested(id) }
        onCopied: toast.show("Copied to clipboard", false)
    }

    AlbumUnifyDialog { id: unifyDialog }
    AlbumSplitDialog { id: splitDialog }

    ExportDialog {
        id: exportDialog
        onNotify: function (message, isError) { toast.show(message, isError) }
    }

    Toast { id: toast }
}
