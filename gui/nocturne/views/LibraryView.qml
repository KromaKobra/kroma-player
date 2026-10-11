import QtQuick
import QtQuick.Controls
import Kroma.Library 1.0
import Nocturne
import "../components"

// The sidebar selects Songs, Artists or Albums over persistent library views.
Item {
    id: root

    property bool active: false
    property var columnLayouts: null

    // Forwarded up to the window, which owns navigation to the hidden pages.
    signal generateBackingRequested(var trackIds)
    signal editTrackRequested(int trackId)
    // An artist tile was pressed. The window owns navigation to the hidden
    // artist page, exactly as it does for BrowseView's artist rows.
    signal openArtistRequested(int artistId)

    // An album tile was pressed. The window owns navigation to the hidden album
    // page, exactly as it does for the artist one.
    signal openAlbumRequested(int albumId)

    // Which face is showing. The page has three: the whole track list, every
    // artist as a wall of portraits, and every album as a wall of sleeves. All
    // three are "the collection, whole" — they answer "what do I have", "who do
    // I have" and "which records do I have", and none of them is a search, so
    // they belong on one page rather than three.
    readonly property bool artistsMode: Theme.prefs.libraryMode === 1
    readonly property bool albumsMode: Theme.prefs.libraryMode === 2
    readonly property bool gridMode: artistsMode || albumsMode

    // Sidebar destinations can switch library faces while this page is active.
    Connections {
        target: Theme.prefs
        function onLibraryModeChanged() {
            if (root.active && root.artistsMode)
                artistsVM.refresh()
            if (root.active && root.albumsMode)
                albumsVM.refresh()
        }
    }

    onActiveChanged: if (active) {
        if (artistsMode)
            artistsVM.refresh()
        if (albumsMode)
            albumsVM.refresh()
    }

    // ---------------------------------------------------------------- tracks

    // The library's own list, declared here rather than borrowed from searchVM:
    // Search owns its results, and the two would otherwise overwrite each other.
    TrackQuery {
        id: libraryTracks
        objectName: "libraryTracks"
        sortField: Theme.prefs.librarySort
        sortAscending: Theme.prefs.librarySortAsc
    }

    function shuffled(ids) {
        for (var i = ids.length - 1; i > 0; i--) {
            var j = Math.floor(Math.random() * (i + 1))
            var t = ids[i]; ids[i] = ids[j]; ids[j] = t
        }
        return ids
    }

    // ------------------------------------------------------------------ head

    Item {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: root.gridMode ? gridHeader.implicitHeight : songsHeader.implicitHeight

        SongsHeader {
            id: songsHeader
            objectName: "songsHeader"
            anchors.fill: parent
            visible: !root.gridMode
            songCount: libraryTracks.count
            totalDuration: libraryTracks.totalDuration
            onPlayRequested: playerVM.replace_queue_and_play(libraryTracks.trackIds())
            onShuffleRequested: playerVM.replace_queue_and_play(
                                    root.shuffled(libraryTracks.trackIds()))
            onOptionsRequested: sortMenu.popup(optionsAnchor,
                                               optionsAnchor.width - sortMenu.width,
                                               optionsAnchor.height + Theme.gapXs)
        }

        ViewHeader {
            id: gridHeader
            anchors.fill: parent
            visible: root.gridMode
            title: root.artistsMode ? "Artists" : "Albums"
            subtitle: root.artistsMode
                      ? Fmt.count(artistsVM.count, "artist")
                        + (artistsVM.needsAttentionCount > 0
                           ? " · " + artistsVM.needsAttentionCount + " need a look" : "")
                      : Fmt.count(albumsVM.count, "album")
                        + (albumsVM.needsAttentionCount > 0
                           ? " · " + albumsVM.needsAttentionCount + " need a look" : "")
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
        showIndex: true
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

    // Written out rather than generated: a Repeater inside a
    // Menu parents its items to the internal list instead of registering them
    // as entries, so they never appear.
    ButtonGroup { id: sortFields }
    ButtonGroup { id: sortDirections }

    NocturneMenu {
        id: sortMenu
        objectName: "songsSortMenu"
        transformOrigin: Item.TopRight
        enter: Transition {
            ParallelAnimation {
                NumberAnimation { property: "opacity"; from: 0; to: 1
                                  duration: Theme.prefs.reducedMotion ? 0 : Theme.fast }
                NumberAnimation { property: "scale"; from: Theme.prefs.reducedMotion ? 1 : 0.96; to: 1
                                  duration: Theme.prefs.reducedMotion ? 0 : Theme.fast
                                  easing.type: Theme.easing }
            }
        }
        exit: Transition {
            NumberAnimation { property: "opacity"; from: 1; to: 0
                              duration: Theme.prefs.reducedMotion ? 0 : Theme.fast }
        }

        MenuRow { text: "Sort by"; enabled: false }
        MenuSeparator {}

        MenuRow {
            text: "Title"
            objectName: "songsSort_title"
            ButtonGroup.group: sortFields
            checkable: true
            checked: Theme.prefs.librarySort === "title"
            iconName: checked ? "check" : ""
            onTriggered: Theme.prefs.librarySort = "title"
        }
        MenuRow {
            text: "Artist"
            objectName: "songsSort_artist"
            ButtonGroup.group: sortFields
            checkable: true
            checked: Theme.prefs.librarySort === "artist"
            iconName: checked ? "check" : ""
            onTriggered: Theme.prefs.librarySort = "artist"
        }
        MenuRow {
            text: "Album"
            objectName: "songsSort_album"
            ButtonGroup.group: sortFields
            checkable: true
            checked: Theme.prefs.librarySort === "album"
            iconName: checked ? "check" : ""
            onTriggered: Theme.prefs.librarySort = "album"
        }
        MenuRow {
            text: "Date added"
            objectName: "songsSort_stashedDate"
            ButtonGroup.group: sortFields
            checkable: true
            checked: Theme.prefs.librarySort === "stashedDate"
            iconName: checked ? "check" : ""
            onTriggered: Theme.prefs.librarySort = "stashedDate"
        }
        MenuRow {
            text: "Duration"
            objectName: "songsSort_duration"
            ButtonGroup.group: sortFields
            checkable: true
            checked: Theme.prefs.librarySort === "duration"
            iconName: checked ? "check" : ""
            onTriggered: Theme.prefs.librarySort = "duration"
        }
        MenuSeparator {}
        MenuRow {
            objectName: "songsSortAscending"
            ButtonGroup.group: sortDirections
            text: "Ascending"
            checkable: true
            checked: Theme.prefs.librarySortAsc
            iconName: checked ? "check" : ""
            onTriggered: Theme.prefs.librarySortAsc = true
        }
        MenuRow {
            objectName: "songsSortDescending"
            ButtonGroup.group: sortDirections
            text: "Descending"
            checkable: true
            checked: !Theme.prefs.librarySortAsc
            iconName: checked ? "check" : ""
            onTriggered: Theme.prefs.librarySortAsc = false
        }
    }

    Toast { id: toast }
}
