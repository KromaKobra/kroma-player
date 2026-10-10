import QtQuick
import QtQuick.Controls
import Kroma.Library 1.0
import Nocturne
import "../components"

// The collection itself: what is in it, where it lives, and every track in it.
//
// This is the browse-everything page. Search is for finding one thing; this is
// for looking at the whole library, so the overview rides in the track list's
// header and scrolls away as soon as you start reading down the rows.
Item {
    id: root

    property bool active: false
    property var columnLayouts: null

    // Forwarded up to the window, which owns navigation to the hidden pages.
    signal generateBackingRequested(var trackIds)
    signal editTrackRequested(int trackId)
    // "files", "folder" or "csv" — which source the user picked. The import
    // page owns the dialogs and the whole run; these buttons are here because
    // this is where you think of importing, not because the work happens here.
    signal importRequested(string kind)

    // An artist tile was pressed. The window owns navigation to the hidden
    // artist page, exactly as it does for BrowseView's artist rows.
    signal openArtistRequested(int artistId)

    // An album tile was pressed. The window owns navigation to the hidden album
    // page, exactly as it does for the artist one.
    signal openAlbumRequested(int albumId)

    // The backup dialog was put away, however it was opened. The window listens
    // so that a backup taken on the way to an update can hand back to it.
    signal backupClosed()

    // Open the backup dialog from outside the page — the update dialog's "Back
    // up first", Settings → System's. This page's instance rather than a second
    // one: two would each report every finished backup, twice over.
    function openBackup() {
        backupDialog.openDialog()
    }

    // Which face is showing. The page has three: the whole track list, every
    // artist as a wall of portraits, and every album as a wall of sleeves. All
    // three are "the collection, whole" — they answer "what do I have", "who do
    // I have" and "which records do I have", and none of them is a search, so
    // they belong on one page rather than three.
    readonly property bool artistsMode: Theme.prefs.libraryMode === 1
    readonly property bool albumsMode: Theme.prefs.libraryMode === 2
    readonly property bool gridMode: artistsMode || albumsMode

    // Pushed once, not bound: Pill assigns to its own currentIndex on click and
    // a binding into it would be destroyed by the first press. Nothing in the
    // backend can change the mode behind the pill, so one-way is complete —
    // unlike BrowseView's searchMode, which needs a Connections to stay in sync.
    Component.onCompleted: modePill.currentIndex = Theme.prefs.libraryMode

    onActiveChanged: if (active) {
        databaseVM.refresh()
        if (artistsMode)
            artistsVM.refresh()
        if (albumsMode)
            albumsVM.refresh()
    }

    // The one thing this page still asks the import model, so a second run
    // cannot be started on top of one already scanning. Sending the user to a
    // page that would refuse them is worse than a greyed-out button.
    readonly property bool importBusy: importVM.isAnalyzing || importVM.isSearching

    // ---------------------------------------------------------------- tracks

    // The library's own list, declared here rather than borrowed from searchVM:
    // Search owns its results, and the two would otherwise overwrite each other.
    TrackQuery {
        id: libraryTracks
        sortField: Theme.prefs.librarySort
        sortAscending: Theme.prefs.librarySortAsc
    }

    readonly property var sortOptions: [
        { key: "title",       label: "Title" },
        { key: "artist",      label: "Artist" },
        { key: "album",       label: "Album" },
        { key: "stashedDate", label: "Date added" },
        { key: "duration",    label: "Duration" }
    ]

    function sortLabel(key) {
        for (var i = 0; i < sortOptions.length; i++)
            if (sortOptions[i].key === key)
                return sortOptions[i].label
        return sortOptions[0].label
    }

    function shuffled(ids) {
        for (var i = ids.length - 1; i > 0; i--) {
            var j = Math.floor(Math.random() * (i + 1))
            var t = ids[i]; ids[i] = ids[j]; ids[j] = t
        }
        return ids
    }

    // ------------------------------------------------------------------ head

    ViewHeader {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        title: "Library"
        // For tracks: the overview cards say this too, but they scroll away with
        // the rest of it, and this is what is left once you are down among the
        // rows. For artists: the vault size is a *file* fact and means nothing on
        // a page of portraits, so the count of names that look like more than one
        // artist takes its place — the only thing about the artist list as a
        // whole that anyone can act on.
        subtitle: root.artistsMode
                  ? Fmt.count(artistsVM.count, "artist")
                    + (artistsVM.needsAttentionCount > 0
                       ? " · " + artistsVM.needsAttentionCount + " need a look" : "")
                  : root.albumsMode
                  // For albums the actionable whole-list fact is how many have
                  // tracks that disagree about their artwork — the same shape of
                  // answer the artist face gives, and the only thing about the
                  // album list as a whole anyone can act on.
                  ? Fmt.count(albumsVM.count, "album")
                    + (albumsVM.needsAttentionCount > 0
                       ? " · " + albumsVM.needsAttentionCount + " need a look" : "")
                  : Fmt.count(libraryTracks.count, "track")
                    + " · " + Fmt.mb(databaseVM.vaultMb)

        // Which face to show. First in the header so it reads leftmost of the
        // actions: it changes what the page *is*, where the others act on it.
        Pill {
            id: modePill
            anchors.verticalCenter: parent.verticalCenter
            options: ["Tracks", "Artists", "Albums"]
            onSelected: function (index) { Theme.prefs.libraryMode = index }
        }

        // The same control the Import header carries; `importRequested` puts
        // that page up first and hands the kind straight on to it.
        ImportButton {
            anchors.verticalCenter: parent.verticalCenter
            enabledAction: !root.importBusy
            onSourceChosen: function (kind) { root.importRequested(kind) }
        }

        AppButton {
            anchors.verticalCenter: parent.verticalCenter
            text: "Backup"
            iconName: "folder"
            variant: "ghost"
            onClicked: backupDialog.openDialog()
        }
    }

    // ------------------------------------------------------------- overview
    //
    // Rides in the track list's header, so it scrolls up out of the way once
    // you are looking at tracks rather than at the library as a whole.

    Component {
        id: overview

        Column {
            id: overviewColumn

            readonly property real contentWidth: width - Theme.gapXl * 2

            width: libraryTable.width
            leftPadding: Theme.gapXl
            rightPadding: Theme.gapXl
            bottomPadding: Theme.gapSm
            spacing: Theme.gapLg

            // What you can do with the library, whole. Everything else it used
            // to say — the counts, the size, where it lives — is either in the
            // page subtitle or, for the vault, in Settings.
            Row {
                spacing: Theme.gapSm

                AppButton {
                    text: "Play whole library"
                    iconName: "play"
                    variant: "filled"
                    enabledAction: libraryTracks.count > 0
                    onClicked: playerVM.replace_queue_and_play(libraryTracks.trackIds())
                }
                AppButton {
                    text: "Shuffle whole library"
                    iconName: "shuffle"
                    variant: "soft"
                    enabledAction: libraryTracks.count > 1
                    onClicked: playerVM.replace_queue_and_play(
                                   root.shuffled(libraryTracks.trackIds()))
                }
            }

            // ------------------------------------------------- list controls
            Item {
                width: overviewColumn.contentWidth
                height: 32

                SectionLabel {
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    text: "ALL TRACKS · " + libraryTracks.count
                          + (libraryTracks.count > 0
                             ? " · " + Fmt.longDuration(libraryTracks.totalDuration)
                             : "")
                }

                Row {
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Theme.gapXs

                    AppButton {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Sort: " + root.sortLabel(Theme.prefs.librarySort)
                        iconName: "sort"
                        variant: "ghost"
                        fontSize: Theme.fzSmall
                        hPadding: 12
                        onClicked: sortMenu.popup()
                    }

                    IconButton {
                        anchors.verticalCenter: parent.verticalCenter
                        icon: "chevronDown"
                        iconSize: 16
                        diameter: 30
                        rotation: Theme.prefs.librarySortAsc ? 180 : 0
                        tooltip: Theme.prefs.librarySortAsc ? "Ascending" : "Descending"
                        onClicked: Theme.prefs.librarySortAsc = !Theme.prefs.librarySortAsc

                        Behavior on rotation {
                            NumberAnimation { duration: Theme.normal
                                              easing.type: Theme.easing }
                        }
                    }
                }
            }
        }
    }

    TrackTable {
        id: libraryTable
        anchors.top: header.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        sideMargin: Theme.gapXl

        mode: "library"
        listVM: libraryTracks
        listHeader: overview
        // Not just about painting: TrackTable claims TrackSelection on becoming
        // active, so a table left active behind the grid would keep Escape,
        // Delete and the info panel bound to a list nobody can see.
        active: root.active && !root.gridMode
        columnLayout: root.columnLayouts ? root.columnLayouts.library : null
        emptyText: "Nothing here yet — import some files to get started"
        onGenerateBackingRequested: function (ids) { root.generateBackingRequested(ids) }
        onEditTrackRequested: function (id) { root.editTrackRequested(id) }
        onCopied: toast.show("Copied to clipboard", false)

        opacity: root.gridMode ? 0 : 1
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.normal } }
    }

    // --------------------------------------------------------------- artists

    // Cross-faded with the track list rather than swapped through a Loader: a
    // Loader would destroy and rebuild the grid on every toggle, losing scroll
    // position and re-creating delegates for a model that is already loaded
    // whole. This page is never unloaded by the window either, so there is
    // nothing for a Loader to save.
    ArtistGrid {
        id: artistGrid
        objectName: "artistGrid"
        anchors.top: header.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom

        onArtistActivated: function (id) { root.openArtistRequested(id) }

        opacity: root.artistsMode ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.normal } }
    }

    // ---------------------------------------------------------------- albums

    // Cross-faded like the artist wall, and for the same reasons.
    AlbumGrid {
        id: albumGrid
        objectName: "albumGrid"
        anchors.top: header.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom

        onAlbumActivated: function (id) { root.openAlbumRequested(id) }

        opacity: root.albumsMode ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.normal } }
    }

    // --------------------------------------------------------------- dialogs

    // Written out rather than repeated over `sortOptions`: a Repeater inside a
    // Menu parents its items to the internal list instead of registering them
    // as entries, so they never appear.
    NocturneMenu {
        id: sortMenu

        MenuRow {
            text: "Title"
            iconName: Theme.prefs.librarySort === "title" ? "check" : ""
            onTriggered: Theme.prefs.librarySort = "title"
        }
        MenuRow {
            text: "Artist"
            iconName: Theme.prefs.librarySort === "artist" ? "check" : ""
            onTriggered: Theme.prefs.librarySort = "artist"
        }
        MenuRow {
            text: "Album"
            iconName: Theme.prefs.librarySort === "album" ? "check" : ""
            onTriggered: Theme.prefs.librarySort = "album"
        }
        MenuRow {
            text: "Date added"
            iconName: Theme.prefs.librarySort === "stashedDate" ? "check" : ""
            onTriggered: Theme.prefs.librarySort = "stashedDate"
        }
        MenuRow {
            text: "Duration"
            iconName: Theme.prefs.librarySort === "duration" ? "check" : ""
            onTriggered: Theme.prefs.librarySort = "duration"
        }
    }

    BackupDialog {
        id: backupDialog
        objectName: "backupDialog"
        onNotify: function (message, isError) { toast.show(message, isError) }
        onClosed: root.backupClosed()
    }

    Toast { id: toast }
}
