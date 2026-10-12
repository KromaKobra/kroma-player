import QtQuick
import QtQuick.Controls
import Kroma.Library 1.0
import Nocturne
import "../../components/controls"
import "../../components/library"
import "../../components/tracks"
import "../../components/tracks/TrackColumns.js" as Catalog

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
    signal editArtistRequested(int artistId)
    signal editAlbumRequested(int albumId)

    // An album tile was pressed. The window owns navigation to the hidden album
    // page, exactly as it does for the artist one.
    signal openAlbumRequested(int albumId)

    // Sidebar collection identity is independent of each display preference.
    readonly property bool artistsMode: Theme.prefs.libraryMode === 1
    readonly property bool albumsMode: Theme.prefs.libraryMode === 2
    // Collection identity is independent of its display layout.
    readonly property bool collectionMode: artistsMode || albumsMode

    // Sidebar destinations can switch library faces while this page is active.
    Connections {
        target: Theme.prefs
        function onLibraryModeChanged() {
            if (root.active && root.artistsMode && artistCollection)
                artistCollection.refresh()
            if (root.active && root.albumsMode && albumCollection)
                albumCollection.refresh()
        }
    }

    onActiveChanged: if (active) {
        if (artistsMode && artistCollection)
            artistCollection.refresh()
        if (albumsMode && albumCollection)
            albumCollection.refresh()
    }

    // ---------------------------------------------------------------- tracks

    // What the list is in when nobody has chosen a sort: newest first. Kept
    // apart from the chosen sort rather than stored as one, so the headers can
    // tell "sorted by date added" (an arrow) from "not sorted" (none).
    readonly property string defaultSort: "stashedDate"
    readonly property bool sortChosen: Theme.prefs.librarySort !== ""
    readonly property string effectiveSort: sortChosen ? Theme.prefs.librarySort : defaultSort
    readonly property bool effectiveAscending: sortChosen && Theme.prefs.librarySortAsc

    function chooseSort(field, ascending) {
        Theme.prefs.librarySortAsc = ascending
        Theme.prefs.librarySort = field
    }

    // The library's own list, declared here rather than borrowed from searchVM:
    // Search owns its results, and the two would otherwise overwrite each other.
    TrackQuery {
        id: libraryTracks
        objectName: "libraryTracks"
        sortField: root.effectiveSort
        sortAscending: root.effectiveAscending
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
        height: root.collectionMode ? collectionHeader.implicitHeight : songsHeader.implicitHeight

        SongsHeader {
            id: songsHeader
            objectName: "songsHeader"
            anchors.fill: parent
            visible: !root.collectionMode
            songCount: libraryTracks.count
            totalDuration: libraryTracks.totalDuration
            onPlayRequested: playerVM.replace_queue_and_play(libraryTracks.trackIds())
            onShuffleRequested: playerVM.replace_queue_and_play(
                                    root.shuffled(libraryTracks.trackIds()))
            showSort: Theme.prefs.songGrid || !libraryTable.columnLayout
            onSortRequested: sortMenu.popup(sortAnchor,
                                            sortAnchor.width - sortMenu.width,
                                            sortAnchor.height + Theme.gapXs)
            onOptionsRequested: viewOptions.openFor(optionsAnchor)
        }

        LibraryHeader {
            id: collectionHeader
            objectName: "libraryCollectionHeader"
            anchors.fill: parent
            visible: root.collectionMode
            title: root.artistsMode ? "Artists" : "Albums"
            actionPrefix: root.artistsMode ? "artist" : "album"
            collectionName: root.artistsMode ? "Artist" : "Album"
            optionsObjectName: "libraryCollectionOptionsButton"
            showSort: true
            summary: root.artistsMode
                     ? Fmt.count(artistsVM.count, "artist")
                       + (artistsVM.needsAttentionCount > 0
                          ? " · " + artistsVM.needsAttentionCount + " need a look" : "")
                     : Fmt.count(albumsVM.count, "album")
                       + (albumsVM.needsAttentionCount > 0
                          ? " · " + albumsVM.needsAttentionCount + " need a look" : "")
            onSortRequested: root.artistsMode ? artistCollection.openSort(sortAnchor)
                                             : albumCollection.openSort(sortAnchor)
            onOptionsRequested: root.artistsMode ? artistCollection.openOptions(optionsAnchor)
                                                : albumCollection.openOptions(optionsAnchor)
        }
    }

    TrackTable {
        id: libraryTable
        objectName: "libraryTable"
        anchors.top: header.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        sideMargin: Theme.gapXl

        mode: "library"
        activationAction: "edit"
        gridAvailable: true
        gridMode: Theme.prefs.songGrid
        tileSize: Theme.prefs.songTileSize
        rowHeight: Theme.prefs.songCompact ? 40 : Theme.rowHeight
        listVM: libraryTracks
        showIndex: true
        // The catalog is constant, so this is worked out once.
        sortDirections: Catalog.sortDirections(libraryVM.fields)
        sortField: Theme.prefs.librarySort
        sortAscending: Theme.prefs.librarySortAsc
        onSortRequested: (field, ascending) => root.chooseSort(field, ascending)
        // Not just about painting: TrackTable claims TrackSelection on becoming
        // active, so a table left active behind the grid would keep Escape,
        // Delete and the info panel bound to a list nobody can see.
        active: root.active && !root.collectionMode
        columnLayout: root.columnLayouts ? root.columnLayouts.library : null
        emptyText: "Nothing here yet — import some files to get started"
        onGenerateBackingRequested: function (ids) { root.generateBackingRequested(ids) }
        onEditTrackRequested: function (id) { root.editTrackRequested(id) }
        onCopied: toast.show("Copied to clipboard", false)

        opacity: root.collectionMode ? 0 : 1
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.prefs.reducedMotion ? 0 : Theme.normal } }
    }

    // --------------------------------------------------------------- artists

    // Collection pages remain alive across tab navigation to preserve scrolling.
    // Their internal layout loader only changes when the presentation changes.
    LibraryEntityView {
        id: artistCollection
        objectName: "artistCollection"
        anchors.top: header.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom

        active: root.active && root.artistsMode
        columnLayout: root.columnLayouts ? root.columnLayouts.artistCollection : null
        onOpenRequested: function(id, cover) { root.openArtistRequested(id) }
        onEditRequested: function(id) { root.editArtistRequested(id) }
        viewKey: "artist"
        model: artistsVM
        roundArtwork: true
        emptyIcon: "person"
        emptyTitle: "No artists yet"
        emptyDescription: "Import or download some music and everyone who made it turns up here."
        delegate: ArtistDelegate {
            onActivated: root.openArtistRequested(Number(artistId))
        }

        opacity: root.artistsMode ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.prefs.reducedMotion ? 0 : Theme.normal } }
    }

    // ---------------------------------------------------------------- albums

    // Cross-faded like the artist wall, and for the same reasons.
    LibraryEntityView {
        id: albumCollection
        objectName: "albumCollection"
        anchors.top: header.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom

        active: root.active && root.albumsMode
        columnLayout: root.columnLayouts ? root.columnLayouts.albumCollection : null
        onOpenRequested: function(id, cover) { root.openAlbumRequested(id) }
        onEditRequested: function(id) { root.editAlbumRequested(id) }
        viewKey: "album"
        model: albumsVM
        emptyIcon: "disc"
        emptyTitle: "No albums yet"
        emptyDescription: "Import or download some music and the records it came from turn up here."
        delegate: AlbumDelegate {
            onActivated: root.openAlbumRequested(Number(albumId))
        }

        opacity: root.albumsMode ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.prefs.reducedMotion ? 0 : Theme.normal } }
    }

    // --------------------------------------------------------------- dialogs

    ViewOptionsMenu {
        id: viewOptions
        objectName: "songsViewOptionsMenu"
        viewKey: "song"
        viewportWidth: libraryTable.width - 2 * libraryTable.sideMargin
        leadingSeparator: false
    }

    // Songs have sortable column headers in list view; tiles use this menu.
    // Keep explicit menu entries so Qt registers them as actions.
    ButtonGroup { id: sortFields }
    ButtonGroup { id: sortDirections }

    NocturneMenu {
        id: sortMenu
        objectName: "songsSortMenu"
        transformOrigin: Item.TopRight
        MenuRow {
            text: "Sort by"; enabled: false
        }
        MenuSeparator {}

        MenuRow {
            text: "Title"
            objectName: "songsSort_title"
            ButtonGroup.group: sortFields
            checkable: true
            checked: root.effectiveSort === "title"
            iconName: checked ? "check" : ""
            onTriggered: root.chooseSort("title", root.effectiveAscending)
        }
        MenuRow {
            text: "Artist"
            objectName: "songsSort_artist"
            ButtonGroup.group: sortFields
            checkable: true
            checked: root.effectiveSort === "artist"
            iconName: checked ? "check" : ""
            onTriggered: root.chooseSort("artist", root.effectiveAscending)
        }
        MenuRow {
            text: "Album"
            objectName: "songsSort_album"
            ButtonGroup.group: sortFields
            checkable: true
            checked: root.effectiveSort === "album"
            iconName: checked ? "check" : ""
            onTriggered: root.chooseSort("album", root.effectiveAscending)
        }
        MenuRow {
            text: "Date added"
            objectName: "songsSort_stashedDate"
            ButtonGroup.group: sortFields
            checkable: true
            checked: root.effectiveSort === "stashedDate"
            iconName: checked ? "check" : ""
            onTriggered: root.chooseSort("stashedDate", root.effectiveAscending)
        }
        MenuRow {
            text: "Duration"
            objectName: "songsSort_duration"
            ButtonGroup.group: sortFields
            checkable: true
            checked: root.effectiveSort === "duration"
            iconName: checked ? "check" : ""
            onTriggered: root.chooseSort("duration", root.effectiveAscending)
        }
        MenuSeparator {}
        MenuRow {
            objectName: "songsSortAscending"
            ButtonGroup.group: sortDirections
            text: "Ascending"
            checkable: true
            checked: root.effectiveAscending
            iconName: checked ? "check" : ""
            onTriggered: root.chooseSort(root.effectiveSort, true)
        }
        MenuRow {
            objectName: "songsSortDescending"
            ButtonGroup.group: sortDirections
            text: "Descending"
            checkable: true
            checked: !root.effectiveAscending
            iconName: checked ? "check" : ""
            onTriggered: root.chooseSort(root.effectiveSort, false)
        }
    }

    Toast { id: toast }
}
