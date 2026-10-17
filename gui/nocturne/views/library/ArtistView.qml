import QtQuick
import Kroma.Library 1.0
import Nocturne
import "../../components/controls"
import "../../components/library"
import "../../components/tracks"

// One artist, and everything of theirs in the library.
//
// Not a destination in the rail: you arrive here from something that already
// mentioned this artist — a search result, the artist name on a track row, the
// details panel. So the page opens with the artist already loaded and never has
// an empty state worth designing beyond "nothing is open".
//
// The track list is an ordinary TrackQuery filtered on `artistId`, which means
// it inherits paging, sorting, selection and the context menu from the same
// machinery every other list uses. There is no artist-specific list model.
Item {
    id: root

    property bool active: false
    property var columnLayouts: null

    // Forwarded up to the window, which owns navigation.
    signal done()
    signal editArtistRequested(int artistId)
    signal editTrackRequested(int trackId)
    signal generateBackingRequested(var trackIds)

    readonly property int artistId: artistVM.artistId

    // ---------------------------------------------------------------- tracks

    // Declared here rather than borrowed: Search owns its results and the
    // Library page owns its own list, and a third view sharing either would
    // fight it for the sort. `value` is bound, so opening a different artist
    // re-runs the query without this page knowing it happened.
    TrackQuery {
        id: artistTracks
        filters: root.artistId > 0
                 ? [{ field: "artistId", operator: "Equals", value: root.artistId }]
                 : [{ field: "artistId", operator: "Equals", value: -1 }]
        sortField: "date"
        sortAscending: false
    }

    Connections {
        target: artistVM
        function onError_occurred(message) { toast.show(message, true) }
        function onRefreshed(id) { toast.show("Updated from " + artistVM.refreshLabel, false) }
    }

    // ------------------------------------------------------------------ hero

    // Pinned, and the same band the playlist and album pages wear. The portrait
    // is a circle, which is the whole visual difference between a person and a
    // record — one number on a component both pages share.
    DetailHero {
        id: hero
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right

        kicker: "ARTIST"
        title: artistVM.hasArtist ? artistVM.name : "Artist"
        coverHash: artistVM.imageHash
        artRadius: 76
        // A face is not artwork: the disc glyph the placeholder falls back to
        // belongs on a sleeve, so an unpictured artist keeps only the initial.
        artGlyph: false
        trackCount: artistTracks.count
        totalDuration: artistTracks.totalDuration
        identifiedLabel: artistVM.identifiedLabel
        identifiedIcon: artistVM.identifiedIcon
        canPlay: artistTracks.count > 0
        canShuffle: artistTracks.count > 1
        // No export: a playlist and an album are each a set of files somebody
        // would want a copy of, and "everything by this artist" is a search.
        showExport: false
        // A name like "person1/person2" is a mistake the user may not know they
        // have, and this page is where they are most likely to notice it. The
        // fix is in the editor, which is one row down in the menu — so the pip
        // is what points at the menu, and the menu holds the way through.
        menuAlert: artistVM.needsAttention
        menuAlertTooltip: "This name looks like more than one artist"

        backTooltip: "Back to Artists"
        onBackRequested: root.done()
        onPlayRequested: playerVM.replaceQueueAndPlayEntries(table.entriesFor(artistTracks.trackIds()))
        onShuffleRequested: playerVM.replaceQueueAndPlayEntries(
                                table.entriesFor(hero.shuffled(artistTracks.trackIds())))
        onMenuRequested: artistMenu.popup()
    }

    // Editing is the only thing an artist page can do to an artist. Renaming
    // happens inside the editor, where the name is a field with a Save behind
    // it, and there is no delete — an artist stops existing when their last
    // track does, and nothing in the backend offers otherwise.
    NocturneMenu {
        id: artistMenu

        PinMenuRow { contentType: "artist"; contentId: root.artistId }

        MenuRow {
            objectName: "artistEdit"
            text: "Edit artist…"
            iconName: "edit"
            visible: artistVM.hasArtist
            height: visible ? implicitHeight : 0
            onTriggered: root.editArtistRequested(root.artistId)
        }
    }

    // --------------------------------------------------------------- tracks

    TrackTable {
        id: table
        sourceType: "artist"
        sourceId: root.artistId
        anchors.top: hero.bottom
        anchors.topMargin: Theme.gapSm
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        sideMargin: Theme.gapXl

        mode: "library"
        listVM: artistTracks
        active: root.active
        columnLayout: root.columnLayouts ? root.columnLayouts.library : null
        emptyText: artistVM.hasArtist
                   ? "Nothing by this artist is in the library"
                   : "No artist is open"
        onGenerateBackingRequested: function (ids) { root.generateBackingRequested(ids) }
        onEditTrackRequested: function (id) { root.editTrackRequested(id) }
        onCopied: toast.show("Copied to clipboard", false)
    }

    Toast { id: toast }
}
