import QtQuick
import QtQuick.Controls
import Nocturne
import "../../components/controls"
import "../../components/dialogs"
import "../../components/library"
import "../../components/shell"
import "../../components/tracks"

// The Playlists tab: the collection, and a playlist opened inside it with its
// own hero. Which one is up is the window's call, as it is for every tab, so a
// playlist is a history entry like an artist or an album. This page only asks.
Item {
    id: root

    property bool active: false
    property var columnLayouts: null

    // Forwarded up to the window, which owns navigation.
    signal generateBackingRequested(var trackIds)
    signal editTrackRequested(int trackId)
    signal openPlaylistRequested(int playlistId)
    // The back arrow: up from the playlist to the collection.
    signal done()

    // The open playlist, or -1 for the collection. Set through open() and
    // back(), which the window calls from its navigate().
    property int openId: -1
    // Which way the window's current navigation goes — see StackSlide.
    property int stackDirection: 0

    function open(playlistId) {
        // The window has usually just loaded it, to check it still exists.
        if (playlistTracksVM.playlistId !== playlistId)
            playlistTracksVM.load(playlistId)
        openId = playlistId
    }
    function back() {
        openId = -1
        // Leaving the detail page is leaving its track list, so its selection
        // stops counting — the grid has nothing selected, which puts the
        // playing track back in the details panel. Same rule as switching
        // views; see main.qml's go().
        if (root.active)
            sidebarVM.setSelection([])
    }

    Connections {
        target: playlistsVM
        // Opened when it was made here. One made from Add to playlist,
        // somewhere else, must not change what this tab is showing behind it.
        function onPlaylist_created(playlistId) {
            if (root.active)
                root.openPlaylistRequested(playlistId)
        }
    }

    // ================================================================== grid

    Item {
        id: gridPage
        anchors.fill: parent
        visible: opacity > 0
        opacity: root.openId < 0 ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: Theme.prefs.reducedMotion ? 0 : Theme.normal } }
        transform: StackSlide { current: root.openId < 0; direction: root.stackDirection }

        LibraryHeader {
            id: gridHeader
            objectName: "playlistHeader"
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            height: implicitHeight
            title: "Playlists"
            summary: Fmt.count(grid.count, "playlist")
            actionPrefix: "playlist"
            collectionName: "Playlist"
            optionsObjectName: "playlistViewOptionsButton"
            primaryActionText: "New playlist"
            showSort: true
            onPrimaryActionRequested: createDialog.openBlank()
            onSortRequested: grid.openSort(sortAnchor)
            onOptionsRequested: grid.openOptions(optionsAnchor)
        }
        LibraryEntityView {
            id: grid
            anchors.top: gridHeader.bottom
            anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
            viewKey: "playlist"
            model: playlistsVM
            active: root.active && root.openId < 0
            columnLayout: root.columnLayouts ? root.columnLayouts.playlistCollection : null
            allowCreate: true
            onCreateRequested: createDialog.openBlank()
            contextMenu: cellMenu
            onContextRequested: function(entity) {
                root._armMenu(entity.id, entity.name, entity.trackCount)
            }
            delegate: PlaylistDelegate { onActivated: root.openPlaylistRequested(Number(playlistId)) }
            emptyIcon: "playlist"
            emptyTitle: "No playlists yet"
            emptyDescription: "Group tracks however you like — by mood, by set, by anything."
            emptyAction: AppButton {
                text: "Create your first playlist"; iconName: "plus"; variant: "filled"
                onClicked: createDialog.openBlank()
            }
        }
    }


    // One menu serves both pages. The grid arms it from the cell under the
    // cursor; the detail page arms it from the playlist it has open, so the two
    // entry points cannot drift apart.
    property int _menuId: -1
    property string _menuName: ""
    property int _menuCount: 0

    function _armMenu(id, name, count) {
        _menuId = id
        _menuName = String(name || "")
        _menuCount = Number(count) || 0
    }

    function _openDetailMenu() {
        _armMenu(openId, playlistTracksVM.playlistName, detailTable.count)
        cellMenu.entries = playlistsVM.playbackEntries([openId])
        cellMenu.selectionCount = 1
        cellMenu.inDetail = true
        cellMenu.contentType = "playlist"
        cellMenu.contentId = openId
        cellMenu.popup()
    }

    LibraryCollectionMenu {
        id: cellMenu
        objectName: "playlistCollectionMenu"
        playlist: true
        onOpenRequested: root.openPlaylistRequested(root._menuId)
        onRenameRequested: createDialog.openRename(root._menuId, root._menuName)
        onExportRequested: exportDialog.openFor(root._menuId, root._menuName, root._menuCount)
        onDeleteRequested: confirmDeletePlaylist.open()
    }

    // ================================================================ detail

    Item {
        id: detailPage
        anchors.fill: parent
        visible: opacity > 0
        opacity: root.openId >= 0 ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: Theme.prefs.reducedMotion ? 0 : Theme.normal } }
        transform: StackSlide { current: root.openId >= 0; direction: root.stackDirection }

        // The same banner the artist and album pages wear — see DetailHero.
        DetailHero {
            id: hero
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right

            kicker: "PLAYLIST"
            title: playlistTracksVM.playlistName
            coverHash: playlistTracksVM.coverHash
            trackCount: detailTable.count
            totalDuration: playlistTracksVM.totalDuration
            canPlay: detailTable.count > 0
            canShuffle: detailTable.count > 1
            showExport: true
            canExport: detailTable.count > 0
            backTooltip: "Back to Playlists"

            onBackRequested: root.done()
            onPlayRequested: playerVM.replaceQueueAndPlayEntries(detailTable.entriesFor(playlistTracksVM.trackIds()))
            onShuffleRequested: playerVM.replaceQueueAndPlayEntries(
                                    detailTable.entriesFor(hero.shuffled(playlistTracksVM.trackIds())))
            onExportRequested: exportDialog.openFor(root.openId,
                                                    playlistTracksVM.playlistName,
                                                    detailTable.count)
            onMenuRequested: root._openDetailMenu()
        }

        TrackTable {
            id: detailTable
            anchors.top: hero.bottom
            anchors.topMargin: Theme.gapSm
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            sideMargin: Theme.gapXl

            mode: "playlist"
            sourceType: "playlist"
            sourceId: root.openId
            listVM: playlistTracksVM
            active: root.active && root.openId >= 0
            columnLayout: root.columnLayouts ? root.columnLayouts.playlist : null
            showIndex: true
            // A playlist is an order somebody chose, so its numbers double as
            // grab handles the same way the queue's do — drag to rearrange,
            // drag right to take a track out.
            reorderable: true
            emptyText: "This playlist is empty — add tracks from Browse or the Queue"
            onGenerateBackingRequested: function (ids) { root.generateBackingRequested(ids) }
            onEditTrackRequested: function (id) { root.editTrackRequested(id) }
            onCopied: toast.show("Copied to clipboard", false)
        }
    }

    // ================================================================ dialogs

    NameDialog {
        id: createDialog
        onSubmitted: function (name, editId) {
            if (editId > 0)
                playlistsVM.renamePlaylist(editId, name)
            else
                playlistsVM.createPlaylist(name)
        }
    }

    ConfirmDialog {
        id: confirmDeletePlaylist
        title: "Delete this playlist?"
        body: "“" + root._menuName + "” will be removed. "
              + "The tracks stay in your library."
        confirmText: "Delete"
        danger: true
        onConfirmed: {
            if (root.openId === root._menuId)
                root.done()
            playlistsVM.deletePlaylist(root._menuId)
        }
    }

    ExportDialog {
        id: exportDialog
        onNotify: function (message, isError) { toast.show(message, isError) }
    }

    Toast { id: toast }
}
